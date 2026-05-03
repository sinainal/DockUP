from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..control import actions
from ..control.models import (
    LigandDeleteRequest,
    LigandFetchRequest,
    ReceptorDeleteRequest,
    ReceptorLoadRequest,
    ReceptorSelectRequest,
    ViewerShowRequest,
)

router = APIRouter(prefix="/api/control")


@router.get("/state")
def control_state() -> JSONResponse:
    return JSONResponse(actions.get_state())


@router.get("/receptors/list")
def control_receptor_list() -> JSONResponse:
    return JSONResponse(actions.list_receptors())


@router.post("/receptors/load")
def control_receptor_load(payload: ReceptorLoadRequest) -> JSONResponse:
    return JSONResponse(actions.load_receptors(payload.pdb_ids))


@router.post("/receptors/select")
def control_receptor_select(payload: ReceptorSelectRequest) -> JSONResponse:
    result = actions.select_receptor(payload.pdb_id)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.post("/receptors/delete")
def control_receptor_delete(payload: ReceptorDeleteRequest) -> JSONResponse:
    result = actions.delete_receptor(payload.target)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.post("/receptors/clear")
def control_receptor_clear() -> JSONResponse:
    return JSONResponse(actions.clear_receptors())


@router.get("/ligands/list")
def control_ligand_list() -> JSONResponse:
    return JSONResponse(actions.list_ligands())


@router.post("/ligands/fetch")
def control_ligand_fetch(payload: LigandFetchRequest) -> JSONResponse:
    result = actions.fetch_ligands(payload.ligand_ids)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.post("/ligands/delete")
def control_ligand_delete(payload: LigandDeleteRequest) -> JSONResponse:
    result = actions.delete_ligand(payload.name)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.post("/ligands/clear")
def control_ligand_clear() -> JSONResponse:
    return JSONResponse(actions.clear_ligands())


@router.post("/viewer/show")
def control_viewer_show(payload: ViewerShowRequest) -> JSONResponse:
    result = actions.show_viewer(payload.pdb_id, chain=payload.chain)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)
