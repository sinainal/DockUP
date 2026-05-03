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


class ViewerShowRequest(BaseModel):
    pdb_id: str
    chain: str = ""


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


class QueueRemoveRequest(BaseModel):
    batch_id: str = ""


class RunStartRequest(BaseModel):
    test_mode: bool = False
    batch_id: int | None = None
