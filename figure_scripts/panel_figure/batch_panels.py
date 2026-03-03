from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Dict, Tuple

from PIL import Image

from .config import TargetConfig
from .panel import add_left_margin_label, compose_2x2, load_bold_serif_font
from .pipeline import run as run_pipeline


LIGAND_MAP: Tuple[Tuple[str, str], ...] = (("A", "PET"), ("B", "PS"), ("C", "PP"), ("D", "PE"))


def _find_paths(dtype: str, ligand: str, run: str = "run1") -> Tuple[str, str, str]:
    base = Path("monomer_final") / "results" / dtype / f"{ligand}_1" / run
    pdbs = sorted(base.glob("*_complex.pdb"))
    if not pdbs:
        raise FileNotFoundError(f"No *_complex.pdb found under {base}")
    complex_pdb = str(pdbs[0])
    interaction_json = str(base / "interaction_map.json")
    plip_report = str(base / "plip" / "report.xml")
    return complex_pdb, interaction_json, plip_report


def _render_combined_image(dtype: str, ligand: str, tmp_root: Path) -> Image.Image:
    tmp_dir = tmp_root / f"{dtype}_{ligand}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    complex_pdb, interaction_json, plip_report = _find_paths(dtype, ligand)
    cfg = TargetConfig(
        name=f"{dtype}_{ligand}",
        complex_pdb=complex_pdb,
        interaction_json=interaction_json,
        plip_report_txt=plip_report,
        plip_contacts_csv="",
        output_dir=str(tmp_dir),
        dpi=50,
        show_labels=False,
        cleanup_intermediate=True,
        contacts_zoom=0.0,
    )

    result = run_pipeline(cfg)
    combined_path = Path(result["combined_transparent"])
    with Image.open(combined_path) as im:
        img = im.convert("RGBA").copy()

    shutil.rmtree(tmp_dir, ignore_errors=True)
    return img


def render_dtype_panel(dtype: str, out_dir: Path, tmp_root: Path) -> Path:
    font = load_bold_serif_font(40)
    tiles: Dict[str, Image.Image] = {}
    for letter, ligand in LIGAND_MAP:
        combined = _render_combined_image(dtype, ligand, tmp_root)
        tiles[letter] = add_left_margin_label(combined, letter, font, pad=18, stroke_width=1, min_left_margin=90)

    panel = compose_2x2(tiles, order=("A", "B", "C", "D"))
    out_path = out_dir / f"{dtype}.png"
    panel.save(out_path)
    return out_path


def main():
    out_dir = Path("panels")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(exist_ok=True)
    tmp_root = Path(".tmp_render")
    tmp_root.mkdir(exist_ok=True)

    t0 = time.time()
    outputs = []
    for i in range(1, 6):
        dtype = f"D{i}"
        print(f"Rendering {dtype}...")
        t = time.time()
        out_path = render_dtype_panel(dtype, out_dir, tmp_root)
        outputs.append(out_path)
        print(f"Wrote {out_path} in {time.time() - t:.1f}s")

    shutil.rmtree(tmp_root, ignore_errors=True)
    print(f"Done. {len(outputs)} panels in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
