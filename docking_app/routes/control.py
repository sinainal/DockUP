from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..control import actions
from ..control.events import latest_event
from ..control.models import (
    LigandDeleteRequest,
    LigandFetchRequest,
    ConfigSetRequest,
    GridboxSetRequest,
    QueueBuildRequest,
    QueueRemoveRequest,
    ReceptorDeleteRequest,
    ReceptorLoadRequest,
    ReceptorSelectRequest,
    ResultsDetailRequest,
    ResultsScanRequest,
    RunStartRequest,
    ViewerResiduesRequest,
    ViewerShowRequest,
    WorkspaceSelectRequest,
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


@router.get("/assets/inspect")
def control_assets_inspect() -> JSONResponse:
    return JSONResponse(actions.inspect_assets())


@router.post("/viewer/show")
def control_viewer_show(payload: ViewerShowRequest) -> JSONResponse:
    result = actions.show_viewer(payload.pdb_id, chain=payload.chain)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.post("/viewer/residues")
def control_viewer_residues(payload: ViewerResiduesRequest) -> JSONResponse:
    result = actions.show_residues(payload.pdb_id, residue=payload.residue, chain=payload.chain)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.post("/workspace/select")
def control_workspace_select(payload: WorkspaceSelectRequest) -> JSONResponse:
    result = actions.select_workspace(
        payload.receptor,
        chain=payload.chain,
        native_ligand=payload.native_ligand,
        dock_ligands=payload.dock_ligands,
    )
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.post("/gridbox/set")
def control_gridbox_set(payload: GridboxSetRequest) -> JSONResponse:
    result = actions.set_gridbox(
        payload.method,
        size=payload.size,
        padding=payload.padding,
        center=payload.center,
        pocket_rank=payload.pocket_rank,
        p2rank_mode=payload.p2rank_mode,
    )
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.post("/config/set")
def control_config_set(payload: ConfigSetRequest) -> JSONResponse:
    result = actions.set_config(
        engine=payload.engine,
        mode=payload.mode,
        run_count=payload.run_count,
        padding=payload.padding,
        out_root_name=payload.out_root_name,
        exhaustiveness=payload.exhaustiveness,
        num_modes=payload.num_modes,
        energy_range=payload.energy_range,
        cpu=payload.cpu,
        seed=payload.seed,
        ph=payload.ph,
        advanced=payload.advanced,
    )
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.get("/queue/list")
def control_queue_list() -> JSONResponse:
    return JSONResponse(actions.get_queue())


@router.post("/queue/build")
def control_queue_build(payload: QueueBuildRequest) -> JSONResponse:
    result = actions.build_queue(replace_queue=payload.replace_queue)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.post("/queue/remove")
def control_queue_remove(payload: QueueRemoveRequest) -> JSONResponse:
    result = actions.remove_queue_batch(payload.batch_id)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.post("/run/start")
def control_run_start(payload: RunStartRequest) -> JSONResponse:
    result = actions.run_start(test_mode=payload.test_mode, batch_id=payload.batch_id)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.post("/run/stop")
def control_run_stop() -> JSONResponse:
    result = actions.run_stop()
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.get("/run/status")
def control_run_status() -> JSONResponse:
    return JSONResponse(actions.run_status())


@router.get("/events/latest")
def control_events_latest(after_id: int = 0) -> JSONResponse:
    return JSONResponse(latest_event(after_id))


@router.get("/results/folders")
def control_results_folders() -> JSONResponse:
    return JSONResponse(actions.results_folders())


@router.post("/results/scan")
def control_results_scan(payload: ResultsScanRequest) -> JSONResponse:
    result = actions.results_scan(payload.root_path)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.post("/results/detail")
def control_results_detail(payload: ResultsDetailRequest) -> JSONResponse:
    result = actions.results_detail(payload.result_dir)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)
