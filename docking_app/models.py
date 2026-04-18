from __future__ import annotations

from pydantic import BaseModel


class ModePayload(BaseModel):
    mode: str


class LoadReceptorsPayload(BaseModel):
    pdb_ids: str = ""

class FetchLigandsPayload(BaseModel):
    ligand_ids: str = ""


class SelectReceptorPayload(BaseModel):
    pdb_id: str


class SelectLigandPayload(BaseModel):
    pdb_id: str
    ligand: str = ""
    ligands: list[str] = []
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
    render_mode: str = "classic"
    otofigure_style: str = "balanced"
    otofigure_ray_trace: bool = True
    otofigure_render_engine: str = "ray"
    otofigure_background: str = "transparent"
    otofigure_surface_enabled: bool = True
    otofigure_surface_opacity: float = 0.50
    otofigure_protein_color: str = "bluewhite"
    otofigure_ligand_thickness: float = 0.22
    otofigure_far_ratio: int = 4
    otofigure_close_ratio: int = 2
    otofigure_interaction_ratio: int = 3
    otofigure_far_padding: float = 0.03
    otofigure_far_frame_margin: float = 0.03
    otofigure_close_padding: float = 0.20
    receptors: list[str] = []
    run_by_receptor: dict[str, str] = {}
    ligand_by_receptor: dict[str, str] = {}
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
