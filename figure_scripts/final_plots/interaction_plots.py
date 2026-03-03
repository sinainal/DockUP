"""
Interaction Stacked Bar Plot
Generates: plots/interaction_stacked_bar.png
"""
from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
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
    "hydrogen_bond": "H-bond",
    "hydrophobic_interaction": "Hydrophobic",
    "pi_stack": "Pi-stacking",
    "pi_cation_interaction": "Pi-cation",
    "salt_bridge": "Salt bridge",
    "halogen_bond": "Halogen bond",
    "water_bridge": "Water bridge",
    "metal_complex": "Metal complex",
}


@dataclass(frozen=True)
class InteractionRow:
    receptor: str
    ligand: str
    kind: str
    residue: str


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


def _choose_dominant_kind(counts: dict[str, int]) -> str | None:
    if not counts:
        return None
    max_count = max(counts.values())
    candidates = [kind for kind, count in counts.items() if count == max_count]
    for kind in KIND_ORDER:
        if kind in candidates:
            return kind
    return sorted(candidates)[0]


def collect_common_dominant_rows(results_root: Path) -> tuple[list[InteractionRow], list[str], list[str], object]:
    inventory = collect_inventory(results_root, required_files=("plip/report.xml",))
    detected_receptors, detected_ligands = inventory_entities(inventory)
    metadata = load_source_metadata(results_root, detected_receptors, detected_ligands)
    receptors = list(metadata.receptor_order)
    ligands = list(metadata.ligand_order)

    rows: list[InteractionRow] = []
    for receptor_id in receptors:
        for ligand_name in ligands:
            run_entries = (inventory.get(receptor_id) or {}).get(ligand_name) or []
            run_maps = []
            for _run_name, run_dir in run_entries:
                parsed = _parse_plip_types(run_dir / "plip" / "report.xml")
                if parsed:
                    run_maps.append(parsed)

            run_sets = [set(run_map.keys()) for run_map in run_maps if run_map]
            if not run_sets:
                continue
            common_residues = set.intersection(*run_sets)

            for residue_key in sorted(common_residues):
                kind_counts: dict[str, int] = {}
                for run_map in run_maps:
                    for kind in run_map.get(residue_key, []):
                        kind_counts[kind] = kind_counts.get(kind, 0) + 1
                dominant = _choose_dominant_kind(kind_counts)
                if not dominant:
                    continue
                residue = f"{residue_key[0]}{residue_key[1]}{residue_key[2]}"
                rows.append(
                    InteractionRow(
                        receptor=receptor_id,
                        ligand=ligand_name,
                        kind=dominant,
                        residue=residue,
                    )
                )

    return rows, receptors, ligands, metadata


def plot_stacked_bar(rows: list[InteractionRow], receptors: list[str], ligands: list[str], metadata, out_path: Path) -> None:
    if not receptors or not ligands:
        fig = plt.figure(figsize=(7.2, 3.0))
        fig.text(0.5, 0.5, "No receptor/ligand data found", ha="center", va="center", fontsize=12)
        fig.savefig(out_path, dpi=300, facecolor="white", bbox_inches="tight")
        plt.close(fig)
        return

    df = pd.DataFrame([row.__dict__ for row in rows])
    if df.empty:
        fig = plt.figure(figsize=(7.2, 3.0))
        fig.text(0.5, 0.5, "No common PLIP interactions found across runs", ha="center", va="center", fontsize=11)
        fig.savefig(out_path, dpi=300, facecolor="white", bbox_inches="tight")
        plt.close(fig)
        return

    df["kind_label"] = df["kind"].map(KIND_LABELS).fillna(df["kind"])
    df = df.drop_duplicates(subset=["ligand", "receptor", "kind_label", "residue"])
    counts = df.groupby(["ligand", "receptor", "kind_label"]).size().reset_index(name="count")

    kinds_present = list(dict.fromkeys([KIND_LABELS.get(kind, kind) for kind in KIND_ORDER] + sorted(counts["kind_label"].unique())))
    kinds_present = [kind for kind in kinds_present if kind in set(counts["kind_label"].unique())]

    cmap = plt.get_cmap("tab10")
    colors = {kind: cmap(idx % 10) for idx, kind in enumerate(kinds_present)}

    n_ligands = len(ligands)
    if n_ligands <= 2:
        ncols = n_ligands
    elif n_ligands <= 4:
        ncols = 2
    else:
        ncols = 3
    nrows = (n_ligands + ncols - 1) // ncols

    plt.rcParams["font.family"] = ["Times New Roman", "DejaVu Serif", "serif"]
    fig, axes = plt.subplots(nrows, ncols, figsize=(3.7 * ncols + 0.6, 3.0 * nrows + 1.3), sharey=True)
    if isinstance(axes, np.ndarray):
        axes_flat = list(axes.reshape(-1))
    else:
        axes_flat = [axes]
    fig.patch.set_facecolor("white")

    receptor_order = list(receptors)
    receptor_ticks = [metadata.receptor_display(receptor_id) for receptor_id in receptor_order]
    panel_labels = [chr(ord("A") + idx) for idx in range(n_ligands)]

    for ax_idx, ligand_name in enumerate(ligands):
        ax = axes_flat[ax_idx]
        sub = counts[counts["ligand"] == ligand_name]
        pivot = sub.pivot_table(index="receptor", columns="kind_label", values="count", fill_value=0)
        pivot = pivot.reindex(index=receptor_order, columns=kinds_present, fill_value=0)

        x_vals = list(range(len(receptor_order)))
        bottoms = [0] * len(receptor_order)
        for kind_label in pivot.columns:
            values = pivot[kind_label].astype(int).tolist()
            ax.bar(x_vals, values, bottom=bottoms, color=colors[kind_label], label=kind_label, width=0.74, edgecolor="none")
            bottoms = [base + val for base, val in zip(bottoms, values)]

        ax.set_xticks(x_vals)
        ax.set_xticklabels(receptor_ticks)
        ax.set_title(metadata.ligand_display(ligand_name), fontsize=11)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(axis="y", alpha=0.25, linewidth=0.6, color="#999999")
        ax.text(
            -0.12,
            1.05,
            panel_labels[ax_idx],
            transform=ax.transAxes,
            ha="left",
            va="bottom",
            fontsize=12,
            fontweight="bold",
            color="black",
            clip_on=False,
        )

    for idx in range(n_ligands, len(axes_flat)):
        axes_flat[idx].axis("off")

    for row_idx in range(nrows):
        left_idx = row_idx * ncols
        if left_idx < len(axes_flat) and axes_flat[left_idx].has_data():
            axes_flat[left_idx].set_ylabel("Interaction instances")

    if kinds_present:
        legend_handles = [Patch(facecolor=colors[kind], edgecolor="none", label=kind) for kind in kinds_present]
        fig.legend(
            handles=legend_handles,
            loc="lower center",
            bbox_to_anchor=(0.5, 0.015),
            ncol=min(4, len(kinds_present)),
            frameon=False,
            fontsize=9,
        )

    fig.subplots_adjust(left=0.08, right=0.99, top=0.94, bottom=0.12, wspace=0.36, hspace=0.4)
    fig.savefig(out_path, dpi=300, facecolor="white")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("monomer_final/results"))
    parser.add_argument("--out", type=Path, default=Path("plots"))
    args = parser.parse_args()

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    rows, receptors, ligands, metadata = collect_common_dominant_rows(args.root)
    out_path = out_dir / "interaction_stacked_bar.png"
    plot_stacked_bar(rows, receptors, ligands, metadata, out_path)
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
