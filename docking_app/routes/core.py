from __future__ import annotations

import os
import re
import signal
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from .. import state as runtime_state
from ..config import BASE, DATA_DIR, DOCK_DIR, LIGAND_DIR, RECEPTOR_DIR
from ..helpers import normalize_docking_config, relative_to_base, to_display_path
from ..manifest import (
    build_preview_command,
    normalize_ligand_folder_name,
    persist_root_run_meta,
    write_manifest,
)
from ..models import (
    LoadReceptorsPayload,
    ModePayload,
    RunStartPayload,
    SelectLigandPayload,
    SelectReceptorPayload,
)
from ..services import (
    _build_queue,
    _existing_files,
    _get_meta,
    _init_selection_map,
    _ligand_table,
    _load_receptor_meta,
    _parse_grid_file,
    _save_uploads,
    _start_run,
    _summarize_receptors,
)
from ..sessions import (
    load_run_sessions,
    register_run_session,
    save_run_sessions,
    scan_recent_incomplete_rows,
)
from ..state import DOCKING_CONFIG_DEFAULTS, RUN_LOCK, RUN_STATE, STATE, save_state_cache

router = APIRouter()
_templates: Jinja2Templates | None = None
LIGAND_DIR_RESOLVED = LIGAND_DIR.resolve()
LIGAND_TIMESTAMP_SUFFIX_RE = re.compile(r"_(\d{8}_\d{6})(?:_\d+)?$", re.IGNORECASE)
RUN_SESSION_DIR = DOCK_DIR / "_run_sessions"

from ..manifest import config_to_manifest_values, append_docking_config_args


# ---------------------------------------------------------------------------
# Ligand filename helpers
# ---------------------------------------------------------------------------

def _next_available_ligand_path(filename: str) -> Path:
    stem = Path(filename).stem
    suffix = Path(filename).suffix or ".sdf"
    candidate = LIGAND_DIR / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate
    idx = 1
    while True:
        path = LIGAND_DIR / f"{stem}_{idx}{suffix}"
        if not path.exists():
            return path
        idx += 1


def _normalize_ligand_db_filename(filename: str) -> str:
    src = Path(str(filename or "").strip())
    suffix = src.suffix.lower() or ".sdf"
    stem = str(src.stem or "ligand").strip()
    stem = LIGAND_TIMESTAMP_SUFFIX_RE.sub("", stem).strip("._-")
    if not stem:
        stem = "ligand"
    return f"{stem}{suffix}"


def _cleanup_ligand_dir_names() -> None:
    for path in sorted(LIGAND_DIR.glob("*.sdf"), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        normalized_name = _normalize_ligand_db_filename(path.name)
        if normalized_name == path.name:
            continue
        target = _next_available_ligand_path(normalized_name)
        path.rename(target)


def configure_templates(templates: Jinja2Templates) -> None:
    global _templates
    _templates = templates


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    if _templates is None:
        raise HTTPException(status_code=500, detail="Templates not configured.")
    return _templates.TemplateResponse(
        "index.html",
        {"request": request, "title": "Docking App"},
    )


@router.get("/api/state")
def api_state() -> JSONResponse:
    data = {
        "mode": STATE["mode"],
        "selected_receptor": STATE["selected_receptor"],
        "selected_ligand": STATE["selected_ligand"],
        "selected_chain": STATE["selected_chain"],
        "grid_file_path": STATE["grid_file_path"],
        "queue_count": len(STATE["queue"]),
        "runs": STATE["runs"],
        "grid_pad": STATE["grid_pad"],
        "docking_config": normalize_docking_config(STATE.get("docking_config") or {}),
        "out_root": STATE["out_root"],
        "out_root_path": STATE.get("out_root_path", STATE["out_root"]),
        "out_root_name": STATE.get("out_root_name", ""),
        "results_root_path": STATE.get("results_root_path", str(DOCK_DIR)),
        "run_status": RUN_STATE["status"],
        "run_out_root": RUN_STATE.get("out_root", ""),
    }
    return JSONResponse(data)


@router.post("/api/mode")
def api_mode(payload: ModePayload) -> JSONResponse:
    mode = payload.mode if payload.mode in {"Docking", "Redocking", "Results", "Report"} else "Docking"
    STATE["mode"] = mode
    return JSONResponse({"mode": STATE["mode"]})


@router.post("/api/ligands/upload")
def upload_ligands(files: list[UploadFile] = File(...)) -> JSONResponse:
    saved = _save_uploads(files, LIGAND_DIR)
    return JSONResponse({"saved": [Path(p).name for p in saved]})


@router.get("/api/ligands/list")
def list_ligands() -> JSONResponse:
    _cleanup_ligand_dir_names()
    lig_files = _existing_files(LIGAND_DIR, (".sdf",))
    return JSONResponse({"ligands": [f.name for f in lig_files]})


@router.post("/api/ligands/delete")
def delete_ligand(payload: dict[str, Any]) -> JSONResponse:
    name = str(payload.get("name", "")).strip()
    if not name:
        return JSONResponse({"error": "Missing ligand name."}, status_code=400)
    target = (LIGAND_DIR / name).resolve()
    if LIGAND_DIR.resolve() not in target.parents or target.suffix.lower() != ".sdf":
        return JSONResponse({"error": "Invalid ligand name."}, status_code=400)
    if not target.exists():
        return JSONResponse({"error": "Ligand not found."}, status_code=404)
    target.unlink()
    lig_files = _existing_files(LIGAND_DIR, (".sdf",))
    return JSONResponse({"ligands": [f.name for f in lig_files]})


@router.post("/api/receptors/upload")
def upload_receptors(files: list[UploadFile] = File(...)) -> JSONResponse:
    saved = _save_uploads(files, RECEPTOR_DIR)
    return JSONResponse({"saved": [Path(p).name for p in saved]})


@router.post("/api/receptors/load")
def load_receptors(payload: LoadReceptorsPayload) -> JSONResponse:
    pdb_ids = [p.strip() for p in payload.pdb_ids.splitlines() if p.strip()]
    # Filter out already loaded PDBs
    existing_ids = {r["pdb_id"] for r in STATE["receptor_meta"]}
    new_ids = [pid for pid in pdb_ids if pid not in existing_ids]
    
    pdb_files = _existing_files(RECEPTOR_DIR, (".pdb",))
    # Filter out already loaded files (by stem/pdb_id)
    new_files = [f for f in pdb_files if f.stem not in existing_ids]

    if not new_ids and not new_files:
         return JSONResponse({"summary": _summarize_receptors(STATE["receptor_meta"])})

    meta = _load_receptor_meta(new_ids, new_files)
    STATE["receptor_meta"].extend(meta)

    # Init selection map for new items
    new_selection = _init_selection_map(meta)
    STATE["selection_map"].update(new_selection)

    if STATE["receptor_meta"] and not STATE["selected_receptor"]:
        STATE["selected_receptor"] = STATE["receptor_meta"][0]["pdb_id"]
        STATE["selected_ids"] = [STATE["receptor_meta"][0]["pdb_id"]]

    save_state_cache()  # persist so receptor survives hot-reload
    return JSONResponse({"summary": _summarize_receptors(STATE["receptor_meta"])})


@router.post("/api/receptors/remove")
def remove_receptor(payload: SelectReceptorPayload) -> JSONResponse:
    pdb_id = payload.pdb_id
    STATE["receptor_meta"] = [r for r in STATE["receptor_meta"] if r["pdb_id"] != pdb_id]
    if pdb_id in STATE["selection_map"]:
        del STATE["selection_map"][pdb_id]

    # If selected receptor was removed, select another one if available
    if STATE["selected_receptor"] == pdb_id:
        STATE["selected_receptor"] = STATE["receptor_meta"][0]["pdb_id"] if STATE["receptor_meta"] else ""
        STATE["selected_ligand"] = ""

    save_state_cache()  # persist removal
    return JSONResponse({"summary": _summarize_receptors(STATE["receptor_meta"])})


@router.get("/api/receptors/summary")
def receptor_summary() -> JSONResponse:
    return JSONResponse({"summary": _summarize_receptors(STATE["receptor_meta"])})


@router.post("/api/receptors/select")
def receptor_select(payload: SelectReceptorPayload) -> JSONResponse:
    STATE["selected_receptor"] = payload.pdb_id
    STATE["selected_ids"] = [payload.pdb_id]
    sel = STATE.get("selection_map", {}).get(payload.pdb_id, {})
    STATE["selected_chain"] = sel.get("chain", "all")
    STATE["selected_ligand"] = sel.get("ligand_resname", "")
    return JSONResponse({"selected_receptor": payload.pdb_id})


@router.get("/api/receptors/{pdb_id}")
def receptor_detail(pdb_id: str) -> JSONResponse:
    meta = _get_meta(pdb_id)
    if not meta:
        return JSONResponse({"error": "not found"}, status_code=404)
    grid_data = _parse_grid_file(STATE.get("grid_file_path", ""))
    return JSONResponse(
        {
            "pdb_id": meta["pdb_id"],
            "pdb_text": meta.get("pdb_text"),
            "chains": meta.get("chains", []),
            "ligands_by_chain": meta.get("ligands_by_chain", {}),
            "pdb_file": meta.get("pdb_file", ""),
            "grid_data": grid_data,
            "selected_chain": STATE.get("selected_chain", "all"),
            "selected_ligand": STATE.get("selected_ligand", ""),
        }
    )


@router.get("/api/receptors/{pdb_id}/ligands")
def receptor_ligands(pdb_id: str) -> JSONResponse:
    meta = _get_meta(pdb_id)
    if not meta:
        return JSONResponse({"rows": []})
    return JSONResponse({"rows": _ligand_table(meta)})


@router.post("/api/ligands/select")
def ligand_select(payload: SelectLigandPayload) -> JSONResponse:
    if payload.pdb_id not in STATE.get("selection_map", {}):
        STATE["selection_map"][payload.pdb_id] = {}
    STATE["selection_map"][payload.pdb_id] = {
        "chain": payload.chain,
        "ligand_resname": payload.ligand,
    }
    if payload.pdb_id == STATE.get("selected_receptor"):
        STATE["selected_chain"] = payload.chain
        STATE["selected_ligand"] = payload.ligand
    return JSONResponse({"ok": True})


@router.post("/api/grid/upload")
def upload_grid(file: UploadFile = File(...)) -> JSONResponse:
    saved = _save_uploads([file], DOCK_DIR)
    grid_path = saved[0] if saved else ""
    STATE["grid_file_path"] = grid_path
    return JSONResponse({"grid_file": grid_path})


@router.get("/api/grid")
def grid_info() -> JSONResponse:
    return JSONResponse({"grid_data": _parse_grid_file(STATE.get("grid_file_path", ""))})


@router.post("/api/queue/build")
def queue_build(payload: dict[str, Any]) -> JSONResponse:
    # We expect payload to be a dict with keys: run_count, padding, selection_map, grid_data
    # We append new jobs to the existing queue
    STATE["docking_config"] = normalize_docking_config(payload.get("docking_config") or STATE.get("docking_config") or {})
    run_count = payload.get("run_count")
    if run_count is not None:
        try:
            STATE["runs"] = int(run_count)
        except (TypeError, ValueError):
            pass
    out_root_path = str(payload.get("out_root_path") or STATE.get("out_root_path") or STATE["out_root"])
    out_root_name = str(payload.get("out_root_name") or STATE.get("out_root_name") or "")
    if not out_root_name:
        import datetime
        out_root_name = datetime.datetime.now().strftime("docking_%Y_%m_%d_%H%M%S")
    STATE["out_root_path"] = out_root_path
    STATE["out_root_name"] = out_root_name
    STATE["out_root"] = str(Path(out_root_path) / out_root_name)

    new_jobs = _build_queue(payload)
    STATE["queue"].extend(new_jobs)
    save_state_cache()  # persist queue + out_root so they survive hot-reload

    # --- Debug info (diagnose empty queue) ---
    sel_map = payload.get("selection_map") or {}
    gd = payload.get("grid_data") or {}
    meta_ids = [m["pdb_id"] for m in STATE.get("receptor_meta", [])]
    skipped = []
    for pid, sel in sel_map.items():
        if pid not in meta_ids:
            skipped.append({"pdb_id": pid, "reason": "not_in_receptor_meta"})
        elif not gd.get(pid):
            skipped.append({"pdb_id": pid, "reason": "no_grid_data"})
        elif not sel.get("ligand_resname") and not sel.get("ligand"):
            skipped.append({"pdb_id": pid, "reason": "no_ligand_selected"})
    debug = {
        "receptors_in_state": meta_ids,
        "selection_map_keys": list(sel_map.keys()),
        "grid_data_keys": list(gd.keys()),
        "new_jobs_added": len(new_jobs),
        "mode": str(payload.get("mode") or STATE.get("mode") or "Docking"),
        "skipped": skipped,
    }
    return JSONResponse({
        "queue_count": len(STATE["queue"]),
        "queue": STATE["queue"],
        "debug": debug,
    })


@router.post("/api/run/start")
def run_start(payload: RunStartPayload = RunStartPayload()) -> JSONResponse:
    with RUN_LOCK:
        if RUN_STATE["status"] in {"running", "stopping"}:
            return JSONResponse({"error": "Run already in progress."}, status_code=409)
        if not STATE["queue"]:
            return JSONResponse({"error": "Queue is empty."}, status_code=400)
        manifest_path = DOCK_DIR / "manifest.tsv"
        with manifest_path.open("w", encoding="utf-8") as handle:
            for row in STATE["queue"]:
                row_cfg = normalize_docking_config(row.get("docking_config") or STATE.get("docking_config") or {})
                ligand_val = (
                    row.get("ligand_resname")
                    or row.get("ligand_name")
                    or row.get("ligand")
                    or ""
                )
                values = [
                    row.get("pdb_id", ""),
                    row.get("chain", ""),
                    ligand_val,
                    row.get("lig_spec", ""),
                    row.get("pdb_file", ""),
                    row.get("grid_pad", ""),
                    row.get("grid_file", ""),
                    row.get("force_run_id", ""),
                    *config_to_manifest_values(row_cfg),
                ]
                values = [
                    "__EMPTY__" if v is None or str(v) == "" else str(v)
                    for v in values
                ]
                handle.write("\t".join(values) + "\n")
        total_runs = sum(int(row.get("run_count") or 1) for row in STATE["queue"]) or 0
        preview_cmd = ""
        if STATE["queue"]:
            first = STATE["queue"][0]
            ligand_val = (
                first.get("ligand_resname")
                or first.get("ligand_name")
                or first.get("ligand")
                or ""
            )
            run_id_arg = "1"
            forced_run_id = first.get("force_run_id")
            if forced_run_id not in (None, "", "__EMPTY__"):
                try:
                    run_id_arg = str(int(forced_run_id))
                except (TypeError, ValueError):
                    run_id_arg = "1"

            args = [
                str(first.get("pdb_id", "")),
                str(first.get("chain", "")),
                str(ligand_val),
                "--run_id",
                run_id_arg,
            ]
            def _nonempty(val: str) -> bool:
                return bool(val) and val != "__EMPTY__"

            lig_spec = str(first.get("lig_spec", ""))
            pdb_file = str(first.get("pdb_file", ""))
            grid_pad = str(first.get("grid_pad", ""))
            grid_file = str(first.get("grid_file", ""))
            out_root = str(STATE.get("out_root", ""))

            if _nonempty(lig_spec):
                args += ["--lig_spec", lig_spec]
            if _nonempty(pdb_file):
                args += ["--pdb_file", pdb_file]
            if _nonempty(grid_pad):
                args += ["--grid_pad", grid_pad]
            if _nonempty(grid_file):
                args += ["--grid_file", grid_file]
            if _nonempty(out_root):
                args += ["--out_root", out_root]
            append_docking_config_args(args, first.get("docking_config") or STATE.get("docking_config") or {})

            preview_cmd = f"{BASE / 'scripts' / 'run1.sh'} " + " ".join(args)

        session = register_run_session(
            STATE["out_root"],
            STATE["runs"],
            manifest_path,
            planned_total=total_runs,
        )
        persist_root_run_meta(
            out_root=STATE["out_root"],
            manifest_path=manifest_path,
            mode="fresh",
            planned_total_runs=total_runs,
            queue_count=len(STATE["queue"]),
            runs=STATE["runs"],
            session_id=str(session.get("id") or ""),
        )
        _start_run(manifest_path, STATE["runs"], STATE["out_root"], total_runs, preview_cmd, payload.is_test_mode)
    return JSONResponse(
        {
            "status": RUN_STATE["status"],
            "command": RUN_STATE.get("command", ""),
            "out_root": RUN_STATE.get("out_root", ""),
        }
    )


@router.get("/api/run/recent")
def run_recent(limit: int = 3) -> JSONResponse:
    normalized_limit = max(1, min(3, int(limit or 3)))
    rows = scan_recent_incomplete_rows(limit=normalized_limit, include_jobs=False)
    return JSONResponse({"count": len(rows), "rows": rows})


def _prepare_resume_queue(item_id: str, replace_queue: bool = True) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    recent_rows = scan_recent_incomplete_rows(limit=500, include_jobs=True)
    selected = next((row for row in recent_rows if str(row.get("id")) == item_id), None)
    if not selected:
        raise HTTPException(status_code=404, detail="Recent docking item not found.")
    if not selected.get("resumable"):
        raise HTTPException(status_code=400, detail=selected.get("resume_reason") or "Item is not resumable.")

    pending_rows = list(selected.get("pending_queue_rows") or [])
    if not pending_rows:
        raise HTTPException(status_code=400, detail="No pending queue rows found for selected dock root.")

    batch_id = int(time.time() * 1000)
    queue_rows: list[dict[str, Any]] = []
    skipped_rows = 0
    for item in pending_rows:
        if not item.get("resumable"):
            skipped_rows += 1
            continue
        queue_rows.append(
            {
                "batch_id": batch_id,
                "job_type": "Docking",
                "pdb_id": str(item.get("pdb_id") or ""),
                "chain": str(item.get("chain") or ""),
                "ligand_name": str(item.get("ligand_name") or ""),
                "ligand_resname": str(item.get("ligand_resname") or ""),
                "lig_spec": str(item.get("lig_spec") or ""),
                "pdb_file": str(item.get("pdb_file") or ""),
                "grid_params": None,
                "grid_pad": str(item.get("grid_pad") or ""),
                "grid_file": str(item.get("grid_file") or ""),
                "padding": str(item.get("padding") or ""),
                "run_count": 1,
                "force_run_id": int(item.get("force_run_id") or 1),
                "docking_config": normalize_docking_config(item.get("docking_config") or STATE.get("docking_config") or {}),
            }
        )
    if not queue_rows:
        raise HTTPException(status_code=400, detail="No resumable pending runs found.")

    queue_rows.sort(
        key=lambda row: (
            str(row.get("pdb_id") or ""),
            str(row.get("ligand_name") or ""),
            int(row.get("force_run_id") or 0),
        )
    )

    if replace_queue:
        STATE["queue"] = queue_rows
    else:
        STATE["queue"].extend(queue_rows)

    dock_root = str(selected.get("dock_root") or "").strip()
    out_root = str(selected.get("resume_out_root") or "").strip() or str((DOCK_DIR / dock_root).resolve())
    STATE["runs"] = 1
    STATE["out_root"] = out_root
    STATE["out_root_path"] = str(Path(out_root).parent)
    STATE["out_root_name"] = Path(out_root).name
    if queue_rows:
        STATE["docking_config"] = normalize_docking_config(queue_rows[0].get("docking_config") or STATE.get("docking_config") or {})

    meta = {
        "dock_root": dock_root,
        "skipped_rows": skipped_rows,
        "selected": selected,
    }
    return queue_rows, meta


@router.post("/api/run/recent/prepare")
def run_recent_prepare(payload: dict[str, Any]) -> JSONResponse:
    item_id = str(payload.get("item_id") or "").strip()
    if not item_id:
        raise HTTPException(status_code=400, detail="Missing item_id.")
    replace_queue = bool(payload.get("replace_queue", True))

    with RUN_LOCK:
        if RUN_STATE["status"] in {"running", "stopping"}:
            raise HTTPException(status_code=409, detail="Run already in progress.")

        queue_rows, meta = _prepare_resume_queue(item_id=item_id, replace_queue=replace_queue)
        dock_root = str(meta.get("dock_root") or "")
        skipped_rows = int(meta.get("skipped_rows") or 0)

        return JSONResponse(
            {
                "ok": True,
                "prepared_count": len(queue_rows),
                "queue_count": len(STATE["queue"]),
                "queue": STATE["queue"],
                "out_root": STATE["out_root"],
                "out_root_path": STATE["out_root_path"],
                "out_root_name": STATE["out_root_name"],
                "message": (
                    f"Resume queue prepared for {dock_root}: {len(queue_rows)} run(s)."
                    + (f" Skipped {skipped_rows} non-resumable row(s)." if skipped_rows else "")
                ),
            }
        )


@router.post("/api/run/recent/continue")
def run_recent_continue(payload: dict[str, Any]) -> JSONResponse:
    item_id = str(payload.get("item_id") or "").strip()
    if not item_id:
        raise HTTPException(status_code=400, detail="Missing item_id.")
    replace_queue = bool(payload.get("replace_queue", True))
    is_test_mode = bool(payload.get("is_test_mode", False))

    with RUN_LOCK:
        if RUN_STATE["status"] in {"running", "stopping"}:
            raise HTTPException(status_code=409, detail="Run already in progress.")

        queue_rows, meta = _prepare_resume_queue(item_id=item_id, replace_queue=replace_queue)
        if not queue_rows:
            raise HTTPException(status_code=400, detail="No resume queue rows prepared.")

        manifest_path = DOCK_DIR / "manifest.tsv"
        with manifest_path.open("w", encoding="utf-8") as handle:
            for row in STATE["queue"]:
                row_cfg = normalize_docking_config(row.get("docking_config") or STATE.get("docking_config") or {})
                ligand_val = (
                    row.get("ligand_resname")
                    or row.get("ligand_name")
                    or row.get("ligand")
                    or ""
                )
                values = [
                    row.get("pdb_id", ""),
                    row.get("chain", ""),
                    ligand_val,
                    row.get("lig_spec", ""),
                    row.get("pdb_file", ""),
                    row.get("grid_pad", ""),
                    row.get("grid_file", ""),
                    row.get("force_run_id", ""),
                    *config_to_manifest_values(row_cfg),
                ]
                values = ["__EMPTY__" if v is None or str(v) == "" else str(v) for v in values]
                handle.write("\t".join(values) + "\n")

        total_runs = len(STATE["queue"])
        preview_cmd = ""
        if STATE["queue"]:
            first = STATE["queue"][0]
            ligand_val = (
                first.get("ligand_resname")
                or first.get("ligand_name")
                or first.get("ligand")
                or ""
            )
            run_id_arg = str(int(first.get("force_run_id") or 1))
            args = [
                str(first.get("pdb_id", "")),
                str(first.get("chain", "")),
                str(ligand_val),
                "--run_id",
                run_id_arg,
            ]
            def _nonempty(val: str) -> bool:
                return bool(val) and val != "__EMPTY__"

            lig_spec = str(first.get("lig_spec", ""))
            pdb_file = str(first.get("pdb_file", ""))
            grid_pad = str(first.get("grid_pad", ""))
            grid_file = str(first.get("grid_file", ""))
            out_root = str(STATE.get("out_root", ""))

            if _nonempty(lig_spec):
                args += ["--lig_spec", lig_spec]
            if _nonempty(pdb_file):
                args += ["--pdb_file", pdb_file]
            if _nonempty(grid_pad):
                args += ["--grid_pad", grid_pad]
            if _nonempty(grid_file):
                args += ["--grid_file", grid_file]
            if _nonempty(out_root):
                args += ["--out_root", out_root]
            append_docking_config_args(args, first.get("docking_config") or STATE.get("docking_config") or {})
            preview_cmd = f"{BASE / 'scripts' / 'run1.sh'} " + " ".join(args)

        selected = meta.get("selected") if isinstance(meta, dict) else {}
        source_session_id = str((selected or {}).get("id") or "")
        planned_total_runs = int((selected or {}).get("expected_runs_total") or total_runs)
        persist_root_run_meta(
            out_root=STATE["out_root"],
            manifest_path=manifest_path,
            mode="resume",
            planned_total_runs=planned_total_runs,
            queue_count=len(STATE["queue"]),
            runs=1,
            session_id=source_session_id,
            source_session_id=source_session_id,
        )
        _start_run(manifest_path, 1, STATE["out_root"], total_runs, preview_cmd, is_test_mode)

        return JSONResponse(
            {
                "status": RUN_STATE["status"],
                "command": RUN_STATE.get("command", ""),
                "out_root": RUN_STATE.get("out_root", ""),
                "prepared_count": len(queue_rows),
                "queue_count": len(STATE["queue"]),
                "message": (
                    f"Continue queue started for {meta.get('dock_root')}: {len(queue_rows)} pending run(s)."
                ),
            }
        )


@router.post("/api/queue/remove_batch")
def remove_batch(payload: dict[str, Any]) -> JSONResponse:
    batch_id = payload.get("batch_id")
    if batch_id is not None:
        STATE["queue"] = [job for job in STATE["queue"] if job.get("batch_id") != batch_id]
        
    return JSONResponse({
        "queue_count": len(STATE["queue"]),
        "queue": STATE["queue"]
    })


@router.post("/api/run/recent/delete")
def run_recent_delete(payload: dict[str, Any]) -> JSONResponse:
    item_id = str(payload.get("item_id") or "").strip()
    if not item_id:
        raise HTTPException(status_code=400, detail="Missing item_id.")
    if item_id.startswith("legacy::"):
        raise HTTPException(status_code=400, detail="Legacy recent entry cannot be deleted from index.")

    sessions = load_run_sessions()
    target = next((row for row in sessions if str(row.get("id") or "") == item_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Recent docking item not found.")

    remaining = [row for row in sessions if str(row.get("id") or "") != item_id]
    save_run_sessions(remaining)

    session_dir = RUN_SESSION_DIR / item_id
    if session_dir.exists():
        try:
            shutil.rmtree(session_dir, ignore_errors=True)
        except Exception:
            pass

    return JSONResponse({"ok": True, "deleted_id": item_id, "count": len(remaining)})


def _stop_active_run_process(timeout_sec: float = 8.0) -> tuple[bool, str, int | None]:
    proc = runtime_state.RUN_PROC
    if proc is None:
        return False, "No active run process found.", None

    def _descendants(root_pid: int) -> list[int]:
        collected: list[int] = []
        stack: list[int] = [root_pid]
        seen: set[int] = {root_pid}
        while stack:
            cur = stack.pop()
            try:
                out = subprocess.check_output(
                    ["pgrep", "-P", str(cur)],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                continue
            for token in out.split():
                try:
                    child_pid = int(token)
                except (TypeError, ValueError):
                    continue
                if child_pid in seen:
                    continue
                seen.add(child_pid)
                collected.append(child_pid)
                stack.append(child_pid)
        return collected

    try:
        if proc.poll() is not None:
            runtime_state.RUN_PROC = None
            return True, "Run process already exited.", proc.returncode
    except Exception:
        pass

    descendants = _descendants(proc.pid)

    sent_term = False
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        sent_term = True
    except Exception:
        try:
            proc.terminate()
            sent_term = True
        except Exception:
            sent_term = False
    for child_pid in descendants:
        try:
            os.kill(child_pid, signal.SIGTERM)
        except Exception:
            continue

    deadline = time.time() + max(0.5, float(timeout_sec or 8.0))
    while time.time() < deadline:
        if proc.poll() is not None:
            runtime_state.RUN_PROC = None
            return True, "Run queue stopped.", proc.returncode
        time.sleep(0.2)

    # Escalate to SIGKILL if the process group is still alive.
    killed = False
    descendants = _descendants(proc.pid)
    for child_pid in descendants:
        try:
            os.kill(child_pid, signal.SIGKILL)
        except Exception:
            continue
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        killed = True
    except Exception:
        try:
            proc.kill()
            killed = True
        except Exception:
            killed = False

    if killed:
        for _ in range(20):
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        runtime_state.RUN_PROC = None
        return True, "Run queue was force-stopped.", proc.poll()

    return False, "Failed to stop active run process.", proc.poll()


@router.post("/api/run/stop")
def run_stop() -> JSONResponse:
    with RUN_LOCK:
        status = str(RUN_STATE.get("status") or "idle")
        if status not in {"running", "stopping"}:
            return JSONResponse(
                {
                    "status": status,
                    "returncode": RUN_STATE.get("returncode"),
                    "message": "No running queue to stop.",
                    "out_root": RUN_STATE.get("out_root", ""),
                }
            )
        RUN_STATE["status"] = "stopping"
        RUN_STATE["log_lines"].append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] STOP requested by user.")
        RUN_STATE["log_lines"] = RUN_STATE["log_lines"][-400:]

    stopped, message, code = _stop_active_run_process()

    with RUN_LOCK:
        if stopped:
            RUN_STATE["status"] = "stopped"
            RUN_STATE["returncode"] = code
            RUN_STATE["log_lines"].append(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
            )
        else:
            RUN_STATE["status"] = "error"
            RUN_STATE["returncode"] = code if code is not None else 1
            RUN_STATE["log_lines"].append(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] STOP failed: {message}"
            )
        RUN_STATE["log_lines"] = RUN_STATE["log_lines"][-400:]
        payload = {
            "status": RUN_STATE["status"],
            "returncode": RUN_STATE["returncode"],
            "message": message,
            "out_root": RUN_STATE.get("out_root", ""),
        }
    return JSONResponse(payload)


@router.get("/api/run/status")
def run_status() -> JSONResponse:
    elapsed = 0
    if RUN_STATE.get("start_time"):
        elapsed = max(0, int(time.time() - RUN_STATE["start_time"]))
    return JSONResponse(
        {
            "status": RUN_STATE["status"],
            "returncode": RUN_STATE["returncode"],
            "log": "\n".join(RUN_STATE["log_lines"]),
            "command": RUN_STATE.get("command", ""),
            "out_root": RUN_STATE.get("out_root", ""),
            "batch_log_path": RUN_STATE.get("batch_log_path", ""),
            "total_runs": RUN_STATE.get("total_runs", 0),
            "completed_runs": RUN_STATE.get("completed_runs", 0),
            "elapsed_seconds": elapsed,
        }
    )


