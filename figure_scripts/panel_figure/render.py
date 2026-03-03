import os
from typing import List, Tuple

from .config import (
    CARTOON_COLOR,
    CARTOON_TRANSPARENCY_BASE,
    LABEL_OFFSET_ANGSTROM,
    TargetConfig,
)
from .utils import ensure_dir, load_interacting_residues, run_pymol


def _sel_expr_for_residues(interact_res: List[Tuple[str, str, str]]) -> str:
    if not interact_res:
        return "none"
    by_chain: dict[str, list[str]] = {}
    for chain, _resn, resi in interact_res:
        by_chain.setdefault(chain, []).append(str(resi))
    parts: list[str] = []
    for chain, resis in by_chain.items():
        # keep order stable, try numeric sort if possible
        unique = sorted(set(resis), key=lambda x: int(x) if x.isdigit() else x)
        resi_list = "+".join(unique)
        parts.append(f"(receptor and chain {chain} and resi {resi_list})")
    return " or ".join(parts) if parts else "none"


def _viewport_from_dpi(dpi: int, base_w: int = 640, base_h: int = 480) -> Tuple[int, int]:
    dpi_val = max(30, min(600, int(dpi or 120)))
    scale = max(0.5, float(dpi_val) / 120.0)
    width = max(160, int(base_w * scale))
    height = max(120, int(base_h * scale))
    return width, height


def _fig1_view_block(cfg: TargetConfig) -> str:
    orient_sel = (cfg.fig1_orient_selection or "").strip() or "ligand"
    center_sel = (cfg.fig1_center_selection or "").strip() or "ligand"
    zoom_sel = (cfg.fig1_zoom_selection or "").strip() or "ligand"
    lines = [
        f"orient {orient_sel}",
    ]
    if cfg.fig1_turn_x:
        lines.append(f"turn x, {cfg.fig1_turn_x}")
    if cfg.fig1_turn_y:
        lines.append(f"turn y, {cfg.fig1_turn_y}")
    if cfg.fig1_turn_z:
        lines.append(f"turn z, {cfg.fig1_turn_z}")
    lines += [
        f"center {center_sel}",
        f"zoom {zoom_sel}, {cfg.fig1_zoom_buffer}",
    ]
    return "\n".join(lines)


def build_pml_base(cfg: TargetConfig, interact_res: List[Tuple[str, str, str]], pml_path: str, png_path: str) -> None:
    """Render base cartoon only (no sticks) to reduce occlusion after overlay."""
    sel_expr = _sel_expr_for_residues(interact_res)
    view_block = _fig1_view_block(cfg)
    ray_w, ray_h = _viewport_from_dpi(cfg.dpi)

    pml = f'''
reinitialize
load "{cfg.complex_pdb}", complex
remove resn HOH

hide everything
create receptor, complex and not resn UNL
create ligand, complex and resn UNL
select interact_res, {sel_expr}

show cartoon, receptor
set cartoon_transparency, {CARTOON_TRANSPARENCY_BASE}, receptor
set cartoon_color, {CARTOON_COLOR}, receptor

bg_color white
{view_block}
set near_clip, 0
set far_clip, 0
clip slab, 600
clip move, 50
set antialias, 2
set ray_opaque_background, off
viewport {ray_w}, {ray_h}

png {png_path}, dpi={cfg.dpi}, ray=1
quit
'''
    with open(pml_path, "w") as f:
        f.write(pml.strip() + "\n")


def build_pml_overlay(cfg: TargetConfig, interact_res: List[Tuple[str, str, str]], pml_path: str, png_path: str) -> None:
    """Render ligand + interacting residues + labels, no cartoon (for overlay)."""
    sel_expr = _sel_expr_for_residues(interact_res)
    view_block = _fig1_view_block(cfg)
    ray_w, ray_h = _viewport_from_dpi(cfg.dpi)

    label_block = ""
    if cfg.show_labels:
        label_block = """
label interact_res and name CA, "%s%s%s" % (resn, resi, chain)
set label_color, black
set label_size, 14
set label_shadow_mode, 2
"""

    pml = f'''
reinitialize
load "{cfg.complex_pdb}", complex
remove resn HOH

hide everything
create receptor, complex and not resn UNL
create ligand, complex and resn UNL
select interact_res, {sel_expr}

# Fix element fields for ligand (PDB missing element column)
alter ligand, elem = elem if elem != '' else name[1]
rebuild

# Ligand
show sticks, ligand
set_color ligand_tq, [0.25, 0.88, 0.82]
color ligand_tq, ligand
color blue, ligand and (elem N or name N*)
color red, ligand and (elem O or name O*)
color yellow, ligand and (elem S or name S*)
color forest, ligand and (elem F or name F*)
color green, ligand and (elem CL or name CL*)
color grey50, ligand and (elem H or name H*)
color ligand_tq, ligand and (elem C or name C* or elem "")
set stick_radius, 0.22, ligand

# Interacting residues
show sticks, interact_res
color orange, interact_res and elem C
color blue, interact_res and elem N
color red, interact_res and elem O
color yellow, interact_res and elem S
color forest, interact_res and elem F
color grey50, interact_res and elem H
set stick_transparency, 0.2, interact_res

{label_block}

bg_color white
{view_block}
set near_clip, 0
set far_clip, 0
clip slab, 600
clip move, 50
set antialias, 2
set ray_opaque_background, off
viewport {ray_w}, {ray_h}

png {png_path}, dpi={cfg.dpi}, ray=1
quit
'''
    with open(pml_path, "w") as f:
        f.write(pml.strip() + "\n")


def render_base_and_overlay(cfg: TargetConfig) -> Tuple[str, str]:
    ensure_dir(cfg.output_dir)
    interact_res = load_interacting_residues(cfg.interaction_json, cfg.plip_contacts_csv, cfg.plip_report_txt)

    base_png = os.path.join(cfg.output_dir, f"{cfg.name}_base_cartoon.png")
    base_pml = os.path.join(cfg.output_dir, f"{cfg.name}_base_cartoon.pml")
    overlay_png = os.path.join(cfg.output_dir, f"{cfg.name}_overlay_sticks.png")
    overlay_pml = os.path.join(cfg.output_dir, f"{cfg.name}_overlay_sticks.pml")

    build_pml_base(cfg, interact_res, base_pml, base_png)
    t_base = run_pymol(base_pml)

    build_pml_overlay(cfg, interact_res, overlay_pml, overlay_png)
    t_overlay = run_pymol(overlay_pml)

    if cfg.cleanup_intermediate:
        for tmp in (base_pml, overlay_pml):
            try:
                os.remove(tmp)
            except FileNotFoundError:
                pass

    print(f"Rendered base in {t_base:.2f}s, overlay in {t_overlay:.2f}s")
    return base_png, overlay_png
