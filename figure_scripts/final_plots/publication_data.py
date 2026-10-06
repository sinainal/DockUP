"""Strict, auditable inputs shared by the publication figure workflow."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .affinity_variants import affinity_from_results_json, find_results_json
from .dataset_utils import collect_inventory, inventory_entities, load_source_metadata, run_sort_key
from .common_residue_interactions import KIND_ORDER, KIND_LABELS

RECEPTORS = {"7X2F": "D1", "6CM4": "D2", "3PBL": "D3", "5WIU": "D4", "8IRV": "D5"}
LIGANDS = {"Ethylene_trimer": "PE", "Ethylene_terephthalate_trimer": "PET", "Propylene_trimer": "PP", "Styrene_trimer": "PS"}
POLYMER_COLORS = ["#0072B2", "#D55E00", "#009E73", "#AA4499"]
# Muted, color-blind-conscious contact palette shared by the matrix and renders.
KIND_COLORS = {k: c for k, c in zip(KIND_ORDER, ["#527DA5", "#D3A43B", "#B85C6B", "#4D9183", "#8C6DAF", "#65A9C3", "#8C8A45", "#874C68"])}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def residue_key(label: str):
    match = re.fullmatch(r"([A-Z]+)(-?\d+)(.*)", label)
    if not match:
        raise ValueError(f"Invalid residue label: {label}")
    return int(match[2]), match[3], match[1]


def parse_contacts(path: Path) -> list[dict]:
    root = ET.parse(path).getroot()
    contacts = []
    for site in root.findall(".//bindingsite"):
        # The docking complex contains one modeled trimer named UNL.
        for node in site.findall("./interactions/*/*"):
            if node.tag not in KIND_ORDER:
                raise ValueError(f"Unsupported PLIP contact type {node.tag} in {path}")
            chain, name, number = [node.findtext(tag, "").strip() for tag in ("reschain", "restype", "resnr")]
            if not name or not number:
                raise ValueError(f"Incomplete PLIP contact in {path}")
            def coo(tag):
                elem = node.find(tag)
                return [float(elem.findtext(axis)) for axis in "xyz"] if elem is not None else None
            contacts.append(dict(kind=node.tag, residue=f"{name}{number}{chain}", chain=chain,
                                 resname=name, resnr=number, ligand_xyz=coo("ligcoo"), protein_xyz=coo("protcoo")))
    return contacts


@dataclass
class Run:
    receptor: str
    ligand: str
    run: str
    score: float
    directory: Path
    results: Path
    plip: Path
    complex: Path
    contacts: list[dict]
    reported_score: float | None = None
    score_source: Path | None = None


@dataclass
class Dataset:
    root: Path
    runs: list[Run]
    receptors: list[str]
    ligands: list[str]
    receptor_labels: dict[str, str]
    ligand_labels: dict[str, str]
    manifest: Path | None = None

    def group(self, receptor, ligand):
        return sorted([r for r in self.runs if r.receptor == receptor and r.ligand == ligand], key=lambda r: run_sort_key(r.run))

    def stats(self, receptor, ligand):
        values = [r.score for r in self.group(receptor, ligand)]
        return statistics.mean(values), statistics.stdev(values) if len(values) > 1 else 0.0

    def representative(self, receptor, ligand):
        group = self.group(receptor, ligand)
        median = statistics.median(r.score for r in group)
        return min(group, key=lambda r: (abs(r.score - median), run_sort_key(r.run)))


def load_dataset(root: Path, batch_manifest: Path | None = None, *, require_plip=True, all_ligands=False) -> Dataset:
    root = root.resolve()
    inventory = collect_inventory(root, required_files=())
    recs, ligs = inventory_entities(inventory)
    metadata = load_source_metadata(root, recs, ligs)
    recs, ligs = list(metadata.receptor_order), list(metadata.ligand_order)
    if not all_ligands and set(RECEPTORS).issubset(recs) and set(LIGANDS).issubset(ligs):
        recs, ligs = list(RECEPTORS), list(LIGANDS)
    expected = None
    if batch_manifest:
        expected = set()
        with batch_manifest.open(newline="") as handle:
            for fields in csv.reader(handle, delimiter="\t"):
                if len(fields) < 8:
                    raise ValueError("Invalid DockUP batch manifest")
                ligand = Path(fields[2]).stem
                expected.add((fields[0], ligand, f"run{int(fields[7])}"))
        recs = [r for r in recs if any(k[0] == r for k in expected)]
        ligs = [l for l in ligs if any(k[1] == l for k in expected)]
    runs = []
    for receptor in recs:
        for ligand in ligs:
            entries = inventory.get(receptor, {}).get(ligand, [])
            for run, directory in entries:
                if expected is not None and (receptor, ligand, run) not in expected:
                    continue
                result = find_results_json(directory)
                score = affinity_from_results_json(result) if result else None
                if score is None or not math.isfinite(score):
                    raise ValueError(f"Missing or invalid score: {directory}")
                reported_score = score
                score_source = result
                poses = list(directory.glob(f"{receptor}_results/*_out_vina.pdbqt"))
                if len(poses) > 1:
                    raise ValueError(f"Ambiguous docked pose file: {directory}")
                if poses:
                    match = re.search(r"REMARK VINA RESULT:\s+(-?[\d.]+)", poses[0].read_text())
                    if not match:
                        raise ValueError(f"Missing rank-1 Vina score: {poses[0]}")
                    raw_score = float(match[1])
                    # Vina's stdout uses fewer significant figures for |score|>=10;
                    # DockUP's JSON inherits that display rounding.
                    if abs(raw_score-score) > .00501:
                        raise ValueError(f"Pose score disagrees with DockUP JSON: {directory}")
                    score, score_source = raw_score, poses[0]
                plip = directory / "plip" / "report.xml"
                contacts = parse_contacts(plip) if require_plip else []
                complex_file = directory / f"{receptor}_complex.pdb"
                runs.append(Run(receptor, ligand, run, score, directory, result, plip, complex_file, contacts, reported_score, score_source))
    if not runs:
        raise ValueError(f"No valid results in {root}")
    found = {(r.receptor, r.ligand, r.run) for r in runs}
    if expected is not None and found != expected:
        raise ValueError(f"Batch mismatch: missing={sorted(expected-found)}, unexpected={sorted(found-expected)}")
    for r in recs:
        for l in ligs:
            if not any(x.receptor == r and x.ligand == l for x in runs):
                raise ValueError(f"Missing receptor–ligand group: {r}/{l}")
    return Dataset(root, runs, recs, ligs,
                   {r: RECEPTORS.get(r, metadata.receptor_display(r)) for r in recs},
                   {l: LIGANDS.get(l, metadata.ligand_display(l)) for l in ligs}, batch_manifest)


def interaction_matrices(data: Dataset, receptor: str):
    groups = {ligand: data.group(receptor, ligand) for ligand in data.ligands}
    residues = sorted({c["residue"] for runs in groups.values() for run in runs for c in run.contacts}, key=residue_key)
    frequency, dominant = {}, {}
    for residue in residues:
        for ligand, runs in groups.items():
            n = sum(any(c["residue"] == residue for c in r.contacts) for r in runs)
            frequency[residue, ligand] = n
            # Empty but successfully parsed reports count in the denominator.
            if n == len(runs):
                counts = Counter(c["kind"] for r in runs for c in r.contacts if c["residue"] == residue)
                dominant[residue, ligand] = max(KIND_ORDER, key=lambda kind: (counts[kind], -KIND_ORDER.index(kind)))
    return residues, frequency, dominant


def export_data(data: Dataset, out: Path, prefix: str):
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for r in data.runs:
        rows.append(dict(receptor=r.receptor, subtype=data.receptor_labels[r.receptor], ligand=r.ligand,
                         polymer=data.ligand_labels[r.ligand], run=r.run, score=r.score,
                         dockup_json_score=r.reported_score, score_source=str(r.score_source),
                         score_source_sha256=sha256(r.score_source),
                         results_path=str(r.results), results_sha256=sha256(r.results),
                         plip_path=str(r.plip), plip_sha256=sha256(r.plip) if r.plip.exists() else "",
                         complex_path=str(r.complex), complex_sha256=sha256(r.complex) if r.complex.exists() else ""))
    with (out / f"{prefix}_runs.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
    summary = []
    for receptor in data.receptors:
        for ligand in data.ligands:
            mean, sd = data.stats(receptor, ligand)
            summary.append(dict(receptor=receptor, ligand=ligand, n=len(data.group(receptor, ligand)), mean=mean, sample_sd=sd,
                                representative_run=data.representative(receptor, ligand).run))
    manifest = dict(source_root=str(data.root), batch_manifest=str(data.manifest or ""),
                    batch_sha256=sha256(data.manifest) if data.manifest else "", run_count=len(rows),
                    sd_definition="sample standard deviation (ddof=1)",
                    score_definition="rank-1 REMARK VINA RESULT in docked PDBQT when available; otherwise DockUP results.json",
                    representative_policy="closest to median score; ties resolved by lowest run number", summary=summary)
    (out / f"{prefix}_provenance.json").write_text(json.dumps(manifest, indent=2)+"\n")


def arguments(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--batch-manifest", type=Path)
    parser.add_argument("--all-ligands", action="store_true", help="Include native controls even in dopamine-trimer folders")
    parser.add_argument("--dpi", type=int, default=600)
    parser.add_argument("--layout-options-json", type=Path, help="Publication presentation options only; preserves all seeds")
    return parser


def plot_style():
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.labelsize": 8,
                         "axes.titlesize": 9, "axes.titleweight": "bold", "axes.linewidth": 0.65,
                         "xtick.labelsize": 7, "ytick.labelsize": 7, "pdf.fonttype": 42, "ps.fonttype": 42,
                         "svg.fonttype": "none", "text.color": "#25313F", "axes.labelcolor": "#25313F",
                         "xtick.color": "#25313F", "ytick.color": "#25313F", "figure.facecolor": "white"})


def save_figure(fig, out: Path, stem: str, dpi: int):
    out.mkdir(parents=True, exist_ok=True)
    for suffix in ("png", "pdf", "svg"):
        fig.savefig(out / f"{stem}.{suffix}", dpi=dpi, facecolor="white")
