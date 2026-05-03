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
