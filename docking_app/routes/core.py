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
from ..helpers import (
    find_identical_file_by_bytes,
    next_available_ligand_path,
    normalize_flex_residue_list,
    normalize_docking_config,
    normalize_ligand_name_list,
    normalize_ligand_db_filename,
    relative_to_base,
    resolve_dock_directory,
    to_display_path,
)
from ..manifest import (
    build_preview_command,
    materialize_queue_runs,
    normalize_ligand_folder_name,
    persist_root_run_meta,
    resolve_out_root_path,
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
    _filter_pdb_text_by_chain,
    _get_meta,
    _init_selection_map,
    _ligand_table,
    _load_receptor_meta,
    _normalize_chain_id,
    _normalize_receptor_id,
    _parse_grid_file,
    _sanitize_upload_filename,
    _save_uploads,
    _start_run,
    _summarize_receptors,
)
from ..sessions import (
    RUN_SESSION_DIR,
    build_legacy_session_entry,
    load_run_sessions,
    register_run_session,
    save_run_sessions,
    scan_recent_incomplete_rows,
)
from ..pocket_finder import clear_cached_results, clear_runtime_state, get_runtime_state
from ..state import DOCKING_CONFIG_DEFAULTS, RUN_LOCK, RUN_STATE, STATE, save_state_cache

router = APIRouter()
_templates: Jinja2Templates | None = None
LIGAND_DIR_RESOLVED = LIGAND_DIR.resolve()
RECEPTOR_DIR_RESOLVED = RECEPTOR_DIR.resolve()
DOCK_DIR_RESOLVED = DOCK_DIR.resolve()
VALID_RECEPTOR_ID_RE = re.compile(r"^[A-Z0-9]{4}$")

from ..manifest import config_to_manifest_values, append_docking_config_args


# ---------------------------------------------------------------------------
# Ligand filename helpers
# ---------------------------------------------------------------------------

def _next_available_ligand_path(filename: str) -> Path:
    return next_available_ligand_path(LIGAND_DIR, filename)


def _normalize_ligand_db_filename(filename: str) -> str:
    return normalize_ligand_db_filename(filename)


def _sanitize_out_root_name(raw_name: str) -> str:
    normalized = str(raw_name or "").strip().replace("\\", "/")
    basename = Path(normalized).name.strip()
    if basename in {"", ".", ".."}:
        return ""
    return basename


def _cleanup_ligand_dir_names() -> None:
    for path in sorted(LIGAND_DIR.glob("*.sdf"), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        normalized_name = _normalize_ligand_db_filename(path.name)
        if normalized_name == path.name:
            continue
        target = _next_available_ligand_path(normalized_name)
        path.rename(target)


def _normalize_receptor_state() -> None:
    raw_meta = STATE.get("receptor_meta", [])
    raw_selection = STATE.get("selection_map", {})
    normalized_meta: list[dict[str, Any]] = []
    normalized_selection: dict[str, dict[str, str]] = {}
    seen_ids: set[str] = set()

    for item in raw_meta:
        if not isinstance(item, dict):
            continue
        old_id = str(item.get("pdb_id", "")).strip()
        pdb_id = _normalize_receptor_id(old_id)
        if not pdb_id or pdb_id in seen_ids:
            continue
        if pdb_id.startswith("TMP_PROBE"):
            continue
        entry = dict(item)
        entry["pdb_id"] = pdb_id
        normalized_meta.append(entry)
        seen_ids.add(pdb_id)

        source_sel = {}
        if isinstance(raw_selection, dict):
            source_sel = raw_selection.get(old_id) or raw_selection.get(pdb_id) or {}
        if not isinstance(source_sel, dict):
            source_sel = {}
        normalized_selection[pdb_id] = {
            "chain": str(source_sel.get("chain", "all") or "all"),
            "ligand_resname": str(source_sel.get("ligand_resname", "") or ""),
            "ligand_resnames": normalize_ligand_name_list(
                source_sel.get("ligand_resnames")
                or ([source_sel.get("ligand_resname")] if str(source_sel.get("ligand_resname") or "").strip() not in {"", "all_set"} else [])
            ),
            "flex_residues": normalize_flex_residue_list(
                source_sel.get("flex_residues") or source_sel.get("flex_residue_spec") or []
            ),
        }

    for item in normalized_meta:
        normalized_selection.setdefault(
            item["pdb_id"],
            {"chain": "all", "ligand_resname": "", "ligand_resnames": [], "flex_residues": []},
        )

    STATE["receptor_meta"] = normalized_meta
    STATE["selection_map"] = normalized_selection

    if normalized_meta:
        selected = _normalize_receptor_id(STATE.get("selected_receptor", ""))
        if selected not in seen_ids:
            selected = normalized_meta[0]["pdb_id"]
        STATE["selected_receptor"] = selected
        sel_row = normalized_selection.get(selected, {})
        STATE["selected_chain"] = str(sel_row.get("chain", "all") or "all")
        STATE["selected_ligand"] = str(sel_row.get("ligand_resname", "") or "")
    else:
        STATE["selected_receptor"] = ""
        STATE["selected_ligand"] = ""
        STATE["selected_chain"] = "all"


def _cleanup_probe_receptor_files() -> None:
    for path in RECEPTOR_DIR.glob("*.pdb"):
        pdb_id = _normalize_receptor_id(path.stem)
        if not pdb_id.startswith("TMP_PROBE"):
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue


def _parse_requested_receptor_ids(raw_text: str) -> tuple[list[str], list[str]]:
    valid_ids: list[str] = []
    invalid_ids: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[\s,;]+", str(raw_text or "").strip()):
        pdb_id = _normalize_receptor_id(token)
        if not pdb_id or pdb_id in seen:
            continue
        seen.add(pdb_id)
        if not VALID_RECEPTOR_ID_RE.fullmatch(pdb_id):
            invalid_ids.append(pdb_id)
            continue
        valid_ids.append(pdb_id)
    return valid_ids, invalid_ids


def _collect_receptor_rows() -> list[dict[str, Any]]:
    loaded_ids = {_normalize_receptor_id(item.get("pdb_id")) for item in STATE.get("receptor_meta", [])}
    rows_by_id: dict[str, dict[str, Any]] = {}

    for f in _existing_files(RECEPTOR_DIR, (".pdb",)):
        pdb_id = _normalize_receptor_id(f.stem)
        if not pdb_id or pdb_id.startswith("TMP_PROBE") or pdb_id in rows_by_id:
            continue
        rows_by_id[pdb_id] = {
            "name": f.name,
            "pdb_id": pdb_id,
            "loaded": pdb_id in loaded_ids,
            "has_file": True,
        }

    for item in STATE.get("receptor_meta", []):
        pdb_id = _normalize_receptor_id(item.get("pdb_id"))
        if not pdb_id or pdb_id.startswith("TMP_PROBE") or pdb_id in rows_by_id:
            continue
        name = f"{pdb_id}.pdb"
        has_file = False
        pdb_file = str(item.get("pdb_file", "")).strip()
        if pdb_file:
            try:
                path = Path(pdb_file).resolve()
                if path.exists() and path.suffix.lower() == ".pdb":
                    name = path.name
                    has_file = True
            except Exception:
                has_file = False
        rows_by_id[pdb_id] = {
            "name": name,
            "pdb_id": pdb_id,
            "loaded": True,
            "has_file": has_file,
        }

    return [rows_by_id[k] for k in sorted(rows_by_id.keys())]


def _stored_receptor_files_by_id() -> dict[str, Path]:
    rows: dict[str, Path] = {}
    for f in _existing_files(RECEPTOR_DIR, (".pdb",)):
        pdb_id = _normalize_receptor_id(f.stem)
        if not pdb_id or pdb_id.startswith("TMP_PROBE") or pdb_id in rows:
            continue
        rows[pdb_id] = f
    return rows


def _normalize_active_ligands_state() -> list[str]:
    available = {f.name for f in _existing_files(LIGAND_DIR, (".sdf",))}
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in STATE.get("active_ligands", []):
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        if name not in available:
            continue
        cleaned.append(name)
        seen.add(name)
    STATE["active_ligands"] = cleaned
    return cleaned


def _add_receptors_to_active(requested_ids: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    existing_ids = {_normalize_receptor_id(r.get("pdb_id")) for r in STATE["receptor_meta"]}
    to_add = [rid for rid in requested_ids if rid and rid not in existing_ids]
    if not to_add:
        return [], []

    file_map = _stored_receptor_files_by_id()
    file_meta = [file_map[rid] for rid in to_add if rid in file_map]
    fetch_ids = [rid for rid in to_add if rid not in file_map]

    meta = _load_receptor_meta(fetch_ids, file_meta)
    loaded_ids = {_normalize_receptor_id(item.get("pdb_id")) for item in meta}
    failed = [rid for rid in to_add if rid not in loaded_ids]
    if meta:
        STATE["receptor_meta"].extend(meta)
        STATE["selection_map"].update(_init_selection_map(meta))
    _normalize_receptor_state()
    return meta, failed


def configure_templates(templates: Jinja2Templates) -> None:
    global _templates
    _templates = templates


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    if _templates is None:
        raise HTTPException(status_code=500, detail="Templates not configured.")
    return _templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"title": "Docking App"},
    )


@router.get("/api/state")
def api_state() -> JSONResponse:
    _normalize_receptor_state()
    _normalize_active_ligands_state()
    results_root_raw = str(STATE.get("results_root_path") or DOCK_DIR)
    try:
        results_root_display = to_display_path(Path(results_root_raw).expanduser().resolve())
    except Exception:
        results_root_display = "data/dock"
    data = {
        "mode": STATE["mode"],
        "selected_receptor": STATE["selected_receptor"],
        "selected_ligand": STATE["selected_ligand"],
        "selected_chain": STATE["selected_chain"],
        "selection_map": STATE.get("selection_map", {}),
        "active_ligands": STATE.get("active_ligands", []),
        "grid_file_path": STATE["grid_file_path"],
        "queue_count": len(STATE["queue"]),
        "queue": STATE.get("queue", []),
        "runs": STATE["runs"],
        "grid_pad": STATE["grid_pad"],
        "docking_config": normalize_docking_config(STATE.get("docking_config") or {}),
        "out_root": STATE["out_root"],
        "out_root_path": STATE.get("out_root_path", STATE["out_root"]),
        "out_root_name": STATE.get("out_root_name", ""),
        "results_root_path": results_root_display,
        "run_status": RUN_STATE["status"],
        "run_out_root": RUN_STATE.get("out_root", ""),
    }
    return JSONResponse(data)


@router.post("/api/mode")
def api_mode(payload: ModePayload) -> JSONResponse:
    mode = payload.mode if payload.mode in {"Docking", "Multi-Ligand", "Redocking", "Results", "Report"} else "Docking"
    STATE["mode"] = mode
    save_state_cache()
    return JSONResponse({"mode": STATE["mode"]})


@router.post("/api/ligands/upload")
def upload_ligands(files: list[UploadFile] = File(...)) -> JSONResponse:
    saved: list[str] = []
    duplicates: list[str] = []
    for file in files:
        safe_name = _sanitize_upload_filename(file.filename)
        file_bytes = file.file.read()
        existing_path = find_identical_file_by_bytes(
            LIGAND_DIR,
            file_bytes,
            suffixes=(".sdf",),
            preferred_name=safe_name,
        )
        if existing_path is not None:
            duplicates.append(existing_path.name)
            saved.append(str(existing_path))
            continue
        target_path = _next_available_ligand_path(safe_name)
        target_path.write_bytes(file_bytes)
        saved.append(str(target_path))
    _normalize_active_ligands_state()
    save_state_cache()
    lig_files = _existing_files(LIGAND_DIR, (".sdf",))
    return JSONResponse(
        {
            "saved": [Path(p).name for p in saved],
            "duplicates": duplicates,
            "created_count": max(0, len(saved) - len(duplicates)),
            "ligands": [f.name for f in lig_files],
        }
    )


@router.get("/api/ligands/list")
def list_ligands() -> JSONResponse:
    _cleanup_ligand_dir_names()
    _normalize_active_ligands_state()
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
    STATE["active_ligands"] = [lig for lig in STATE.get("active_ligands", []) if lig != name]
    _normalize_active_ligands_state()
    save_state_cache()
    lig_files = _existing_files(LIGAND_DIR, (".sdf",))
    return JSONResponse({"ligands": [f.name for f in lig_files]})


@router.get("/api/ligands/active")
def list_active_ligands() -> JSONResponse:
    active = _normalize_active_ligands_state()
    return JSONResponse({"active_ligands": active})


@router.post("/api/ligands/active/add")
def add_active_ligands(payload: dict[str, Any]) -> JSONResponse:
    names_raw = payload.get("names", [])
    names = names_raw if isinstance(names_raw, list) else []
    _cleanup_ligand_dir_names()
    available = {f.name for f in _existing_files(LIGAND_DIR, (".sdf",))}
    current = _normalize_active_ligands_state()
    seen = set(current)
    ignored: list[str] = []
    for raw in names:
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        if name not in available:
            ignored.append(name)
            continue
        current.append(name)
        seen.add(name)
    STATE["active_ligands"] = current
    save_state_cache()
    return JSONResponse({"active_ligands": current, "ignored": ignored})


@router.post("/api/ligands/active/remove")
def remove_active_ligand(payload: dict[str, Any]) -> JSONResponse:
    name = str(payload.get("name", "")).strip()
    if not name:
        return JSONResponse({"error": "Missing ligand name."}, status_code=400)
    current = _normalize_active_ligands_state()
    STATE["active_ligands"] = [item for item in current if item != name]
    save_state_cache()
    return JSONResponse({"active_ligands": STATE["active_ligands"]})


@router.post("/api/ligands/active/clear")
def clear_active_ligands() -> JSONResponse:
    STATE["active_ligands"] = []
    save_state_cache()
    return JSONResponse({"active_ligands": []})


@router.post("/api/receptors/upload")
def upload_receptors(files: list[UploadFile] = File(...)) -> JSONResponse:
    saved = _save_uploads(files, RECEPTOR_DIR)
    return JSONResponse({"saved": [Path(p).name for p in saved]})


@router.post("/api/receptors/store")
def store_receptors(payload: LoadReceptorsPayload) -> JSONResponse:
    _cleanup_probe_receptor_files()
    _normalize_receptor_state()

    requested_ids, invalid_ids = _parse_requested_receptor_ids(payload.pdb_ids)
    file_map = _stored_receptor_files_by_id()
    already_stored = {rid for rid in requested_ids if rid in file_map}
    fetch_ids = [rid for rid in requested_ids if rid not in file_map]
    fetched_meta = _load_receptor_meta(fetch_ids, [])
    fetched_ids = {_normalize_receptor_id(item.get("pdb_id")) for item in fetched_meta}
    failed_fetch = [rid for rid in fetch_ids if rid not in fetched_ids]

    stored_ids = sorted(already_stored | fetched_ids)
    ignored_ids = sorted(set(invalid_ids + failed_fetch))
    save_state_cache()
    return JSONResponse(
        {
            "receptors": _collect_receptor_rows(),
            "stored_ids": stored_ids,
            "ignored_ids": ignored_ids,
        }
    )


@router.get("/api/receptors/list")
def list_receptors() -> JSONResponse:
    _cleanup_probe_receptor_files()
    _normalize_receptor_state()
    receptors = _collect_receptor_rows()
    return JSONResponse({"receptors": receptors})


@router.post("/api/receptors/delete")
def delete_receptor_file(payload: dict[str, Any]) -> JSONResponse:
    name = str(payload.get("name", "")).strip()
    if not name:
        return JSONResponse({"error": "Missing receptor name."}, status_code=400)
    target = (RECEPTOR_DIR / name).resolve()
    if RECEPTOR_DIR_RESOLVED not in target.parents or target.suffix.lower() != ".pdb":
        return JSONResponse({"error": "Invalid receptor name."}, status_code=400)
    if not target.exists():
        return JSONResponse({"error": "Receptor not found."}, status_code=404)

    target.unlink()

    remaining_meta = []
    for item in STATE.get("receptor_meta", []):
        pdb_file = str(item.get("pdb_file", "")).strip()
        if pdb_file:
            try:
                if Path(pdb_file).resolve() == target:
                    continue
            except Exception:
                continue
        remaining_meta.append(item)
    STATE["receptor_meta"] = remaining_meta
    _normalize_receptor_state()
    save_state_cache()

    receptors = _collect_receptor_rows()
    return JSONResponse({"receptors": receptors})


@router.post("/api/receptors/add")
def add_receptors(payload: LoadReceptorsPayload) -> JSONResponse:
    _cleanup_probe_receptor_files()
    _normalize_receptor_state()
    requested_ids, invalid_ids = _parse_requested_receptor_ids(payload.pdb_ids)
    _meta, failed_ids = _add_receptors_to_active(requested_ids)

    if STATE["receptor_meta"] and not STATE["selected_receptor"]:
        STATE["selected_receptor"] = STATE["receptor_meta"][0]["pdb_id"]
        STATE["selected_ids"] = [STATE["receptor_meta"][0]["pdb_id"]]

    save_state_cache()
    return JSONResponse(
        {
            "summary": _summarize_receptors(STATE["receptor_meta"]),
            "ignored_ids": sorted(set(invalid_ids + failed_ids)),
        }
    )


@router.post("/api/receptors/load")
def load_receptors(payload: LoadReceptorsPayload) -> JSONResponse:
    return add_receptors(payload)


@router.post("/api/receptors/remove")
def remove_receptor(payload: SelectReceptorPayload) -> JSONResponse:
    _normalize_receptor_state()
    pdb_id = _normalize_receptor_id(payload.pdb_id)
    STATE["receptor_meta"] = [
        r for r in STATE["receptor_meta"] if _normalize_receptor_id(r.get("pdb_id")) != pdb_id
    ]
    if pdb_id in STATE["selection_map"]:
        del STATE["selection_map"][pdb_id]

    # If selected receptor was removed, select another one if available
    if STATE["selected_receptor"] == pdb_id:
        STATE["selected_receptor"] = STATE["receptor_meta"][0]["pdb_id"] if STATE["receptor_meta"] else ""
        STATE["selected_ligand"] = ""

    clear_cached_results(pdb_id)
    pocket_state = get_runtime_state()
    if str(pocket_state.get("pdb_id") or "").upper() == pdb_id:
        clear_runtime_state()

    save_state_cache()  # persist removal
    return JSONResponse({"summary": _summarize_receptors(STATE["receptor_meta"])})


@router.get("/api/receptors/summary")
def receptor_summary() -> JSONResponse:
    _normalize_receptor_state()
    return JSONResponse({"summary": _summarize_receptors(STATE["receptor_meta"])})


@router.post("/api/receptors/select")
def receptor_select(payload: SelectReceptorPayload) -> JSONResponse:
    _normalize_receptor_state()
    pdb_id = _normalize_receptor_id(payload.pdb_id)
    STATE["selected_receptor"] = pdb_id
    STATE["selected_ids"] = [pdb_id]
    sel = STATE.get("selection_map", {}).get(pdb_id, {})
    STATE["selected_chain"] = sel.get("chain", "all")
    STATE["selected_ligand"] = sel.get("ligand_resname", "")
    return JSONResponse({"selected_receptor": pdb_id})


@router.get("/api/receptors/{pdb_id}")
def receptor_detail(pdb_id: str, chain: str = "") -> JSONResponse:
    _normalize_receptor_state()
    normalized_id = _normalize_receptor_id(pdb_id)
    meta = _get_meta(normalized_id)
    if not meta:
        return JSONResponse({"error": "not found"}, status_code=404)
    selected_chain = _normalize_chain_id(
        chain or STATE.get("selection_map", {}).get(normalized_id, {}).get("chain", STATE.get("selected_chain", "all"))
    )
    pdb_text = str(meta.get("pdb_text") or "")
    if selected_chain != "all":
        pdb_text = _filter_pdb_text_by_chain(pdb_text, selected_chain)
    grid_data = _parse_grid_file(STATE.get("grid_file_path", ""))
    return JSONResponse(
        {
            "pdb_id": _normalize_receptor_id(meta.get("pdb_id")),
            "pdb_text": pdb_text,
            "chains": meta.get("chains", []),
            "ligands_by_chain": meta.get("ligands_by_chain", {}),
            "pdb_file": meta.get("pdb_file", ""),
            "grid_data": grid_data,
            "selected_chain": selected_chain,
            "selected_ligand": STATE.get("selected_ligand", ""),
        }
    )


@router.get("/api/receptors/{pdb_id}/ligands")
def receptor_ligands(pdb_id: str, chain: str = "") -> JSONResponse:
    _normalize_receptor_state()
    normalized_id = _normalize_receptor_id(pdb_id)
    meta = _get_meta(normalized_id)
    if not meta:
        return JSONResponse({"rows": []})
    selected_chain = _normalize_chain_id(
        chain or STATE.get("selection_map", {}).get(normalized_id, {}).get("chain", STATE.get("selected_chain", "all"))
    )
    return JSONResponse({"rows": _ligand_table(meta, selected_chain)})


@router.post("/api/ligands/select")
def ligand_select(payload: SelectLigandPayload) -> JSONResponse:
    _normalize_receptor_state()
    pdb_id = _normalize_receptor_id(payload.pdb_id)
    if pdb_id not in STATE.get("selection_map", {}):
        STATE["selection_map"][pdb_id] = {}
    existing = STATE["selection_map"].get(pdb_id, {})
    ligand_names = normalize_ligand_name_list(payload.ligands)
    if not ligand_names:
        single_ligand = str(payload.ligand or "").strip()
        if single_ligand and single_ligand != "all_set":
            ligand_names = [single_ligand]
    ligand_label = str(payload.ligand or "").strip()
    if not ligand_label and ligand_names:
        ligand_label = " + ".join(ligand_names)
    STATE["selection_map"][pdb_id] = {
        "chain": payload.chain,
        "ligand_resname": ligand_label,
        "ligand_resnames": ligand_names,
        "flex_residues": normalize_flex_residue_list(existing.get("flex_residues") or existing.get("flex_residue_spec") or []),
    }
    if pdb_id == STATE.get("selected_receptor"):
        STATE["selected_chain"] = payload.chain
        STATE["selected_ligand"] = ligand_label
    save_state_cache()
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
    _normalize_receptor_state()
    if "docking_config" in payload:
        raw_docking_config = payload.get("docking_config")
        STATE["docking_config"] = normalize_docking_config(
            raw_docking_config if isinstance(raw_docking_config, dict) else {}
        )
    else:
        STATE["docking_config"] = normalize_docking_config(STATE.get("docking_config") or {})
    run_count = payload.get("run_count")
    if run_count is not None:
        try:
            STATE["runs"] = int(run_count)
        except (TypeError, ValueError):
            pass
    requested_out_root_path = str(
        payload.get("out_root_path") or STATE.get("out_root_path") or str(DOCK_DIR)
    )
    resolved_out_root_path = resolve_dock_directory(
        requested_out_root_path,
        default=DOCK_DIR_RESOLVED,
        allow_create=True,
    )
    out_root_name = _sanitize_out_root_name(
        str(payload.get("out_root_name") or STATE.get("out_root_name") or "")
    )
    if not out_root_name:
        import datetime
        out_root_name = datetime.datetime.now().strftime("docking_%Y_%m_%d_%H%M%S")
    STATE["out_root_path"] = to_display_path(resolved_out_root_path)
    STATE["out_root_name"] = out_root_name
    STATE["out_root"] = str((resolved_out_root_path / out_root_name).resolve())

    if "selection_map" in payload and isinstance(payload.get("selection_map"), dict):
        incoming = payload.get("selection_map") or {}
        normalized_incoming: dict[str, dict[str, str]] = {}
        for raw_pid, raw_sel in incoming.items():
            pdb_id = _normalize_receptor_id(raw_pid)
            if not pdb_id:
                continue
            sel = raw_sel if isinstance(raw_sel, dict) else {}
            normalized_incoming[pdb_id] = {
                "chain": str(sel.get("chain", "all") or "all"),
                "ligand_resname": str(sel.get("ligand_resname") or sel.get("ligand") or ""),
                "ligand_resnames": normalize_ligand_name_list(
                    sel.get("ligand_resnames")
                    or ([sel.get("ligand_resname") or sel.get("ligand")] if str(sel.get("ligand_resname") or sel.get("ligand") or "").strip() not in {"", "all_set"} else [])
                ),
                "flex_residues": normalize_flex_residue_list(
                    sel.get("flex_residues") or sel.get("flex_residue_spec") or []
                ),
            }
        STATE["selection_map"] = normalized_incoming
        selected = _normalize_receptor_id(STATE.get("selected_receptor", ""))
        if selected and selected in STATE["selection_map"]:
            STATE["selected_chain"] = STATE["selection_map"][selected].get("chain", "all")
            STATE["selected_ligand"] = STATE["selection_map"][selected].get("ligand_resname", "")

    new_jobs = _build_queue(payload)
    update_batch_id = payload.get("update_batch_id")
    replace_queue = bool(payload.get("replace_queue", False))
    if update_batch_id is not None:
        try:
            normalized_batch_id = int(update_batch_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid update_batch_id.")
        for job in new_jobs:
            job["batch_id"] = normalized_batch_id
        existing = list(STATE.get("queue", []))
        next_queue: list[dict[str, Any]] = []
        inserted = False
        for row in existing:
            row_batch_id = row.get("batch_id")
            try:
                row_batch_id = int(row_batch_id)
            except (TypeError, ValueError):
                pass
            if row_batch_id == normalized_batch_id:
                if not inserted:
                    next_queue.extend(new_jobs)
                    inserted = True
                continue
            next_queue.append(row)
        if not inserted:
            next_queue.extend(new_jobs)
        STATE["queue"] = next_queue
    elif replace_queue:
        STATE["queue"] = list(new_jobs)
    else:
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
        elif not sel.get("ligand_resname") and not sel.get("ligand") and not sel.get("ligand_resnames"):
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
        queue_rows = list(STATE["queue"])
        if payload.batch_id is not None:
            queue_rows = [row for row in queue_rows if str(row.get("batch_id")) == str(payload.batch_id)]
            if not queue_rows:
                return JSONResponse({"error": "Selected queue batch was not found."}, status_code=404)

        resolved_out_roots: list[str] = []
        for row in queue_rows:
            resolved_out_roots.append(
                str(resolve_out_root_path(str(row.get("out_root") or STATE.get("out_root") or "")))
            )
        unique_out_roots = sorted({root for root in resolved_out_roots if root})
        if len(unique_out_roots) > 1:
            return JSONResponse(
                {"error": "Selected queue batch has mixed output folders. Rebuild that batch first."},
                status_code=400,
            )
        queue_out_root = unique_out_roots[0] if unique_out_roots else str(resolve_out_root_path(str(STATE.get("out_root") or "")))

        execution_rows = materialize_queue_runs(queue_rows, queue_out_root)
        manifest_path = write_manifest(execution_rows, DOCK_DIR / "manifest.tsv")
        total_runs = len(execution_rows)
        queue_runs = 1
        preview_cmd = build_preview_command(execution_rows, queue_out_root)

        session = register_run_session(
            queue_out_root,
            queue_runs,
            manifest_path,
            planned_total=total_runs,
        )
        persist_root_run_meta(
            out_root=queue_out_root,
            manifest_path=manifest_path,
            mode="fresh",
            planned_total_runs=total_runs,
            queue_count=len(queue_rows),
            runs=queue_runs,
            session_id=str(session.get("id") or ""),
        )
        _start_run(manifest_path, queue_runs, queue_out_root, total_runs, preview_cmd, payload.is_test_mode)
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
                "job_type": str(item.get("job_type") or "Docking"),
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
                "flex_residues": normalize_flex_residue_list(item.get("flex_residues") or item.get("flex_residue_spec") or []),
                "flex_residue_spec": str(item.get("flex_residue_spec") or ""),
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
    try:
        STATE["out_root_path"] = to_display_path(Path(out_root).expanduser().resolve().parent)
    except Exception:
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

        manifest_path = write_manifest(STATE["queue"], DOCK_DIR / "manifest.tsv")

        total_runs = len(STATE["queue"])
        preview_cmd = build_preview_command(STATE["queue"], str(STATE.get("out_root") or ""))

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
    save_state_cache()

    return JSONResponse({
        "queue_count": len(STATE["queue"]),
        "queue": STATE["queue"]
    })


@router.post("/api/run/recent/delete")
def run_recent_delete(payload: dict[str, Any]) -> JSONResponse:
    item_id = str(payload.get("item_id") or "").strip()
    out_root_hint = str(payload.get("out_root") or "").strip()
    purge_files = bool(payload.get("purge_files", False))
    if not item_id and not out_root_hint:
        raise HTTPException(status_code=400, detail="Missing item_id or out_root.")

    target_out_root = ""
    removed_legacy = False
    legacy_entry = build_legacy_session_entry()
    if item_id.startswith("legacy::"):
        target_out_root = item_id.split("legacy::", 1)[1].strip()
    elif out_root_hint:
        try:
            target_out_root = str(Path(out_root_hint).expanduser().resolve())
        except OSError:
            target_out_root = out_root_hint

    sessions = load_run_sessions()
    target = next((row for row in sessions if str(row.get("id") or "") == item_id), None) if item_id else None
    if target and not target_out_root:
        try:
            target_out_root = str(Path(str(target.get("out_root") or "")).expanduser().resolve())
        except OSError:
            target_out_root = str(target.get("out_root") or "").strip()

    if not target_out_root:
        raise HTTPException(status_code=404, detail="Recent docking item not found.")

    target_out_root_path = Path(target_out_root).expanduser()
    try:
        target_out_root_resolved = target_out_root_path.resolve()
    except OSError:
        target_out_root_resolved = target_out_root_path

    with RUN_LOCK:
        active_out_root = str(RUN_STATE.get("out_root") or "").strip()
        if active_out_root:
            try:
                active_out_root = str(Path(active_out_root).expanduser().resolve())
            except OSError:
                pass
        if (
            str(RUN_STATE.get("status") or "") in {"running", "stopping"}
            and active_out_root
            and active_out_root == str(target_out_root_resolved)
        ):
            raise HTTPException(status_code=409, detail="Cannot delete a dock root while it is running.")

    removed_ids = [
        str(row.get("id") or "").strip()
        for row in sessions
        if str(Path(str(row.get("out_root") or "")).expanduser().resolve()) == str(target_out_root_resolved)
    ]
    remaining = [
        row
        for row in sessions
        if str(Path(str(row.get("out_root") or "")).expanduser().resolve()) != str(target_out_root_resolved)
    ]
    save_run_sessions(remaining)

    for session_id in removed_ids:
        if not session_id:
            continue
        session_dir = RUN_SESSION_DIR / session_id
        if session_dir.exists():
            try:
                shutil.rmtree(session_dir, ignore_errors=True)
            except Exception:
                pass

    if legacy_entry:
        legacy_out_root = str(Path(str(legacy_entry.get("out_root") or "")).expanduser().resolve())
        if legacy_out_root == str(target_out_root_resolved):
            for path in [DOCK_DIR / "run_batch.sh", DOCK_DIR / "manifest.tsv"]:
                if path.exists():
                    path.unlink(missing_ok=True)
            removed_legacy = True

    purged_out_root = False
    if purge_files:
        dock_root = DOCK_DIR.resolve()
        if target_out_root_resolved != dock_root and dock_root in target_out_root_resolved.parents:
            shutil.rmtree(target_out_root_resolved, ignore_errors=True)
            purged_out_root = True

    return JSONResponse(
        {
            "ok": True,
            "deleted_id": item_id,
            "deleted_session_ids": removed_ids,
            "deleted_out_root": str(target_out_root_resolved),
            "deleted_legacy_entry": removed_legacy,
            "purged_out_root": purged_out_root,
            "count": len(remaining),
        }
    )


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
