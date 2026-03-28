from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from figure_scripts.otofigure import final_formatter


@pytest.mark.unit
def test_create_formatted_figures_preserves_transparent_background(tmp_path: Path) -> None:
    input_dir = tmp_path / "final_results"
    output_dir = tmp_path / "formatted_results"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    image_path = input_dir / "3pbl_demo_final.png"
    image = Image.new("RGBA", (300, 150), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((50, 30, 170, 110), radius=16, fill=(235, 124, 119, 255))
    image.save(image_path)

    df = final_formatter.process_image_directory(str(input_dir))
    assert df is not None

    output_files = final_formatter.create_formatted_figures(df, str(output_dir), max_images=1, render_dpi=30)
    assert output_files

    with Image.open(output_files[0]) as image_obj:
        rgba = image_obj.convert("RGBA")
        assert rgba.getpixel((0, 0))[3] == 0
        assert any(pixel[3] == 0 for pixel in rgba.getdata())
        assert any(pixel[3] > 0 for pixel in rgba.getdata())
        assert image_obj.info.get("dpi") == pytest.approx((60, 60), rel=0.02)
