"""
Unified end-to-end pipeline (Figure1 + Figure2).
- Figure1: receptor cartoon + ligand + interacting residues (PLIP-based), labels off.
- Figure2: PLIP contacts, oriented via orient on ligand+interact_res (PCA-like), labels separated then recomposed (concat).
Outputs in cfg.output_dir:
  <name>_type2_final.png
  contacts_plip.png
  combined_transparent.png (alpha side-by-side)
  combined_white.png (flattened)
Intermediates cleaned.
"""

import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Tuple, Dict, Any, Optional

from PIL import Image

from .config import TargetConfig
from .concat import compose_with_paths
from .render import render_base_and_overlay
from .utils import run_pymol


def _viewport_from_dpi(dpi: int, base_w: int = 640, base_h: int = 480) -> Tuple[int, int]:
    dpi_val = max(30, min(600, int(dpi or 120)))
    scale = max(0.5, float(dpi_val) / 120.0)
    width = max(160, int(base_w * scale))
    height = max(120, int(base_h * scale))
    return width, height


def render_figure1(cfg: TargetConfig) -> str:
    base_png, overlay_png = render_base_and_overlay(cfg)
    final_path = os.path.join(cfg.output_dir, f"{cfg.name}_type2_final.png")
    compose_with_paths((base_png, overlay_png), final_path)
    if cfg.cleanup_intermediate:
        for tmp in (base_png, overlay_png, os.path.join(cfg.output_dir, f"{cfg.name}_base_cartoon.pml"), os.path.join(cfg.output_dir, f"{cfg.name}_overlay_sticks.pml")):
            try:
                os.remove(tmp)
            except FileNotFoundError:
                pass
    return final_path


def parse_plip(report_path: Path) -> Tuple[list, list]:
    """
    Return contacts as tuples:
      (reschain, restype, resnr, dist(float), lig_xyz(tuple), prot_xyz(tuple))

    Prefer XML if provided; TXT parsing only covers hydrophobic + hydrogen bonds.
    """
    if report_path.suffix.lower() == ".xml":
        tree = ET.parse(report_path)
        root = tree.getroot()
        contacts = []
        for inter in root.findall(".//bindingsite/interactions//*"):
            ligcoo = inter.find("ligcoo")
            protcoo = inter.find("protcoo")
            if ligcoo is None or protcoo is None:
                continue
            resnr = (inter.findtext("resnr") or "").strip()
            restype = (inter.findtext("restype") or "").strip()
            reschain = (inter.findtext("reschain") or "").strip()
            if not (resnr and restype and reschain):
                continue
            dist = None
            for tag in ("dist_h-a", "dist", "centdist", "dist_d-a"):
                txt = inter.findtext(tag)
                if txt is not None and str(txt).strip():
                    try:
                        dist = float(str(txt).strip())
                        break
                    except ValueError:
                        pass
            if dist is None:
                continue
            try:
                lx = float(ligcoo.findtext("x"))
                ly = float(ligcoo.findtext("y"))
                lz = float(ligcoo.findtext("z"))
                px = float(protcoo.findtext("x"))
                py = float(protcoo.findtext("y"))
                pz = float(protcoo.findtext("z"))
            except (TypeError, ValueError):
                continue
            contacts.append((reschain, restype, resnr, dist, (lx, ly, lz), (px, py, pz)))
        return contacts, []

    hbonds = []
    hydros = []
    mode = None
    with report_path.open() as f:
        for line in f:
            if line.startswith("**") and line.strip().endswith("**"):
                header = line.strip("*").strip().lower()
                if header == "hydrogen bonds":
                    mode = "hbond"
                elif header == "hydrophobic interactions":
                    mode = "hydro"
                else:
                    mode = None
                continue
            if mode is None:
                continue
            if line.startswith("|") and not line.startswith("| RESNR"):
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) < 8:
                    continue
                if mode == "hbond":
                    resnr, restype, reschain = parts[0], parts[1], parts[2]
                    dist = float(parts[7])  # DIST_H-A
                    lig_xyz = tuple(float(v.strip()) for v in parts[-2].split(","))
                    prot_xyz = tuple(float(v.strip()) for v in parts[-1].split(","))
                    hbonds.append((reschain, restype, resnr, dist, lig_xyz, prot_xyz))
                elif mode == "hydro":
                    resnr, restype, reschain = parts[0], parts[1], parts[2]
                    dist = float(parts[6])  # DIST
                    lig_xyz = tuple(float(v.strip()) for v in parts[9].split(","))
                    prot_xyz = tuple(float(v.strip()) for v in parts[10].split(","))
                    hydros.append((reschain, restype, resnr, dist, lig_xyz, prot_xyz))
    return hydros, hbonds


def build_contacts_pml(pdb_path: Path, hydros, hbonds, png_path: Path, pse_path: Path, dpi: int, zoom: float) -> str:
    ray_w, ray_h = _viewport_from_dpi(dpi)
    contacts = hydros + hbonds
    by_chain: dict[str, list[str]] = {}
    for chain, _resn, resi, *_rest in contacts:
        by_chain.setdefault(chain, []).append(str(resi))
    parts = []
    for chain, resis in by_chain.items():
        unique = sorted(set(resis), key=lambda x: int(x) if x.isdigit() else x)
        parts.append(f"(receptor and chain {chain} and resi {'+'.join(unique)})")
    sel_expr = " or ".join(parts) if parts else "none"
    lines = [
        "reinitialize",
        f"load {pdb_path}, complex",
        "remove resn HOH",
        "hide everything",
        "create receptor, complex and not resn UNL",
        "create ligand, complex and resn UNL",
        f"select interact_res, {sel_expr}",
        "select pca_sel, ligand or interact_res",
        "orient pca_sel",
        "show sticks, ligand",
        "set_color ligand_tq, [0.25, 0.88, 0.82]",
        "color ligand_tq, ligand",
        "color blue, ligand and (elem N or name N*)",
        "color red, ligand and (elem O or name O*)",
        "color yellow, ligand and (elem S or name S*)",
        "color forest, ligand and (elem F or name F*)",
        "color green, ligand and (elem CL or name CL*)",
        "color grey50, ligand and (elem H or name H*)",
        "color ligand_tq, ligand and (elem C or name C* or elem '')",
        "set stick_radius, 0.22, ligand",
        "show sticks, interact_res",
        "color orange, interact_res and elem C",
        "color blue, interact_res and elem N",
        "color red, interact_res and elem O",
        "color yellow, interact_res and elem S",
        "color forest, interact_res and elem F",
        "color grey50, interact_res and elem H",
        "set stick_transparency, 0.1, interact_res",
        "set dash_gap, 0.2",
        "set dash_length, 0.25",
        "set dash_width, 1.2",
        "set dash_color, yellow",
        "set label_color, black",
        "set label_size, 14",
        "set label_shadow_mode, 2",
    ]
    for idx, (reschain, resn, resi, dist, lig_xyz, prot_xyz) in enumerate(contacts, 1):
        lx, ly, lz = lig_xyz
        px, py, pz = prot_xyz
        mx, my, mz = (lx + px) / 2, (ly + py) / 2, (lz + pz) / 2
        lines.append(f"pseudoatom ligp{idx}, pos=[{lx:.3f},{ly:.3f},{lz:.3f}]")
        lines.append(f"pseudoatom protp{idx}, pos=[{px:.3f},{py:.3f},{pz:.3f}]")
        lines.append(f"pseudoatom mid{idx}, pos=[{mx:.3f},{my:.3f},{mz:.3f}]")
        lines.append(f"distance d{idx}_{resn}{resi}, ligp{idx}, protp{idx}")
        lines.append(f"hide labels, d{idx}_{resn}{resi}")
        lines.append(f"label mid{idx}, \"{dist:.2f}\"")
        lines.append(f"hide nonbonded, ligp{idx}")
        lines.append(f"hide nonbonded, protp{idx}")
        lines.append(f"hide nonbonded, mid{idx}")
    lines.append('label interact_res and name CA, "%s%s%s" % (resn, resi, chain)')
    lines += [
        "bg_color white",
        f"zoom pca_sel, {zoom}",
        "set near_clip, 0",
        "set far_clip, 0",
        "clip slab, 600",
        "clip move, 50",
        "set antialias, 2",
        "set ray_opaque_background, off",
        f"viewport {ray_w}, {ray_h}",
        f"png {png_path}, dpi={dpi}, ray=1",
        f"save {pse_path}",
        "quit",
    ]
    return "\n".join(lines) + "\n"


def render_contacts(cfg: TargetConfig, report_path: Path) -> Tuple[Path, Path]:
    hydros, hbonds = parse_plip(report_path)
    out_png = Path(cfg.output_dir) / "contacts_plip_raw.png"
    out_pse = Path(cfg.output_dir) / "contacts_plip.pse"
    pml = build_contacts_pml(
        Path(cfg.complex_pdb),
        hydros,
        hbonds,
        out_png,
        out_pse,
        dpi=cfg.dpi,
        zoom=cfg.contacts_zoom,
    )
    pml_path = Path(cfg.output_dir) / "contacts_plip.pml"
    pml_path.write_text(pml)
    run_pymol(str(pml_path))
    return out_png, out_pse


def contacts_concat(output_dir: Path, pse_path: Path, dpi: int = 30) -> Path:
    out_dir = Path(output_dir)
    no_labels = out_dir / "contacts_plip_nolabels.png"
    labels_only = out_dir / "contacts_plip_labelsonly.png"
    composite = out_dir / "contacts_plip.png"
    ray_w, ray_h = _viewport_from_dpi(dpi)

    # no labels
    pml_no = f"""
reinitialize
load {pse_path}, contacts
hide labels
set label_size, 0
hide nonbonded
bg_color white
viewport {ray_w}, {ray_h}
png {no_labels}, dpi={dpi}, ray=1
quit
"""
    (out_dir / "contacts_plip_nolabels.pml").write_text(pml_no)
    run_pymol(str(out_dir / "contacts_plip_nolabels.pml"))

    # labels only
    pml_lab = f"""
reinitialize
load {pse_path}, contacts
hide everything
label all, label
hide nonbonded
bg_color white
set ray_opaque_background, off
viewport {ray_w}, {ray_h}
png {labels_only}, dpi={dpi}, ray=1
quit
"""
    (out_dir / "contacts_plip_labelsonly.pml").write_text(pml_lab)
    run_pymol(str(out_dir / "contacts_plip_labelsonly.pml"))

    base = Image.open(no_labels).convert("RGBA")
    overlay = Image.open(labels_only).convert("RGBA")
    base.alpha_composite(overlay)
    base.save(composite, dpi=(dpi, dpi))

    if True:
        for tmp in (
            no_labels,
            labels_only,
            out_dir / "contacts_plip_nolabels.pml",
            out_dir / "contacts_plip_labelsonly.pml",
            pse_path,
            out_dir / "contacts_plip.pml",
        ):
            try:
                Path(tmp).unlink()
            except FileNotFoundError:
                pass
    return composite


def alpha_side_by_side(fig1: Path, fig2: Path, out_dir: Path) -> Tuple[Path, Path]:
    img1 = Image.open(fig1).convert("RGBA")
    img2 = Image.open(fig2).convert("RGBA")
    h = max(img1.height, img2.height)
    canvas = Image.new("RGBA", (img1.width + img2.width, h), (255, 255, 255, 0))
    canvas.alpha_composite(img1, dest=(0, (h - img1.height) // 2))
    canvas.alpha_composite(img2, dest=(img1.width, (h - img2.height) // 2))
    out_trans = Path(out_dir) / "combined_transparent.png"
    out_white = Path(out_dir) / "combined_white.png"
    canvas.save(out_trans)
    white = Image.new("RGB", canvas.size, "white")
    white.paste(canvas, mask=canvas.split()[3])
    white.save(out_white)
    return out_trans, out_white


def run(cfg: TargetConfig) -> Dict[str, Any]:
    Path(cfg.output_dir).mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    # Figure1
    t = time.time()
    fig1_path = render_figure1(cfg)
    t_fig1 = time.time() - t

    # Figure2 with PCA orient + label concat
    t = time.time()
    fig2_raw, fig2_pse = render_contacts(cfg, Path(cfg.plip_report_txt))
    fig2_png = contacts_concat(Path(cfg.output_dir), fig2_pse, dpi=cfg.dpi)
    if cfg.cleanup_intermediate:
        try:
            Path(fig2_raw).unlink()
        except FileNotFoundError:
            pass
    t_fig2 = time.time() - t

    # Side-by-side alpha composite
    t = time.time()
    trans, white = alpha_side_by_side(Path(fig1_path), Path(fig2_png), Path(cfg.output_dir))
    t_alpha = time.time() - t

    total = time.time() - t0
    return {
        "fig1": fig1_path,
        "fig2": str(fig2_png),
        "combined_transparent": str(trans),
        "combined_white": str(white),
        "timing": {"fig1": t_fig1, "fig2": t_fig2, "alpha": t_alpha, "total": total},
    }


def main(output_dir: Optional[str] = None):
    cfg = TargetConfig()
    if output_dir is not None:
        cfg.output_dir = output_dir
    result = run(cfg)
    t = result["timing"]
    print(f"Paths -> fig1: {result['fig1']}, fig2: {result['fig2']}, combined: {result['combined_transparent']} / {result['combined_white']}")
    print(f"Timing -> fig1 {t['fig1']:.2f}s, fig2 {t['fig2']:.2f}s, alpha {t['alpha']:.2f}s, total {t['total']:.2f}s")


if __name__ == "__main__":
    main()
