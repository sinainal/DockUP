from pathlib import Path
from typing import Tuple

from PIL import Image

from .config import MASK_THRESHOLD


def mask_by_threshold(img: Image.Image, threshold: int = MASK_THRESHOLD) -> Image.Image:
    rgba = img.convert("RGBA")
    data = []
    for r, g, b, a in rgba.getdata():
        if r > threshold and g > threshold and b > threshold:
            data.append((r, g, b, 0))
        else:
            data.append((r, g, b, a))
    rgba.putdata(data)
    return rgba


def compose(base_path: str, overlay_path: str, out_path: str) -> None:
    base = Image.open(base_path).convert("RGBA")
    overlay = Image.open(overlay_path)
    if overlay.size != base.size:
        overlay = overlay.resize(base.size, Image.LANCZOS)
    if overlay.mode in ("RGBA", "LA") or "transparency" in overlay.info:
        overlay_rgba = overlay.convert("RGBA")
    else:
        overlay_rgba = mask_by_threshold(overlay)
    base.alpha_composite(overlay_rgba)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    base.save(out_path)
    print(f"Wrote composite {out_path}")


def compose_with_paths(base_overlay: Tuple[str, str], out_path: str) -> None:
    base_path, overlay_path = base_overlay
    compose(base_path, overlay_path, out_path)
