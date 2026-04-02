from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageColor, ImageDraw, ImageFont

from .pipeline import (
    FINAL_DINAMIK_SCRIPT,
    NORMAL_BASE_RENDER_DPI,
    PREVIEW_BASE_RENDER_DPI,
    RENDER_INTERACTION_MAPS_SCRIPT,
    _find_python_with_modules,
    _render_settings,
    _run_step,
)


def _load_site_rows(run_dir: Path) -> list[dict[str, Any]]:
    sites_path = run_dir / "multi_ligand" / "sites.json"
    if not sites_path.exists():
        raise FileNotFoundError(f"Missing multi-ligand site index: {sites_path}")
    payload = json.loads(sites_path.read_text(encoding="utf-8"))
    rows = payload.get("sites", []) if isinstance(payload, dict) else []
    site_rows = [row for row in rows if isinstance(row, dict)]
    if len(site_rows) < 2:
        raise FileNotFoundError(f"Expected at least 2 site entries in {sites_path}")
    return site_rows[:2]


def _copy_case_inputs(work_dir: Path, *, receptor_id: str, run_dir: Path, site_rows: list[dict[str, Any]]) -> dict[str, Path]:
    protein_dir = work_dir / "protein"
    ligands_dir = work_dir / "ligands"
    results_dir = work_dir / "results"
    interaction_dir = work_dir / "interaction"
    final_results_dir = work_dir / "final_results"
    for directory in (protein_dir, ligands_dir, results_dir, interaction_dir, final_results_dir):
        directory.mkdir(parents=True, exist_ok=True)

    protein_candidates = sorted(run_dir.glob("*_rec_raw.pdb"))
    if not protein_candidates:
        protein_candidates = sorted(run_dir.glob("*_complex.pdb"))
    if not protein_candidates:
        raise FileNotFoundError(f"Missing receptor pdb in {run_dir}")
    shutil.copy2(protein_candidates[0], protein_dir / f"{str(receptor_id or '').lower()}.pdb")

    for site in site_rows:
        site_id = str(site.get("site_id") or "").strip() or "site"
        pose_rel = str(site.get("pose_rel") or site.get("pose_path") or "").strip()
        pose_path = Path(pose_rel)
        if not pose_path.is_absolute():
            pose_path = (run_dir / pose_path).resolve()
        else:
            pose_path = pose_path.resolve()
        if not pose_path.exists():
            raise FileNotFoundError(f"Missing pose PDB for {site_id}: {pose_path}")
        shutil.copy2(pose_path, ligands_dir / f"{site_id}.pdb")

    return {
        "protein_dir": protein_dir,
        "ligands_dir": ligands_dir,
        "results_dir": results_dir,
        "interaction_dir": interaction_dir,
        "final_results_dir": final_results_dir,
    }


def _find_first_image(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"Missing {pattern} in {directory}")
    return matches[0]


def _load_font(size: int) -> ImageFont.ImageFont:
    for candidate in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_image(image: Image.Image, box_width: int, box_height: int) -> Image.Image:
    scale = min(float(box_width) / float(image.width), float(box_height) / float(image.height))
    width = max(1, int(round(image.width * scale)))
    height = max(1, int(round(image.height * scale)))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def _compose_multi_panel(
    *,
    far_image_path: Path,
    close_image_path: Path,
    interaction_paths: list[tuple[str, Path]],
    output_png: Path,
    dpi: int,
    background_mode: str,
    far_ratio: int,
    close_ratio: int,
    interaction_ratio: int,
) -> None:
    far_img = Image.open(far_image_path).convert("RGBA")
    close_img = Image.open(close_image_path).convert("RGBA")
    interaction_images = [(label, Image.open(path).convert("RGBA")) for label, path in interaction_paths]

    canvas_height = max(far_img.height, close_img.height, max(img.height for _, img in interaction_images))
    left_width = max(1, int(round(canvas_height * (far_ratio / 2.4))))
    middle_width = max(1, int(round(canvas_height * (close_ratio / 2.4))))
    interaction_box_width = max(1, int(round(canvas_height * (interaction_ratio / 2.9))))
    padding = max(24, int(round(canvas_height * 0.03)))
    gap = max(18, int(round(canvas_height * 0.025)))
    header_height = max(48, int(round(canvas_height * 0.08)))
    right_width = interaction_box_width * 2 + gap
    canvas_width = padding * 2 + left_width + gap + middle_width + gap + right_width

    background = (255, 255, 255, 0) if background_mode == "transparent" else (*ImageColor.getrgb("#ffffff"), 255)
    canvas = Image.new("RGBA", (canvas_width, canvas_height + header_height + padding * 2), background)
    draw = ImageDraw.Draw(canvas)
    label_font = _load_font(max(18, int(round(canvas_height * 0.035))))
    header_font = _load_font(max(16, int(round(canvas_height * 0.03))))
    ink = (34, 40, 49, 255)
    sub_ink = (71, 85, 105, 255)

    content_top = padding + header_height
    content_height = canvas_height
    far_box = (padding, content_top, padding + left_width, content_top + content_height)
    close_box = (far_box[2] + gap, content_top, far_box[2] + gap + middle_width, content_top + content_height)
    right_left = close_box[2] + gap
    interaction_boxes = [
        (right_left, content_top + header_height // 2, right_left + interaction_box_width, content_top + content_height),
        (right_left + interaction_box_width + gap, content_top + header_height // 2, right_left + interaction_box_width * 2 + gap, content_top + content_height),
    ]

    for title, target_box in (("Far View", far_box), ("Close View", close_box)):
        draw.text((target_box[0], padding + 6), title, font=label_font, fill=ink)
    for (label, _img), target_box in zip(interaction_images, interaction_boxes):
        draw.text((target_box[0], content_top - header_height // 2), label, font=header_font, fill=sub_ink)

    for image, target_box in ((far_img, far_box), (close_img, close_box)):
        fitted = _fit_image(image, target_box[2] - target_box[0], target_box[3] - target_box[1])
        offset_x = target_box[0] + max(0, ((target_box[2] - target_box[0]) - fitted.width) // 2)
        offset_y = target_box[1] + max(0, ((target_box[3] - target_box[1]) - fitted.height) // 2)
        canvas.alpha_composite(fitted, (offset_x, offset_y))

    for (label, image), target_box in zip(interaction_images, interaction_boxes):
        fitted = _fit_image(image, target_box[2] - target_box[0], target_box[3] - target_box[1])
        offset_x = target_box[0] + max(0, ((target_box[2] - target_box[0]) - fitted.width) // 2)
        offset_y = target_box[1] + max(0, ((target_box[3] - target_box[1]) - fitted.height) // 2)
        canvas.alpha_composite(fitted, (offset_x, offset_y))
        draw.rounded_rectangle(target_box, radius=12, outline=(203, 213, 225, 255), width=2)

    output_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_png, dpi=(dpi, dpi))


def run(
    *,
    receptor_id: str,
    ligand_name: str,
    run_dir: str | Path,
    output_png: str | Path,
    work_dir: str | Path,
    dpi: int = 120,
    style_preset: str = "balanced",
    ray_trace: bool = True,
    options: dict[str, Any] | None = None,
    preview_mode: bool = False,
    on_process_start=None,
    on_process_end=None,
) -> dict[str, Any]:
    resolved_run_dir = Path(run_dir).resolve()
    render_options = dict(options or {})
    render_engine = str(render_options.get("render_engine") or ("ray" if bool(ray_trace) else "opengl")).strip().lower()
    if render_engine not in {"ray", "opengl", "fast_draw"}:
        render_engine = "ray" if bool(ray_trace) else "opengl"
    background_mode = str(render_options.get("background") or "transparent").strip().lower()
    if background_mode not in {"transparent", "white"}:
        background_mode = "transparent"
    surface_enabled = bool(render_options.get("surface_enabled", True))
    surface_opacity = max(0.0, min(1.0, float(render_options.get("surface_opacity", 0.50))))
    protein_color = str(render_options.get("protein_color") or "bluewhite").strip() or "bluewhite"
    ligand_thickness = max(0.05, min(0.8, float(render_options.get("ligand_thickness", 0.22))))
    far_padding = max(0.0, min(0.5, float(render_options.get("far_padding", 0.03))))
    close_padding = max(0.0, min(1.0, float(render_options.get("close_padding", 0.20))))
    far_ratio = max(1, min(9, int(round(float(render_options.get("far_ratio", 4))))))
    close_ratio = max(1, min(9, int(round(float(render_options.get("close_ratio", 2))))))
    interaction_ratio = max(1, min(9, int(round(float(render_options.get("interaction_ratio", 3))))))

    work_root = Path(work_dir).resolve()
    if work_root.exists():
        shutil.rmtree(work_root, ignore_errors=True)
    work_root.mkdir(parents=True, exist_ok=True)

    site_rows = _load_site_rows(resolved_run_dir)
    layout = _copy_case_inputs(work_root, receptor_id=receptor_id, run_dir=resolved_run_dir, site_rows=site_rows)
    width, height, render_dpi = _render_settings(dpi, preview_mode=preview_mode)

    pymol_python = _find_python_with_modules(["pymol"], env_var="DOCKUP_OTOFIGURE_PYMOL_PYTHON")
    interaction_python = _find_python_with_modules(["rdkit", "PIL"], env_var="DOCKUP_OTOFIGURE_VIZ_PYTHON")

    env = os.environ.copy()
    env.setdefault("MPLBACKEND", "Agg")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["MPLCONFIGDIR"] = str((work_root / ".matplotlib").resolve())

    interaction_manifest = work_root / "interaction_manifest.json"
    interaction_manifest.write_text(
        json.dumps(
            [
                {
                    "receptor_id": str(receptor_id or "").lower(),
                    "run_name": str(site.get("site_id") or f"site_{idx + 1}"),
                    "run_dir": str(
                        (
                            resolved_run_dir
                            / str(site.get("site_dir_rel") or Path(str(site.get("site_dir") or "")).name)
                        ).resolve()
                    ),
                }
                for idx, site in enumerate(site_rows)
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    logs: dict[str, str] = {}
    logs["final_dinamik"] = _run_step(
        [
            pymol_python,
            str(FINAL_DINAMIK_SCRIPT),
            "--pdb_id",
            str(receptor_id or "").lower(),
            "--ligands_dir",
            str(layout["ligands_dir"]),
            "--output_dir",
            str(layout["results_dir"]),
            "--width",
            str(width),
            "--height",
            str(height),
            "--dpi",
            str(render_dpi),
            "--style-preset",
            str(style_preset or "balanced"),
            "--ray-trace",
            "1" if bool(ray_trace) else "0",
            "--render-engine",
            render_engine,
            "--background",
            background_mode,
            "--surface-mode",
            "1" if surface_enabled else "0",
            "--surface-opacity",
            str(surface_opacity),
            "--protein-color",
            protein_color,
            "--ligand-thickness",
            str(ligand_thickness),
            "--far-padding",
            str(far_padding),
            "--close-padding",
            str(close_padding),
        ],
        cwd=work_root,
        env=env,
        on_process_start=on_process_start,
        on_process_end=on_process_end,
    )
    logs["interaction_maps"] = _run_step(
        [
            interaction_python,
            str(RENDER_INTERACTION_MAPS_SCRIPT),
            "--manifest",
            str(interaction_manifest),
            "--output_dir",
            str(layout["interaction_dir"]),
            "--width",
            str(max(1400, width * 4)),
            "--height",
            str(max(900, height * 3)),
        ],
        cwd=work_root,
        env=env,
        on_process_start=on_process_start,
        on_process_end=on_process_end,
    )

    far_image = _find_first_image(layout["results_dir"], "*_far.png")
    close_image = _find_first_image(layout["results_dir"], "*_close.png")
    interaction_paths = []
    for site in site_rows:
        site_id = str(site.get("site_id") or "").strip()
        label = str(site.get("ligand_display_name") or site.get("ligand_source_name") or site_id).strip() or site_id
        image_path = layout["interaction_dir"] / f"{str(receptor_id or '').lower()}_{site_id}_interaction.png"
        if not image_path.exists():
            raise FileNotFoundError(f"Missing interaction image for {site_id}: {image_path}")
        interaction_paths.append((label, image_path))

    target_png = Path(output_png).resolve()
    _compose_multi_panel(
        far_image_path=far_image,
        close_image_path=close_image,
        interaction_paths=interaction_paths,
        output_png=target_png,
        dpi=render_dpi if not preview_mode else min(render_dpi, PREVIEW_BASE_RENDER_DPI if preview_mode else NORMAL_BASE_RENDER_DPI),
        background_mode=background_mode,
        far_ratio=far_ratio,
        close_ratio=close_ratio,
        interaction_ratio=interaction_ratio,
    )

    return {
        "final_png": str(target_png),
        "used_runs": [resolved_run_dir.name],
        "ligand_name": ligand_name,
        "render_dpi": render_dpi,
        "render_width": width,
        "render_height": height,
        "site_count": len(site_rows),
        "logs": logs,
    }
