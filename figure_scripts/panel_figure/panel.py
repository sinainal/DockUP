from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, Tuple

from PIL import Image, ImageDraw, ImageFont


def load_bold_serif_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Prefer Times New Roman Bold; fall back to common Times-like bold serif fonts."""
    candidates = [
        "Times New Roman Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    ]
    for cand in candidates:
        try:
            return ImageFont.truetype(cand, size)
        except OSError:
            continue
    return ImageFont.load_default()


def add_left_margin_label(
    img: Image.Image,
    label: str,
    font: ImageFont.ImageFont,
    *,
    pad: int = 18,
    stroke_width: int = 1,
    min_left_margin: int = 90,
) -> Image.Image:
    """Add transparent left margin and draw label in top-left (black with white stroke)."""
    base = img.convert("RGBA")
    tmp_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1), (0, 0, 0, 0)))
    bbox = tmp_draw.textbbox((0, 0), label, font=font, stroke_width=stroke_width)
    label_w = bbox[2] - bbox[0]
    left_margin = max(min_left_margin, pad + label_w + pad)

    out = Image.new("RGBA", (base.width + left_margin, base.height), (255, 255, 255, 0))
    out.alpha_composite(base, dest=(left_margin, 0))

    draw = ImageDraw.Draw(out)
    draw.text(
        (pad, pad),
        label,
        fill="black",
        font=font,
        stroke_width=stroke_width,
        stroke_fill="white",
    )
    return out


def compose_2x2(
    tiles: Dict[str, Image.Image],
    *,
    order: Tuple[str, str, str, str] = ("A", "B", "C", "D"),
) -> Image.Image:
    """Compose A/B/C/D tiles into a 2x2 RGBA panel."""
    imgs = [tiles[k].convert("RGBA") for k in order]
    w, h = imgs[0].size
    for im in imgs[1:]:
        if im.size != (w, h):
            raise ValueError(f"Tile sizes differ: expected {(w, h)}, got {im.size}")

    panel = Image.new("RGBA", (w * 2, h * 2), (255, 255, 255, 0))
    positions = [(0, 0), (w, 0), (0, h), (w, h)]
    for im, pos in zip(imgs, positions):
        panel.alpha_composite(im, dest=pos)
    return panel

