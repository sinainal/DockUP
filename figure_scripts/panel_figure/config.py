import os
from dataclasses import dataclass


@dataclass
class TargetConfig:
    name: str = "D1_PET1"
    complex_pdb: str = os.path.join("monomer_final", "results", "D1", "PET_1", "run1", "7X2F_complex.pdb")
    interaction_json: str = os.path.join("monomer_final", "results", "D1", "PET_1", "run1", "interaction_map.json")
    plip_report_txt: str = os.path.join("monomer_final", "results", "D1", "PET_1", "run1", "plip", "report.xml")
    plip_contacts_csv: str = ""
    output_dir: str = "output"
    dpi: int = 30
    show_labels: bool = False
    cleanup_intermediate: bool = True
    contacts_zoom: float = 0.0
    # Figure1 view controls (for stable "fixed" camera)
    fig1_orient_selection: str = "ligand"  # "ligand" or "receptor"
    fig1_center_selection: str = "ligand"
    fig1_zoom_selection: str = "ligand"
    fig1_zoom_buffer: float = 10.0
    fig1_turn_x: float = 0.0
    fig1_turn_y: float = 0.0
    fig1_turn_z: float = 0.0


# Defaults for figure options
CARTOON_TRANSPARENCY_BASE = 0.75  # for base cartoon render
CARTOON_COLOR = "grey85"
LABEL_OFFSET_ANGSTROM = 2.0
MASK_THRESHOLD = 240  # overlay white removal in concat
