from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from ..pocket_finder import (
    build_pocket_response,
    clear_cached_results,
    clear_runtime_state,
    compute_gridbox_for_pocket,
    get_runtime_state,
    latest_output_dir,
    run_p2rank_async,
)
from ..services import _get_meta, _normalize_chain_id, _normalize_receptor_id
from ..state import STATE

router = APIRouter()


def _selected_receptor_id(raw: str | None = None) -> str:
    requested = _normalize_receptor_id(raw or STATE.get("selected_receptor", ""))
    if not requested:
        raise HTTPException(status_code=400, detail="Select a receptor first.")
    return requested


def _selected_chain_for_receptor(pdb_id: str, raw: str | None = None) -> str:
    if raw is not None and str(raw).strip():
        return _normalize_chain_id(raw)
    selection = STATE.get("selection_map", {}).get(pdb_id, {})
    if pdb_id == _normalize_receptor_id(STATE.get("selected_receptor", "")):
        return _normalize_chain_id(selection.get("chain", STATE.get("selected_chain", "all")))
    return _normalize_chain_id(selection.get("chain", "all"))


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


def _output_dir_for_receptor(pdb_id: str, chain: str = "all") -> Path:
    state = get_runtime_state()
    selected_chain = _normalize_chain_id(chain)
    if (
        str(state.get("pdb_id") or "").upper() == pdb_id.upper()
        and _normalize_chain_id(state.get("chain") or "all") == selected_chain
    ):
        output_dir = Path(str(state.get("output_dir") or "")).expanduser()
        if output_dir.exists():
            return output_dir
    cached = latest_output_dir(pdb_id, selected_chain)
    if cached and cached.exists():
        return cached
    raise HTTPException(status_code=404, detail="No binding site results for selected receptor.")


def _status_for_receptor(pdb_id: str, chain: str = "all") -> dict[str, Any]:
    state = get_runtime_state()
    selected_chain = _normalize_chain_id(chain)
    if (
        str(state.get("pdb_id") or "").upper() == pdb_id.upper()
        and _normalize_chain_id(state.get("chain") or "all") == selected_chain
        and state.get("status") != "idle"
    ):
        return state
    cached = latest_output_dir(pdb_id, selected_chain)
    if cached and cached.exists():
        return {
            "job_id": int(state.get("job_id") or 0),
            "status": "done",
            "pdb_id": pdb_id.upper(),
            "chain": selected_chain,
            "message": f"Binding site prediction ready for {pdb_id.upper()} ({selected_chain}).",
            "error": "",
            "started_at": None,
            "finished_at": None,
            "work_dir": str(cached.parent),
            "output_dir": str(cached),
        }
    if str(state.get("pdb_id") or "").upper():
        return {
            "job_id": int(state.get("job_id") or 0),
            "status": "idle",
            "pdb_id": pdb_id.upper(),
            "chain": selected_chain,
            "message": "",
            "error": "",
            "started_at": None,
            "finished_at": None,
            "work_dir": "",
            "output_dir": "",
        }
    return {
        "job_id": int(state.get("job_id") or 0),
        "status": "idle",
        "pdb_id": pdb_id.upper(),
        "chain": selected_chain,
        "message": "",
        "error": "",
        "started_at": None,
        "finished_at": None,
        "work_dir": "",
        "output_dir": "",
    }


def _running_other_receptor(requested: str, chain: str = "all") -> bool:
    state = get_runtime_state()
    return (
        state.get("status") == "running"
        and (
            str(state.get("pdb_id") or "").upper() != requested.upper()
            or _normalize_chain_id(state.get("chain") or "all") != _normalize_chain_id(chain)
        )
    )


@router.post("/api/pockets/run")
def run_binding_site_finder(payload: dict[str, Any]) -> JSONResponse:
    pdb_id = _selected_receptor_id(str(payload.get("pdb_id") or ""))
    chain = _selected_chain_for_receptor(pdb_id, str(payload.get("chain") or ""))
    force_rerun = bool(payload.get("force_rerun"))
    if not force_rerun:
        cached = latest_output_dir(pdb_id, chain)
        if cached and cached.exists():
            return JSONResponse(_status_for_receptor(pdb_id, chain))
    if _running_other_receptor(pdb_id, chain):
        raise HTTPException(status_code=409, detail="Another binding site prediction is already running.")
    receptor_file = _selected_receptor_file(pdb_id)
    try:
        state = run_p2rank_async(pdb_id, receptor_file, chain=chain)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse(state)


@router.get("/api/pockets/status")
def binding_site_status(pdb_id: str = "", chain: str = "") -> JSONResponse:
    requested = _selected_receptor_id(pdb_id)
    selected_chain = _selected_chain_for_receptor(requested, chain)
    return JSONResponse(_status_for_receptor(requested, selected_chain))


@router.get("/api/pockets/results")
def binding_site_results(pdb_id: str = "", chain: str = "") -> JSONResponse:
    requested = _selected_receptor_id(pdb_id)
    selected_chain = _selected_chain_for_receptor(requested, chain)
    state = _status_for_receptor(requested, selected_chain)
    if state.get("status") == "running":
        raise HTTPException(status_code=409, detail="Binding site prediction is not ready.")
    output_dir = _output_dir_for_receptor(requested, selected_chain)
    payload = build_pocket_response(output_dir)
    return JSONResponse(
        {
            "pdb_id": requested,
            "chain": selected_chain,
            "status": "done",
            **payload,
        }
    )


@router.get("/api/pockets/file")
def binding_site_file(kind: str, pdb_id: str = "", chain: str = "") -> FileResponse:
    requested = _selected_receptor_id(pdb_id)
    selected_chain = _selected_chain_for_receptor(requested, chain)
    output_dir = _output_dir_for_receptor(requested, selected_chain)
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
    selected_chain = _selected_chain_for_receptor(requested, str(payload.get("chain") or ""))
    output_dir = _output_dir_for_receptor(requested, selected_chain)
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
    return JSONResponse({"grid_data": grid_data, "chain": selected_chain})


@router.post("/api/pockets/clear")
def clear_binding_site_state(payload: dict[str, Any] | None = None) -> JSONResponse:
    payload = payload or {}
    requested = _normalize_receptor_id(str(payload.get("pdb_id") or ""))
    selected_chain = _selected_chain_for_receptor(requested, str(payload.get("chain") or "")) if requested else "all"
    if requested:
        clear_cached_results(requested, None if selected_chain == "all" and not str(payload.get("chain") or "").strip() else selected_chain)
        state = get_runtime_state()
        if (
            str(state.get("pdb_id") or "").upper() == requested.upper()
            and _normalize_chain_id(state.get("chain") or "all") == selected_chain
        ):
            return JSONResponse(clear_runtime_state())
        return JSONResponse(_status_for_receptor(requested, selected_chain))
    return JSONResponse(clear_runtime_state())
