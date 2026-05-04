from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ControlError(BaseModel):
    code: str = "control_error"
    message: str
    recoverable: bool = True
    next_actions: list[str] = Field(default_factory=list)


class ControlEnvelope(BaseModel):
    ok: bool
    action: str
    trace_id: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    changed: dict[str, Any] = Field(default_factory=dict)
    ui_hints: dict[str, Any] = Field(default_factory=dict)
    error: ControlError | None = None


class ReceptorLoadRequest(BaseModel):
    pdb_ids: str = ""


class ReceptorSelectRequest(BaseModel):
    pdb_id: str


class ReceptorDeleteRequest(BaseModel):
    target: str = ""


class LigandFetchRequest(BaseModel):
    ligand_ids: str = ""


class LigandDeleteRequest(BaseModel):
    name: str = ""


class ActiveLigandsSetRequest(BaseModel):
    names: list[str] = Field(default_factory=list)
    replace: bool = True


class LigandGenerateRequest(BaseModel):
    specs: list[dict[str, Any]] = Field(default_factory=list)
    reset: bool = False
    activate: bool = True


class ViewerShowRequest(BaseModel):
    pdb_id: str
    chain: str = ""


class ViewerResiduesRequest(BaseModel):
    pdb_id: str = ""
    residue: str = "TRP"
    chain: str = "all"


class WorkspaceSelectRequest(BaseModel):
    receptor: str = "all"
    chain: str = "auto"
    native_ligand: str = "auto"
    dock_ligands: str = "all"


class GridboxSetRequest(BaseModel):
    method: str = "native_ligand"
    size: float = 20.0
    padding: float = 0.0
    center: str = ""
    pocket_rank: int = 1
    p2rank_mode: str = "fit"


class GridboxSetManyRequest(BaseModel):
    grid_data: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ConfigSetRequest(BaseModel):
    engine: str = "vina_gpu_21"
    mode: str = "standard"
    run_count: int = 1
    padding: float = 0.0
    out_root_name: str = ""
    exhaustiveness: int | None = None
    num_modes: int | None = None
    energy_range: float | None = None
    cpu: int | None = None
    seed: int | None = None
    ph: float | None = None
    advanced: str = ""


class QueueBuildRequest(BaseModel):
    replace_queue: bool = True


class QueuePrepareRequest(BaseModel):
    mode: str = "Docking"
    receptors: list[str] = Field(default_factory=list)
    chains: dict[str, str] = Field(default_factory=dict)
    ligands: list[str] = Field(default_factory=list)
    ligand_specs: list[dict[str, Any]] = Field(default_factory=list)
    grid_data: dict[str, dict[str, Any]] = Field(default_factory=dict)
    selection_map: dict[str, dict[str, Any]] = Field(default_factory=dict)
    docking_config: dict[str, Any] = Field(default_factory=dict)
    run_count: int = 1
    padding: float = 0.0
    out_root_path: str = "data/dock"
    out_root_name: str = ""
    replace_queue: bool = True
    reset_queue: bool = True
    reset_ligands: bool = False
    activate_ligands: bool = True


class QueueRemoveRequest(BaseModel):
    batch_id: str = ""


class RunStartRequest(BaseModel):
    test_mode: bool = False
    batch_id: int | None = None


class ResultsScanRequest(BaseModel):
    root_path: str = "data/dock"


class ResultsDetailRequest(BaseModel):
    result_dir: str = ""
