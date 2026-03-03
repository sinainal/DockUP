"""
Common Residue Heatmap
Generates: plots/common_residue_heatmap.png
"""
from __future__ import annotations

import argparse
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch

from .dataset_utils import collect_inventory, inventory_entities, load_source_metadata

KIND_ORDER: tuple[str, ...] = (
    "hydrophobic_interaction",
    "hydrogen_bond",
    "salt_bridge",
    "pi_stack",
    "pi_cation_interaction",
    "halogen_bond",
    "water_bridge",
    "metal_complex",
)
KIND_LABELS: dict[str, str] = {
    "hydrophobic_interaction": "Hydrophobic",
    "hydrogen_bond": "H-bond",
    "salt_bridge": "Salt bridge",
    "pi_stack": "Pi-stack",
    "pi_cation_interaction": "Pi-cation",
    "halogen_bond": "Halogen bond",
    "water_bridge": "Water bridge",
    "metal_complex": "Metal complex",
}


@dataclass(frozen=True)
class ReceptorMatrix:
    residues: list[str]
    types: list[list[str | None]]


def _residue_label(restype: str, resid: str, chain: str) -> str:
    return f"{restype}{resid}{chain}"


def _residue_sort_key(label: str) -> tuple[str, int, str]:
    match = re.match(r"^([A-Za-z]+)(\d+)([A-Za-z_]*)$", label)
    if match:
        return (match.group(1), int(match.group(2)), match.group(3))
    return (label, 10**9, "")


def _parse_plip_types(report_xml: Path) -> dict[tuple[str, str, str], list[str]]:
    if not report_xml.exists():
        return {}
    try:
        tree = ET.parse(report_xml)
    except ET.ParseError:
        return {}
    root = tree.getroot()
    out: dict[tuple[str, str, str], list[str]] = {}
    for inter in root.findall(".//bindingsite/interactions//*"):
        kind = inter.tag
        resnr = (inter.findtext("resnr") or "").strip()
        restype = (inter.findtext("restype") or "").strip()
        reschain = (inter.findtext("reschain") or "").strip()
        if resnr and restype and reschain:
            key = (restype, resnr, reschain)
            out.setdefault(key, []).append(kind)
    return out


def _choose_type(counts: Counter[str]) -> str | None:
    if not counts:
        return None
    max_count = max(counts.values())
    candidates = [k for k, v in counts.items() if v == max_count]
    for kind in KIND_ORDER:
        if kind in candidates:
            return kind
    return sorted(candidates)[0]


def collect_common_residue_types(results_root: Path) -> tuple[dict[str, ReceptorMatrix], list[str], list[str], object]:
    inventory = collect_inventory(results_root, required_files=("plip/report.xml",))
    detected_receptors, detected_ligands = inventory_entities(inventory)
    metadata = load_source_metadata(results_root, detected_receptors, detected_ligands)
    receptors = list(metadata.receptor_order)
    ligands = list(metadata.ligand_order)

    out: dict[str, ReceptorMatrix] = {}
    for receptor_id in receptors:
        ligand_runs = inventory.get(receptor_id) or {}
        residue_union: set[str] = set()
        ligand_maps: dict[str, dict[str, str]] = {}

        for ligand_name in ligands:
            runs = ligand_runs.get(ligand_name) or []
            run_maps = []
            for _run_name, run_dir in runs:
                parsed = _parse_plip_types(run_dir / "plip" / "report.xml")
                if parsed:
                    run_maps.append(parsed)

            if not run_maps:
                ligand_maps[ligand_name] = {}
                continue

            run_sets = [set(row.keys()) for row in run_maps]
            common_residues = set.intersection(*run_sets) if run_sets else set()
            chosen_by_residue: dict[str, str] = {}

            for residue_key in common_residues:
                counts: Counter[str] = Counter()
                for run_map in run_maps:
                    for kind in run_map.get(residue_key, []):
                        counts[kind] += 1
                chosen = _choose_type(counts)
                if not chosen:
                    continue
                label = _residue_label(*residue_key)
                chosen_by_residue[label] = chosen

            ligand_maps[ligand_name] = chosen_by_residue
            residue_union.update(chosen_by_residue.keys())

        residues_sorted = sorted(residue_union, key=_residue_sort_key)
        matrix: list[list[str | None]] = []
        for residue in residues_sorted:
            row: list[str | None] = []
            for ligand_name in ligands:
                row.append((ligand_maps.get(ligand_name) or {}).get(residue))
            matrix.append(row)
        out[receptor_id] = ReceptorMatrix(residues=residues_sorted, types=matrix)

    return out, receptors, ligands, metadata


def _types_in_data(data: dict[str, ReceptorMatrix]) -> list[str]:
    types: set[str] = set()
    for receptor_matrix in data.values():
        for row in receptor_matrix.types:
            for kind in row:
                if kind:
                    types.add(kind)
    ordered = [kind for kind in KIND_ORDER if kind in types]
    for kind in sorted(types):
        if kind not in ordered:
            ordered.append(kind)
    return ordered


def _build_cmap(types: list[str], palette: list[str]) -> tuple[ListedColormap, BoundaryNorm, dict[str, int]]:
    colors = ["#ffffff"]
    kind_to_idx: dict[str, int] = {}
    for idx, kind in enumerate(types):
        colors.append(palette[idx % len(palette)])
        kind_to_idx[kind] = idx + 1
    cmap = ListedColormap(colors)
    norm = BoundaryNorm(range(len(colors) + 1), cmap.N)
    return cmap, norm, kind_to_idx


def plot_common_heatmap(
    data: dict[str, ReceptorMatrix],
    receptors: list[str],
    ligands: list[str],
    metadata,
    out_path: Path,
) -> None:
    if not receptors or not ligands:
        fig = plt.figure(figsize=(7.0, 3.0))
        fig.text(0.5, 0.5, "No receptor/ligand data found", ha="center", va="center", fontsize=12)
        fig.savefig(out_path, dpi=300, facecolor="white", bbox_inches="tight")
        plt.close(fig)
        return

    palette = ["#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F", "#EDC948", "#B07AA1", "#9C755F"]
    plt.rcParams["font.family"] = ["Times New Roman", "DejaVu Serif", "serif"]
    plt.rcParams["axes.facecolor"] = "white"
    plt.rcParams["figure.facecolor"] = "white"

    types = _types_in_data(data)
    cmap, norm, type_to_idx = _build_cmap(types, palette)

    max_rows = max((len(data.get(rec, ReceptorMatrix([], [])).residues) for rec in receptors), default=0)
    fig_width = max(8.0, 2.2 * len(receptors) + 1.0)
    fig_height = max(4.2, min(14.0, 2.2 + 0.18 * max_rows))
    fig, axes = plt.subplots(1, len(receptors), figsize=(fig_width, fig_height), sharey=False)
    if len(receptors) == 1:
        axes = [axes]

    ligand_ticks = [metadata.ligand_display(ligand_name) for ligand_name in ligands]

    for ax, receptor_id in zip(axes, receptors):
        receptor_matrix = data.get(receptor_id, ReceptorMatrix([], []))
        if not receptor_matrix.residues:
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_title(metadata.receptor_display(receptor_id), fontsize=11)
            for spine in ax.spines.values():
                spine.set_visible(False)
            continue

        mat = np.zeros((len(receptor_matrix.residues), len(ligands)), dtype=int)
        for row_idx, row in enumerate(receptor_matrix.types):
            for col_idx, kind in enumerate(row):
                if kind:
                    mat[row_idx, col_idx] = type_to_idx[kind]

        ax.imshow(mat, aspect="auto", cmap=cmap, norm=norm)
        ax.set_title(metadata.receptor_display(receptor_id), fontsize=11)
        ax.set_xticks(range(len(ligands)))
        ax.set_xticklabels(ligand_ticks, rotation=45, ha="right")
        ax.set_yticks(range(len(receptor_matrix.residues)))
        ax.set_yticklabels(receptor_matrix.residues, fontsize=8)
        ax.tick_params(axis="y", length=0)
        ax.tick_params(axis="x", length=0)
        for spine in ax.spines.values():
            spine.set_visible(False)

        ax.set_xticks(np.arange(-0.5, len(ligands), 1), minor=True)
        ax.set_yticks(np.arange(-0.5, len(receptor_matrix.residues), 1), minor=True)
        ax.grid(which="minor", color="#e6e6e6", linestyle="-", linewidth=0.4)
        ax.tick_params(which="minor", bottom=False, left=False)

    if types:
        legend_handles = [Patch(facecolor=palette[idx % len(palette)], edgecolor="none") for idx in range(len(types))]
        legend_labels = [KIND_LABELS.get(kind, kind.replace("_", " ")) for kind in types]
        fig.legend(
            handles=legend_handles,
            labels=legend_labels,
            loc="lower center",
            ncol=min(4, len(legend_labels)),
            frameon=False,
            fontsize=9,
            bbox_to_anchor=(0.5, -0.01),
        )

    fig.subplots_adjust(left=0.05, right=0.995, top=0.93, bottom=0.17, wspace=0.32)
    fig.savefig(out_path, dpi=400, facecolor="white", bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("monomer_final/results"))
    parser.add_argument("--out", type=Path, default=Path("plots"))
    args = parser.parse_args()

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    data, receptors, ligands, metadata = collect_common_residue_types(args.root)
    out_path = out_dir / "common_residue_heatmap.png"
    plot_common_heatmap(data, receptors, ligands, metadata, out_path)
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
