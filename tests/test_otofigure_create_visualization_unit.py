from __future__ import annotations

import sys
from pathlib import Path

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
    )

    assert ok is True
    out_path = output_dir / "3pbl_demo_final.png"
    assert out_path.exists()

    with Image.open(out_path) as image_obj:
        rgba = image_obj.convert("RGBA")
        assert rgba.getpixel((0, 0))[3] == 0
        assert any(pixel[3] == 0 for pixel in rgba.getdata())
        assert any(pixel[3] > 0 and max(pixel[:3]) > 200 for pixel in rgba.getdata())
