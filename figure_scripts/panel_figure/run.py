"""
End-to-end run:
1) Render base cartoon-only image and overlay (ligand + interacting residues + labels with offset) via PyMOL.
2) Compose them with masking to drop white background from overlay.

Outputs placed under cfg.output_dir (default test6).
"""

import os
from pathlib import Path

from .config import TargetConfig
from .concat import compose_with_paths
from .render import render_base_and_overlay


def main():
    cfg = TargetConfig()
    base_png, overlay_png = render_base_and_overlay(cfg)
    out_final = os.path.join(cfg.output_dir, f"{cfg.name}_type2_final.png")
    compose_with_paths((base_png, overlay_png), out_final)
    if cfg.cleanup_intermediate:
        for p in (base_png, overlay_png):
            try:
                Path(p).unlink()
            except FileNotFoundError:
                pass
    print("Final image:", out_final)


if __name__ == "__main__":
    main()
