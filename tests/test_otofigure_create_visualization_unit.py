from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from figure_scripts.otofigure import create_visualization


def _make_transparent_panel(path: Path, *, size: tuple[int, int], ligand_box: tuple[int, int, int, int]) -> None:
    img = Image.new("RGBA", size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((20, 15, size[0] - 20, size[1] - 15), fill=(225, 228, 244, 255))
    draw.rounded_rectangle(ligand_box, radius=18, fill=(235, 124, 119, 255))
    img.save(path)


def test_create_visualization_preserves_transparent_background(tmp_path: Path) -> None:
    input_dir = tmp_path / "results"
    output_dir = tmp_path / "final_results"
    interaction_dir = tmp_path / "interaction"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    interaction_dir.mkdir(parents=True, exist_ok=True)

    _make_transparent_panel(
        input_dir / "3pbl_demo_far.png",
        size=(420, 300),
        ligand_box=(180, 90, 250, 180),
    )
    _make_transparent_panel(
        input_dir / "3pbl_demo_close.png",
        size=(300, 300),
        ligand_box=(95, 55, 205, 245),
    )

    ok = create_visualization.create_visualization(
        str(input_dir / "3pbl_demo_far.png"),
        output_dir=str(output_dir),
        interaction_dir=str(interaction_dir),
        debug=False,
        dpi=120,
    )

    assert ok is True
    out_path = output_dir / "3pbl_demo_final.png"
    assert out_path.exists()

    with Image.open(out_path) as image_obj:
        rgba = image_obj.convert("RGBA")
        assert rgba.getpixel((0, 0))[3] == 0
        assert any(pixel[3] == 0 for pixel in rgba.getdata())
        assert any(pixel[3] > 0 and max(pixel[:3]) > 200 for pixel in rgba.getdata())
        assert image_obj.info.get("dpi") == pytest.approx((120, 120), rel=0.01)


def test_trim_transparent_content_reduces_empty_canvas() -> None:
    image = np.zeros((120, 240, 4), dtype=np.uint8)
    image[42:68, 96:138] = (255, 0, 0, 255)

    cropped = create_visualization._trim_transparent_content(image, padding=8)

    assert cropped.shape[0] < image.shape[0]
    assert cropped.shape[1] < image.shape[1]
    assert cropped.shape[2] == 4
    assert cropped[:, :, 3].max() == 255


def test_content_bbox_detects_visible_region_inside_transparent_far_panel() -> None:
    image = np.zeros((180, 320, 4), dtype=np.uint8)
    image[48:136, 92:236] = (220, 225, 244, 180)

    bbox = create_visualization._content_bbox(image, padding=10)

    assert bbox is not None
    left, top, right, bottom = bbox
    assert left < 92
    assert top < 48
    assert right > 236
    assert bottom > 136
    assert (right - left) < image.shape[1]
    assert (bottom - top) < image.shape[0]


def test_find_rgb_regions_supports_tighter_far_focus_defaults() -> None:
    image = np.zeros((250, 333, 4), dtype=np.uint8)
    image[118:130, 160:172] = (255, 120, 120, 255)

    x, y, size, _ = create_visualization.find_rgb_regions(
        image,
        padding_percent=create_visualization.FAR_BOX_PADDING_PERCENT,
        min_focus_ratio=create_visualization.FAR_BOX_MIN_FOCUS_RATIO,
        min_focus_px=create_visualization.FAR_BOX_MIN_FOCUS_PX,
    )

    assert size <= 30
    assert x >= 150
    assert y >= 108


def test_find_rgb_regions_centers_square_on_mask_centroid_not_bbox_midpoint() -> None:
    image = np.zeros((200, 200, 4), dtype=np.uint8)
    image[90:110, 92:112] = (255, 120, 120, 255)
    image[100:140, 92:100] = (255, 120, 120, 255)

    x, y, size, _ = create_visualization.find_rgb_regions(
        image,
        padding_percent=0.0,
        min_focus_ratio=0.0,
        min_focus_px=20,
    )

    assert size >= 39
    box_center_x = x + (size / 2.0)
    box_center_y = y + (size / 2.0)
    # The asymmetric tail shifts the true center away from the bbox midpoint.
    assert box_center_x == pytest.approx(100.0, abs=1.5)
    assert box_center_y == pytest.approx(109.5, abs=1.5)


def test_create_visualization_supports_white_background_and_custom_ratios(tmp_path: Path) -> None:
    input_dir = tmp_path / "results"
    output_dir = tmp_path / "final_results"
    interaction_dir = tmp_path / "interaction"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    interaction_dir.mkdir(parents=True, exist_ok=True)

    _make_transparent_panel(
        input_dir / "6cm4_demo_far.png",
        size=(420, 300),
        ligand_box=(180, 90, 250, 180),
    )
    _make_transparent_panel(
        input_dir / "6cm4_demo_close.png",
        size=(300, 300),
        ligand_box=(95, 55, 205, 245),
    )

    ok = create_visualization.create_visualization(
        str(input_dir / "6cm4_demo_far.png"),
        output_dir=str(output_dir),
        interaction_dir=str(interaction_dir),
        debug=False,
        dpi=120,
        far_ratio=5,
        close_ratio=2,
        interaction_ratio=4,
        background_mode="white",
    )

    assert ok is True
    out_path = output_dir / "6cm4_demo_final.png"
    assert out_path.exists()

    with Image.open(out_path) as image_obj:
        rgba = image_obj.convert("RGBA")
        assert rgba.getpixel((0, 0))[3] == 255
        assert image_obj.size[0] > image_obj.size[1]
