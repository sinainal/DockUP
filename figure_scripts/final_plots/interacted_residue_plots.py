"""
Run Frequency Heatmap Plot
Generates: plots/run_frequency_heatmap.png
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

from .dataset_utils import collect_inventory, inventory_entities, load_source_metadata


@dataclass(frozen=True)
class ResidueKey:
    chain: str
    restype: str
    resnr: str

    def label(self) -> str:
        return f"{self.restype} {self.resnr}"


def parse_residues_from_plip(report_xml: Path) -> list[ResidueKey]:
    if not report_xml.exists():
        return []
    try:
        tree = ET.parse(report_xml)
    except ET.ParseError:
        return []

    root = tree.getroot()
    residues: list[ResidueKey] = []
    for inter in root.findall(".//bindingsite/interactions//*"):
        resnr = (inter.findtext("resnr") or "").strip()
        restype = (inter.findtext("restype") or "").strip()
        reschain = (inter.findtext("reschain") or "").strip()
        if not (resnr and restype and reschain):
            continue
        residues.append(ResidueKey(chain=reschain, restype=restype, resnr=resnr))
    return residues


def collect_presence(results_root: Path):
    inventory = collect_inventory(results_root, required_files=("plip/report.xml",))
    detected_receptors, detected_ligands = inventory_entities(inventory)
    metadata = load_source_metadata(results_root, detected_receptors, detected_ligands)
    receptors = list(metadata.receptor_order)
    ligands = list(metadata.ligand_order)

    presence: dict[str, dict[str, dict[str, set[ResidueKey]]]] = {
        receptor_id: {ligand_name: {} for ligand_name in ligands}
        for receptor_id in receptors
    }

    for receptor_id in receptors:
        for ligand_name in ligands:
            run_entries = (inventory.get(receptor_id) or {}).get(ligand_name) or []
            for run_name, run_dir in run_entries:
                report_xml = run_dir / "plip" / "report.xml"
                residues = set(parse_residues_from_plip(report_xml))
                presence[receptor_id][ligand_name][run_name] = residues

    return presence, receptors, ligands, metadata


def build_frequency_matrix(
    presence: dict[str, dict[str, dict[str, set[ResidueKey]]]],
    receptor_id: str,
    ligands: list[str],
) -> tuple[list[ResidueKey], np.ndarray]:
    all_residues: set[ResidueKey] = set()
    for ligand_name in ligands:
        for run_residues in presence.get(receptor_id, {}).get(ligand_name, {}).values():
            all_residues |= run_residues

    residues = sorted(
        all_residues,
        key=lambda row: (int(row.resnr) if row.resnr.isdigit() else 10**9, row.restype, row.chain),
    )

    matrix = np.zeros((len(residues), len(ligands)), dtype=int)
    for col_idx, ligand_name in enumerate(ligands):
        runs = presence.get(receptor_id, {}).get(ligand_name, {})
        for row_idx, residue in enumerate(residues):
            matrix[row_idx, col_idx] = sum(1 for run_set in runs.values() if residue in run_set)

    return residues, matrix


def plot_frequency_heatmap(presence, receptors: list[str], ligands: list[str], metadata, out_path: Path) -> None:
    if not receptors or not ligands:
        fig = plt.figure(figsize=(7.0, 3.0))
        fig.text(0.5, 0.5, "No receptor/ligand data found", ha="center", va="center", fontsize=12)
        fig.savefig(out_path, dpi=300, facecolor="white", bbox_inches="tight")
        plt.close(fig)
        return

    plt.rcParams["font.family"] = ["Times New Roman", "DejaVu Serif", "serif"]

    receptor_order = list(receptors)
    matrices = {receptor_id: build_frequency_matrix(presence, receptor_id, ligands) for receptor_id in receptor_order}

    max_freq = max((int(mat.max()) for _residues, mat in matrices.values()), default=0)
    vmax = max(1, max_freq)

    fig_width = max(8.2, 2.2 * len(receptor_order) + 0.8)
    fig_height = 4.4
    fig, axes = plt.subplots(1, len(receptor_order), figsize=(fig_width, fig_height), constrained_layout=True, sharex=True)
    if len(receptor_order) == 1:
        axes = [axes]

    image_for_cbar = None
    ligand_ticks = [metadata.ligand_display(ligand_name) for ligand_name in ligands]

    for ax, receptor_id in zip(axes, receptor_order):
        residues, matrix = matrices[receptor_id]
        image = ax.imshow(matrix, aspect="auto", cmap="viridis", vmin=0, vmax=vmax)
        image_for_cbar = image

        ax.set_title(metadata.receptor_display(receptor_id), fontsize=11)
        ax.set_xticks(range(len(ligands)))
        ax.set_xticklabels(ligand_ticks, rotation=45, ha="right")
        ax.set_yticks(range(len(residues)))
        ax.set_yticklabels([residue.label() for residue in residues], fontsize=8)
        ax.tick_params(axis="y", length=0)
        ax.tick_params(axis="x", length=0)

        for spine in ax.spines.values():
            spine.set_visible(False)

        ax.set_xticks(np.arange(-0.5, len(ligands), 1), minor=True)
        ax.set_yticks(np.arange(-0.5, len(residues), 1), minor=True)
        ax.grid(which="minor", color="#e6e6e6", linestyle="-", linewidth=0.4)
        ax.tick_params(which="minor", bottom=False, left=False)

    if image_for_cbar is not None:
        cbar = fig.colorbar(image_for_cbar, ax=axes, shrink=0.76)
        cbar.set_label(f"Run frequency (0-{vmax})")

    fig.savefig(out_path, dpi=300, facecolor="white")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("monomer_final/results"))
    parser.add_argument("--out", type=Path, default=Path("plots"))
    args = parser.parse_args()

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    presence, receptors, ligands, metadata = collect_presence(args.root)
    out_path = out_dir / "run_frequency_heatmap.png"
    plot_frequency_heatmap(presence, receptors, ligands, metadata, out_path)
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
