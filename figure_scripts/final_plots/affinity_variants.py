"""
Affinity Boxplot with Summary Table (Stitched Layout)
Generates: plots/affinity_boxplot.png
"""
from __future__ import annotations

import argparse
import io
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

from .dataset_utils import collect_inventory, inventory_entities, load_source_metadata

LEGACY_LIGAND_COLORS: dict[str, str] = {
    "PET_1": "#66C2A5",
    "PS_1": "#FC8D62",
    "PP_1": "#8DA0CB",
    "PE_1": "#E78AC3",
}
LEGACY_LIGAND_COLOR_ORDER: tuple[str, ...] = ("#66C2A5", "#FC8D62", "#8DA0CB", "#E78AC3")
LEGACY_RECEPTOR_COLORS: dict[str, str] = {
    "D1": "#1B9E77",
    "D2": "#D95F02",
    "D3": "#7570B3",
    "D4": "#E7298A",
    "D5": "#66A61E",
}
BOX_EDGE = "#333333"


@dataclass(frozen=True)
class Obs:
    receptor: str
    ligand: str
    run: str
    affinity: float


@dataclass(frozen=True)
class Stats:
    mean: float
    sd: float


def find_results_json(run_dir: Path) -> Path | None:
    candidates = [
        run_dir / "results.json",
        run_dir / "docking" / "results.json",
        run_dir / "dock_results.json",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def affinity_from_results_json(path: Path) -> float | None:
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return None
        data = json.loads(text)
        if not data:
            return None

        first_item = next(iter(data.values()), None) if isinstance(data, dict) else None
        if isinstance(first_item, dict):
            raw_value = first_item.get("best_affinity")
        elif isinstance(data, dict):
            raw_value = data.get("best_affinity")
        else:
            raw_value = None

        if raw_value is None:
            return None
        value = float(raw_value)
        if math.isnan(value):
            return None
        return value
    except Exception:
        return None


def collect_obs(results_root: Path) -> tuple[list[Obs], list[str], list[str], object]:
    inventory = collect_inventory(results_root, required_files=())
    detected_receptors, detected_ligands = inventory_entities(inventory)
    metadata = load_source_metadata(results_root, detected_receptors, detected_ligands)
    receptors = list(metadata.receptor_order)
    ligands = list(metadata.ligand_order)

    out: list[Obs] = []
    for receptor_id in receptors:
        for ligand_name in ligands:
            run_entries = (inventory.get(receptor_id) or {}).get(ligand_name) or []
            for run_name, run_dir in run_entries:
                result_json = find_results_json(run_dir)
                if not result_json:
                    continue
                affinity = affinity_from_results_json(result_json)
                if affinity is None:
                    continue
                out.append(
                    Obs(
                        receptor=receptor_id,
                        ligand=ligand_name,
                        run=run_name,
                        affinity=affinity,
                    )
                )
    return out, receptors, ligands, metadata


def compute_stats_table(obs: list[Obs], receptors: list[str], ligands: list[str]) -> dict[str, dict[str, Stats]]:
    grouped = {receptor_id: {ligand_name: [] for ligand_name in ligands} for receptor_id in receptors}
    for row in obs:
        grouped[row.receptor][row.ligand].append(row.affinity)

    stats_out: dict[str, dict[str, Stats]] = {receptor_id: {} for receptor_id in receptors}
    for receptor_id in receptors:
        for ligand_name in ligands:
            values = grouped[receptor_id][ligand_name]
            if not values:
                stats_out[receptor_id][ligand_name] = Stats(mean=math.nan, sd=math.nan)
            else:
                sd = statistics.stdev(values) if len(values) >= 2 else 0.0
                stats_out[receptor_id][ligand_name] = Stats(mean=float(np.mean(values)), sd=sd)
    return stats_out


def _crop_white_border(img: Image.Image, threshold: int = 250) -> Image.Image:
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    arr = np.array(img)
    rgb = arr[..., :3]
    alpha = arr[..., 3]
    mask = (alpha > 0) & (np.min(rgb, axis=-1) < threshold)

    if not mask.any():
        return img

    ys, xs = np.where(mask)
    y0, y1 = ys.min(), ys.max() + 1
    x0, x1 = xs.min(), xs.max() + 1
    return img.crop((x0, y0, x1, y1))


def _fmt(mean_val: float, sd_val: float) -> str:
    if math.isnan(mean_val) or math.isnan(sd_val):
        return "n/a"
    return f"{mean_val:.2f} ± {sd_val:.2f}"


def _make_table(ax: plt.Axes, stats: dict[str, dict[str, Stats]], receptors: list[str], ligands: list[str], metadata) -> None:
    ax.axis("off")
    col_labels = ["Receptor"] + [metadata.ligand_display(ligand_name) for ligand_name in ligands]
    rows = []
    for receptor_id in receptors:
        row = [metadata.receptor_display(receptor_id)]
        for ligand_name in ligands:
            stat = stats[receptor_id][ligand_name]
            row.append(_fmt(stat.mean, stat.sd))
        rows.append(row)

    table = ax.table(cellText=rows, colLabels=col_labels, cellLoc="center", colLoc="center", loc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(8.4)
    table.scale(1.0, 1.22)

    for (row_idx, _col_idx), cell in table.get_celld().items():
        cell.set_linewidth(0.6)
        cell.set_edgecolor("#444444")
        if row_idx == 0:
            cell.set_facecolor("#e9eef6")
            cell.set_text_props(weight="bold")
        else:
            cell.set_facecolor("white")


def _flatten_axes(axes):
    if isinstance(axes, np.ndarray):
        return list(axes.reshape(-1))
    return [axes]


def generate_boxplot_image(obs: list[Obs], receptors: list[str], ligands: list[str], metadata) -> Image.Image:
    plt.rcParams["font.family"] = ["Times New Roman", "DejaVu Serif", "serif"]
    plt.rcParams["axes.facecolor"] = "white"
    plt.rcParams["figure.facecolor"] = "white"

    if not receptors or not ligands:
        fig = plt.figure(figsize=(7.2, 3.0))
        fig.text(0.5, 0.5, "No receptor/ligand data found", ha="center", va="center", fontsize=12)
        buf = io.BytesIO()
        fig.savefig(buf, dpi=300, facecolor="white", bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return Image.open(buf)

    data = {receptor_id: {ligand_name: [] for ligand_name in ligands} for receptor_id in receptors}
    all_values: list[float] = []
    for row in obs:
        data[row.receptor][row.ligand].append(row.affinity)
        all_values.append(row.affinity)

    if all_values:
        y_min = min(all_values) - 1.0
        y_max = max(all_values) + 1.0
    else:
        y_min, y_max = -12.0, -2.0

    n_ligands = len(ligands)
    if n_ligands <= 2:
        ncols = n_ligands
    elif n_ligands <= 4:
        ncols = 2
    else:
        ncols = 3
    nrows = (n_ligands + ncols - 1) // ncols

    if n_ligands == 4 and ncols == 2 and nrows == 2:
        fig_width, fig_height = 7.4, 6.6
    else:
        fig_width, fig_height = 3.65 * ncols + 0.5, 2.8 * nrows + 1.0
    fig, axes = plt.subplots(nrows, ncols, figsize=(fig_width, fig_height), squeeze=False)
    axes_flat = _flatten_axes(axes)

    ligand_colors: dict[str, str] = {}
    for idx, ligand_name in enumerate(ligands):
        ligand_colors[ligand_name] = LEGACY_LIGAND_COLORS.get(
            ligand_name,
            LEGACY_LIGAND_COLOR_ORDER[idx % len(LEGACY_LIGAND_COLOR_ORDER)],
        )

    receptor_order = list(receptors)
    receptor_colors: dict[str, tuple[float, float, float, float] | str] = {}
    fallback_cmap = plt.get_cmap("tab20")
    fallback_idx = 0
    for receptor_id in receptor_order:
        if receptor_id in LEGACY_RECEPTOR_COLORS:
            receptor_colors[receptor_id] = LEGACY_RECEPTOR_COLORS[receptor_id]
        else:
            receptor_colors[receptor_id] = fallback_cmap(fallback_idx % 20)
            fallback_idx += 1

    panel_labels = [chr(ord("A") + idx) for idx in range(n_ligands)]
    receptor_ticks = [metadata.receptor_display(receptor_id) for receptor_id in receptor_order]

    for idx, ligand_name in enumerate(ligands):
        ax = axes_flat[idx]
        values_list = [data[receptor_id][ligand_name] for receptor_id in receptor_order]
        positions = np.arange(1, len(receptor_order) + 1)

        boxplot = ax.boxplot(
            values_list,
            positions=positions,
            widths=0.55,
            patch_artist=True,
            showfliers=False,
            medianprops={"color": BOX_EDGE, "linewidth": 1.2},
            boxprops={"linewidth": 1.0, "color": BOX_EDGE},
            whiskerprops={"linewidth": 1.0, "color": BOX_EDGE},
            capprops={"linewidth": 1.0, "color": BOX_EDGE},
        )
        for box in boxplot["boxes"]:
            box.set_facecolor(ligand_colors[ligand_name])
            box.set_alpha(0.55)
            box.set_edgecolor(BOX_EDGE)

        for pos, y_values, receptor_id in zip(positions, values_list, receptor_order):
            if not y_values:
                continue
            if len(y_values) <= 1:
                offsets = np.array([0.0])
            else:
                offsets = (np.arange(len(y_values)) - (len(y_values) - 1) / 2.0) * 0.08
            ax.scatter(
                pos + offsets,
                y_values,
                s=22,
                color=receptor_colors[receptor_id],
                alpha=0.88,
                linewidths=0.5,
                edgecolors="black",
                zorder=3,
            )

        ax.set_title(metadata.ligand_display(ligand_name), fontsize=11)
        ax.set_xticks(positions)
        ax.set_xticklabels(receptor_ticks)
        ax.set_ylim(y_min, y_max)
        ax.invert_yaxis()
        ax.grid(axis="y", alpha=0.5, color="#d0d0d0", linewidth=0.7)
        ax.text(
            -0.12,
            1.04,
            panel_labels[idx],
            transform=ax.transAxes,
            ha="left",
            va="bottom",
            fontsize=12,
            fontweight="bold",
            color="black",
            clip_on=False,
        )
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)

    for idx in range(n_ligands, len(axes_flat)):
        axes_flat[idx].axis("off")

    for row_idx in range(nrows):
        left = row_idx * ncols
        if left < len(axes_flat) and axes_flat[left].has_data():
            axes_flat[left].set_ylabel("Affinity (kcal/mol)")

    fig.subplots_adjust(left=0.08, right=0.99, top=0.9, bottom=0.08, wspace=0.35, hspace=0.4)

    buf = io.BytesIO()
    fig.savefig(buf, dpi=300, facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf)


def generate_final_stitch(obs: list[Obs], boxplot_img: Image.Image, receptors: list[str], ligands: list[str], metadata, out_path: Path) -> None:
    stats = compute_stats_table(obs, receptors, ligands)

    fig_height = 10.0 if len(ligands) <= 4 else 11.2
    fig = plt.figure(figsize=(9.0, fig_height), constrained_layout=False)
    gs = fig.add_gridspec(2, 1, height_ratios=[3.1, 1.12], hspace=0.02)
    ax_plot = fig.add_subplot(gs[0, 0])
    ax_table = fig.add_subplot(gs[1, 0])

    _make_table(ax_table, stats, receptors, ligands, metadata)

    cropped = _crop_white_border(boxplot_img, threshold=250)
    ax_plot.imshow(np.array(cropped))
    ax_plot.axis("off")

    fig.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01, hspace=0.01, wspace=0.0)
    fig.savefig(out_path, dpi=400, facecolor="white", bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("monomer_final/results"))
    parser.add_argument("--out", type=Path, default=Path("plots"))
    args = parser.parse_args()

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    observations, receptors, ligands, metadata = collect_obs(args.root)
    if not observations:
        print("WARNING: No affinity values found; writing placeholder figure.")

    boxplot_image = generate_boxplot_image(observations, receptors, ligands, metadata)
    out_path = out_dir / "affinity_boxplot.png"
    generate_final_stitch(observations, boxplot_image, receptors, ligands, metadata, out_path)
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
