from __future__ import annotations

from pydantic import BaseModel


class ModePayload(BaseModel):
    mode: str


class LoadReceptorsPayload(BaseModel):
    pdb_ids: str = ""


class SelectReceptorPayload(BaseModel):
    pdb_id: str


class SelectLigandPayload(BaseModel):
    pdb_id: str
    ligand: str = ""
    chain: str = "all"


class QueueBuildPayload(BaseModel):
    grid_pad: str = ""
    runs: int = 1
    out_root: str = ""


class RunStartPayload(BaseModel):
    is_test_mode: bool = False
    batch_id: int | None = None


class RenderPayload(BaseModel):
    root_path: str = "data/dock"
    source_path: str = ""
    output_path: str = ""
    linked_path: str = ""
    dpi: int = 120
    receptors: list[str] = []
    run_by_receptor: dict[str, str] = {}
    is_preview: bool = False


class GraphPayload(BaseModel):
    root_path: str = "data/dock"
    source_path: str = ""
    output_path: str = ""
    linked_path: str = ""
    scripts: list[str] = []


class ReportCompilePayload(BaseModel):
    root_path: str = "data/dock"
    source_path: str = ""
    output_path: str = ""
    images_root_path: str = ""
    selected_images: list[str] = []
    figure_captions: dict[str, str] = {}
    figure_start_number: int = 1
    extra_sections: list[dict[str, str]] = []
