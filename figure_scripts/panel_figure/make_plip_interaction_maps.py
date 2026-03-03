from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Dict, List

from PIL import Image

from .config import TargetConfig
from .panel import add_left_margin_label, load_bold_serif_font
from .pipeline import contacts_concat, render_contacts
from ..report_utils import LIGAND_ORDER, compute_best_run_table


def _find_complex_pdb(run_dir: Path) -> Path:
    pdbs = sorted(run_dir.glob("*_complex.pdb"))
    if not pdbs:
        raise FileNotFoundError(f"No *_complex.pdb in {run_dir}")
    return pdbs[0]


def _render_contacts_png(run_dir: Path, out_dir: Path, *, dpi: int = 30, zoom: float = 2.0) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    complex_pdb = _find_complex_pdb(run_dir)
    report_xml = run_dir / "plip" / "report.xml"

    cfg = TargetConfig(
        name=run_dir.name,
        complex_pdb=str(complex_pdb),
        interaction_json=str(run_dir / "interaction_map.json"),
        plip_report_txt=str(report_xml),
        output_dir=str(out_dir),
        dpi=dpi,
        show_labels=False,
        cleanup_intermediate=True,
        contacts_zoom=zoom,
    )

    raw_png, pse = render_contacts(cfg, report_xml)
    final_png = contacts_concat(Path(cfg.output_dir), pse, dpi=dpi)
    try:
        Path(raw_png).unlink()
    except FileNotFoundError:
        pass
    return final_png


def _compose_vertical(images: List[Image.Image], *, pad: int = 20, bg=(255, 255, 255, 255)) -> Image.Image:
    widths = [im.width for im in images]
    heights = [im.height for im in images]
    w = max(widths)
    h = sum(heights) + pad * (len(images) - 1)
    canvas = Image.new("RGBA", (w, h), bg)
    y = 0
    for im in images:
        x = (w - im.width) // 2
        canvas.alpha_composite(im, dest=(x, y))
        y += im.height + pad
    return canvas


def main() -> None:
    results_root = Path("monomer_final") / "results"
    best_runs = compute_best_run_table(results_root)

    out_dir = Path("report") / "assets" / "plip_interaction_maps"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_root = out_dir / ".tmp"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    tmp_root.mkdir(exist_ok=True)

    font = load_bold_serif_font(28)
    receptors = [f"D{i}" for i in range(1, 6)]

    t0 = time.time()
    for lig in LIGAND_ORDER:
        lig_short = lig.replace("_1", "")
        imgs: List[Image.Image] = []
        for rec in receptors:
            run = best_runs[rec][lig] or "run1"
            run_dir = results_root / rec / lig / run
            tile_dir = tmp_root / f"{rec}_{lig_short}"
            png = _render_contacts_png(run_dir, tile_dir, dpi=30, zoom=2.0)
            with Image.open(png) as im:
                tile = im.convert("RGBA").copy()
            # Label each row with receptor (D1..D5)
            tile = add_left_margin_label(tile, rec, font, pad=12, stroke_width=1, min_left_margin=80)
            imgs.append(tile)
            shutil.rmtree(tile_dir, ignore_errors=True)

        collage = _compose_vertical(imgs, pad=18, bg=(255, 255, 255, 255))
        out_path = out_dir / f"plip_contacts_{lig_short}.png"
        collage.save(out_path)
        print(f"Wrote {out_path}")

    shutil.rmtree(tmp_root, ignore_errors=True)
    print(f"Done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()

