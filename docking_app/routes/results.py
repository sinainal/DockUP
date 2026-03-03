from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from ..config import BASE, DATA_DIR, DOCK_DIR, RECEPTOR_DIR
from ..config import WORKSPACE_DIR
from ..services import _parse_plip_report, _parse_results_folder, _scan_results
from ..state import STATE

router = APIRouter()
BASE_RESOLVED = BASE.resolve()
DATA_DIR_RESOLVED = DATA_DIR.resolve()
DOCK_DIR_RESOLVED = DOCK_DIR.resolve()

@router.post("/api/results/scan")
def scan_results(payload: dict[str, Any]) -> JSONResponse:
    root_path = str(payload.get("root_path") or STATE.get("results_root_path") or DOCK_DIR)
    rp = Path(root_path).expanduser()
    if not rp.is_absolute():
        # Try workspace first (data/dock lives under workspace)
        ws = (WORKSPACE_DIR / rp).resolve()
        if ws.exists() and ws.is_dir():
            rp = ws
        else:
            rp = (BASE / rp).resolve()
    data = _scan_results(str(rp))
    STATE["results_root_path"] = data.get("root_path", str(rp))
    return JSONResponse(data)


@router.post("/api/results/detail")
def results_detail(payload: dict[str, Any]) -> JSONResponse:
    result_dir = str(payload.get("result_dir") or "")
    if not result_dir:
        raise HTTPException(status_code=400, detail="Missing result_dir.")
    root = Path(STATE.get("results_root_path") or DOCK_DIR).expanduser().resolve()
    target = Path(result_dir).expanduser().resolve()
    if root not in target.parents and target != root:
        raise HTTPException(status_code=400, detail="Invalid result_dir.")
    entry = _parse_results_folder(target) or {}
    report_xml = target / "plip" / "report.xml"
    interactions, residues, ligand_info = _parse_plip_report(report_xml)
    if not entry.get("ligand_resname") and ligand_info.get("ligand_resname"):
        entry["ligand_resname"] = ligand_info.get("ligand_resname")
    if not entry.get("ligand_chain") and ligand_info.get("ligand_chain"):
        entry["ligand_chain"] = ligand_info.get("ligand_chain")
    if not entry.get("ligand_resid") and ligand_info.get("ligand_resid"):
        entry["ligand_resid"] = ligand_info.get("ligand_resid")

    native_ligand_path = ""
    ligand_filename = ""
    folder_ligand_name = str(target.parent.name or "").strip()
    for name in (
        f"{entry.get('pdb_id', '')}_ligand_fixed.sdf",
        f"{entry.get('pdb_id', '')}_ligand.sdf",
    ):
        candidate = target / name
        if candidate.exists():
            native_ligand_path = str(candidate)
            # Extract filename without extension as ligand name
            ligand_filename = candidate.stem.replace("_ligand_fixed", "").replace("_ligand", "")
            break
    
    # Also check for docked ligand SDF files to get original ligand name
    for sdf_file in target.glob("*.sdf"):
        if "_ligand" not in sdf_file.name:
            # This might be the original docked ligand file
            ligand_filename = sdf_file.stem
            break

    pdb_key = str(entry.get("pdb_id") or "").strip().lower()
    if ligand_filename and pdb_key and ligand_filename.strip().lower() == pdb_key:
        ligand_filename = ""
    if not ligand_filename:
        ligand_filename = folder_ligand_name

    entry["native_ligand_path"] = native_ligand_path
    current_display = str(entry.get("ligand_display_name") or "").strip()
    entry["ligand_filename"] = ligand_filename or current_display or entry.get("ligand_resname", "UNL")
    
    # Find original receptor PDB (not cleaned) for native ligand visualization
    original_receptor_path = ""
    pdb_id = entry.get("pdb_id", "")
    if pdb_id:
        # Try to find raw PDB in receptor directory or fetch from RCSB
        raw_candidates = [
            RECEPTOR_DIR / f"{pdb_id}.pdb",
            RECEPTOR_DIR / f"{pdb_id.upper()}.pdb",
            RECEPTOR_DIR / f"{pdb_id.lower()}.pdb",
        ]
        for cand in raw_candidates:
            if cand.exists():
                original_receptor_path = str(cand)
                break
    entry["original_receptor_path"] = original_receptor_path
    
    # Preserve folder-derived display name; only fall back when it's missing/unknown.
    if not current_display or current_display in {"UNL", "Native"}:
        if entry.get("ligand_resname") == "UNL" and ligand_filename:
            entry["ligand_display_name"] = ligand_filename
        else:
            entry["ligand_display_name"] = entry.get("ligand_resname", "UNL")
    else:
        entry["ligand_display_name"] = current_display
    
    return JSONResponse({"result": entry, "residues": residues, "interactions": interactions})


@router.get("/api/results/file")
def results_file(path: str) -> FileResponse:
    if not path:
        raise HTTPException(status_code=400, detail="Missing path.")
    target = Path(path).expanduser().resolve()
    
    # Allow serving from results root OR receptor directory
    results_root = Path(STATE.get("results_root_path") or DOCK_DIR).expanduser().resolve()
    receptor_root = RECEPTOR_DIR.resolve()
    
    valid_path = False
    if results_root in target.parents or target == results_root:
        valid_path = True
    if receptor_root in target.parents or target == receptor_root:
        valid_path = True
    
    if not valid_path:
        raise HTTPException(status_code=400, detail="Invalid path.")
    if target.suffix.lower() not in {".pdb", ".sdf"}:
        raise HTTPException(status_code=400, detail="Unsupported file type.")
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(target, media_type="text/plain")


@router.post("/api/paths/resolve")
def resolve_path(payload: dict[str, Any]) -> JSONResponse:
    rel_path = str(payload.get("relative_path") or "").strip().lstrip("/\\")
    scope = str(payload.get("scope") or "generic").strip().lower()
    if not rel_path:
        raise HTTPException(status_code=400, detail="Missing relative_path.")

    rel = Path(rel_path)
    parts = [part for part in rel.parts if part not in {"", "."}]
    if not parts:
        raise HTTPException(status_code=400, detail="Invalid relative_path.")
    if ".." in parts:
        raise HTTPException(status_code=400, detail="Invalid relative_path.")

    def _is_safe(path: Path) -> bool:
        resolved = path.resolve()
        return resolved == BASE_RESOLVED or BASE_RESOLVED in resolved.parents

    def _existing_safe_dir(path: Path) -> Path | None:
        resolved = path.resolve()
        if not resolved.exists() or not resolved.is_dir():
            return None
        if not _is_safe(resolved):
            return None
        return resolved

    first = parts[0]
    if scope not in {"report", "results"} and first == BASE.name:
        return JSONResponse({"path": "."})

    if scope == "results":
        lowered = [part.lower() for part in parts]
        chosen: Path | None = None

        def _pick_results_under_dock(tail: list[str]) -> Path | None:
            if not tail:
                return _existing_safe_dir(DOCK_DIR_RESOLVED)
            # Results root should stay at the dock source level, not a deep run folder.
            direct = _existing_safe_dir(DOCK_DIR_RESOLVED / tail[0])
            if direct is not None:
                return direct
            return _existing_safe_dir(DOCK_DIR_RESOLVED)

        for idx in range(len(lowered) - 1):
            if lowered[idx] == "data" and lowered[idx + 1] == "dock":
                chosen = _pick_results_under_dock(parts[idx + 2 :])
                if chosen is not None:
                    break

        if chosen is None and lowered and lowered[0] == "dock":
            chosen = _pick_results_under_dock(parts[1:])

        if chosen is None:
            chosen = _existing_safe_dir(DOCK_DIR_RESOLVED / first)

        if chosen is None:
            chosen = _existing_safe_dir(DOCK_DIR_RESOLVED)

        return JSONResponse({"path": _to_display_path(chosen or DOCK_DIR_RESOLVED)})

    if scope == "report":
        lowered = [part.lower() for part in parts]
        chosen: Path | None = None

        def _pick_under_dock(tail: list[str]) -> Path | None:
            if not tail:
                return _existing_safe_dir(DOCK_DIR_RESOLVED)
            return _existing_safe_dir(DOCK_DIR_RESOLVED / tail[0])

        for idx in range(len(lowered) - 1):
            if lowered[idx] == "data" and lowered[idx + 1] == "dock":
                chosen = _pick_under_dock(parts[idx + 2 :])
                if chosen is not None:
                    break

        if chosen is None and "dock" in lowered:
            dock_idx = lowered.index("dock")
            chosen = _pick_under_dock(parts[dock_idx + 1 :])

        if chosen is None:
            direct = _existing_safe_dir(DOCK_DIR_RESOLVED / first)
            if direct is not None:
                chosen = direct
            elif first in {"D1", "D2", "D3", "D4", "D5"}:
                chosen = _existing_safe_dir(DOCK_DIR_RESOLVED / "dimer_final_linked")
            elif "_dimer_run" in first:
                chosen = _existing_safe_dir(DOCK_DIR_RESOLVED / "dimer_full")

        if chosen is None:
            chosen = DOCK_DIR_RESOLVED

        return JSONResponse({"path": _to_display_path(chosen)})

    anchors: tuple[Path, ...]
    anchors = (BASE_RESOLVED, DATA_DIR_RESOLVED, DOCK_DIR_RESOLVED)

    # Browser sends a file path inside selected folder. We inspect parent prefixes
    # from deepest to shallowest and pick the first existing safe directory.
    parent_parts = parts[:-1] if len(parts) > 1 else parts
    prefixes = [Path(*parent_parts[:idx]) for idx in range(len(parent_parts), 0, -1)]

    chosen: Path | None = None
    for prefix in prefixes:
        for anchor in anchors:
            candidate = (anchor / prefix).resolve()
            if candidate.exists() and candidate.is_dir() and _is_safe(candidate):
                chosen = candidate
                break
        if chosen is not None:
            break

    if chosen is None:
        for anchor in anchors:
            candidate = (anchor / first).resolve()
            if candidate.exists() and candidate.is_dir() and _is_safe(candidate):
                chosen = candidate
                break

    return JSONResponse({"path": _to_display_path(chosen or BASE_RESOLVED)})


def _to_display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        rel_to_data = resolved.relative_to(DATA_DIR_RESOLVED)
        rel_str = str(rel_to_data).replace("\\", "/")
        return "data" if rel_str in {"", "."} else f"data/{rel_str}"
    except ValueError:
        pass

    try:
        rel_to_base = resolved.relative_to(BASE_RESOLVED)
        rel_str = str(rel_to_base).replace("\\", "/")
        return "." if rel_str in {"", "."} else rel_str
    except ValueError:
        return str(resolved).replace("\\", "/")


