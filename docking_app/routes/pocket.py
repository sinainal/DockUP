from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from ..pocket_finder import (
    build_pocket_response,
    clear_runtime_state,
    compute_gridbox_for_pocket,
    get_runtime_state,
    run_p2rank_async,
)
from ..services import _get_meta, _normalize_receptor_id
from ..state import STATE

router = APIRouter()


def _selected_receptor_id(raw: str | None = None) -> str:
    requested = _normalize_receptor_id(raw or STATE.get("selected_receptor", ""))
    if not requested:
        raise HTTPException(status_code=400, detail="Select a receptor first.")
    return requested


def _selected_receptor_file(pdb_id: str) -> Path:
    meta = _get_meta(pdb_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Selected receptor not found.")
    raw_path = str(meta.get("pdb_file") or "").strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="Selected receptor has no stored PDB file.")
    path = Path(raw_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Selected receptor file is missing.")
    return path


def _output_dir_for_state(pdb_id: str) -> Path:
    state = get_runtime_state()
    if str(state.get("pdb_id") or "").upper() != pdb_id.upper():
        raise HTTPException(status_code=404, detail="No binding site results for selected receptor.")
    output_dir = Path(str(state.get("output_dir") or "")).expanduser()
    if not output_dir.exists():
        raise HTTPException(status_code=404, detail="Binding site output directory not found.")
    return output_dir


@router.post("/api/pockets/run")
def run_binding_site_finder(payload: dict[str, Any]) -> JSONResponse:
    pdb_id = _selected_receptor_id(str(payload.get("pdb_id") or ""))
    receptor_file = _selected_receptor_file(pdb_id)
    try:
        state = run_p2rank_async(pdb_id, receptor_file)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse(state)


@router.get("/api/pockets/status")
def binding_site_status(pdb_id: str = "") -> JSONResponse:
    requested = _selected_receptor_id(pdb_id)
    state = get_runtime_state()
    if str(state.get("pdb_id") or "").upper() and str(state.get("pdb_id") or "").upper() != requested.upper():
        return JSONResponse(
            {
                "status": "idle",
                "pdb_id": requested,
                "message": "",
                "error": "",
            }
        )
    return JSONResponse(state)


@router.get("/api/pockets/results")
def binding_site_results(pdb_id: str = "") -> JSONResponse:
    requested = _selected_receptor_id(pdb_id)
    state = get_runtime_state()
    if state.get("status") != "done":
        raise HTTPException(status_code=409, detail="Binding site prediction is not ready.")
    output_dir = _output_dir_for_state(requested)
    payload = build_pocket_response(output_dir)
    return JSONResponse(
        {
            "pdb_id": requested,
            "status": state.get("status"),
            **payload,
        }
    )


@router.get("/api/pockets/file")
def binding_site_file(kind: str, pdb_id: str = "") -> FileResponse:
    requested = _selected_receptor_id(pdb_id)
    output_dir = _output_dir_for_state(requested)
    if kind == "points":
        matches = sorted((output_dir / "visualizations" / "data").glob("*_points.pdb.gz"))
    elif kind == "protein":
        matches = sorted((output_dir / "visualizations" / "data").glob("*.pdb"))
    else:
        raise HTTPException(status_code=400, detail="Unsupported binding site file kind.")
    if not matches:
        raise HTTPException(status_code=404, detail="Binding site file not found.")
    return FileResponse(matches[0])


@router.post("/api/pockets/gridbox")
def binding_site_gridbox(payload: dict[str, Any]) -> JSONResponse:
    requested = _selected_receptor_id(str(payload.get("pdb_id") or ""))
    output_dir = _output_dir_for_state(requested)
    pocket_rank = int(payload.get("pocket_rank") or 0)
    if pocket_rank <= 0:
        raise HTTPException(status_code=400, detail="Pocket rank is required.")
    mode = str(payload.get("mode") or "fit").strip().lower()
    fixed_size = float(payload.get("fixed_size") or 20.0)
    padding = float(payload.get("padding") or 2.0)
    grid_data = compute_gridbox_for_pocket(
        output_dir,
        pocket_rank=pocket_rank,
        mode=mode,
        fixed_size=fixed_size,
        padding=padding,
    )
    return JSONResponse({"grid_data": grid_data})


@router.post("/api/pockets/clear")
def clear_binding_site_state() -> JSONResponse:
    state = clear_runtime_state()
    return JSONResponse(state)
