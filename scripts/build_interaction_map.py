#!/usr/bin/env python3
"""Generate lightweight interaction_map.csv/.json files from PLIP outputs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


DEFAULT_CUTOFF = 4.0
DEFAULT_HBOND_CUTOFF = 3.2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True, help="PLIP report.xml path")
    parser.add_argument(
        "--complex",
        type=Path,
        required=True,
        help="Complex PDB that contains both receptor and ligand (e.g. *_complex_protonated.pdb)",
    )
    parser.add_argument(
        "--pose",
        type=Path,
        required=True,
        help="Pose PDB path (used only for metadata in the JSON payload)",
    )
    parser.add_argument(
        "--receptor",
        type=Path,
        required=True,
        help="Original receptor PDB path (metadata only)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Destination directory for interaction_map.{csv,json}",
    )
    parser.add_argument(
        "--cutoff",
        type=float,
        default=DEFAULT_CUTOFF,
        help=f"Distance cutoff for contacts in Å (default: {DEFAULT_CUTOFF})",
    )
    parser.add_argument(
        "--hbond-cutoff",
        type=float,
        default=DEFAULT_HBOND_CUTOFF,
        help=f"Distance threshold for flagging hydrogen-bond candidates (default: {DEFAULT_HBOND_CUTOFF})",
    )
    return parser.parse_args()


def load_identifiers(report_path: Path) -> Dict[str, str]:
    root = ET.parse(report_path).getroot()
    identifiers = root.find(".//identifiers")
    if identifiers is None:
        return {}
    data = {
        "hetid": (identifiers.findtext("hetid") or "").strip(),
        "chain": (identifiers.findtext("chain") or "").strip(),
        "position": (identifiers.findtext("position") or "").strip(),
        "pdb_id": (root.findtext(".//pdbid") or "").strip(),
    }
    return data


def infer_ligand(complex_path: Path, hint: Dict[str, str]) -> Dict[str, str]:
    """Fallback heuristic if PLIP identifiers are incomplete."""
    resname = hint.get("hetid", "")
    chain = hint.get("chain", "")
    resid = hint.get("position", "")
    if resname and chain and resid:
        return {"resname": resname, "chain": chain, "resid": resid}

    counts: Dict[Tuple[str, str, str], int] = {}
    with complex_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.startswith("HETATM"):
                continue
            element = (line[76:78].strip() or line[12:16].strip()[0]).upper()
            if element == "H":
                continue
            key = (line[21].strip(), line[17:20].strip(), line[22:26].strip())
            counts[key] = counts.get(key, 0) + 1

    if not counts:
        raise SystemExit(f"Could not infer ligand atoms from {complex_path}")

    chain, resname, resid = max(counts.items(), key=lambda item: item[1])[0]
    return {"resname": resname, "chain": chain, "resid": resid}


def parse_atoms(
    pdb_path: Path,
    ligand_identity: Dict[str, str],
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    receptor_atoms: List[Dict[str, str]] = []
    ligand_atoms: List[Dict[str, str]] = []

    with pdb_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.startswith(("ATOM", "HETATM")):
                continue
            element = (line[76:78].strip() or line[12:16].strip()[0]).upper()
            if element == "H":
                continue
            atom = {
                "chain": line[21].strip(),
                "resname": line[17:20].strip(),
                "resid": line[22:26].strip(),
                "name": line[12:16].strip(),
                "element": element,
                "x": float(line[30:38]),
                "y": float(line[38:46]),
                "z": float(line[46:54]),
            }
            if (
                atom["chain"] == ligand_identity["chain"]
                and atom["resname"] == ligand_identity["resname"]
                and atom["resid"] == ligand_identity["resid"]
            ):
                ligand_atoms.append(atom)
            else:
                receptor_atoms.append(atom)
    if not ligand_atoms:
        raise SystemExit(
            f"No ligand atoms matched selection {ligand_identity} in {pdb_path}"
        )
    return receptor_atoms, ligand_atoms


def classify_interaction(
    rec_atom: Dict[str, str],
    lig_atom: Dict[str, str],
    distance: float,
    hbond_cutoff: float,
) -> str:
    if rec_atom["element"] == "C" and lig_atom["element"] == "C":
        return "hydrophobic"
    if (
        distance <= hbond_cutoff
        and rec_atom["element"] in {"N", "O"}
        and lig_atom["element"] in {"N", "O"}
    ):
        return "hbond_candidate"
    return "contact"


def collect_contacts(
    receptor_atoms: Iterable[Dict[str, str]],
    ligand_atoms: Iterable[Dict[str, str]],
    cutoff: float,
    hbond_cutoff: float,
) -> List[Dict[str, str]]:
    contacts: List[Dict[str, str]] = []
    for rec_atom in receptor_atoms:
        rx, ry, rz = rec_atom["x"], rec_atom["y"], rec_atom["z"]
        for lig_atom in ligand_atoms:
            dx = rx - lig_atom["x"]
            dy = ry - lig_atom["y"]
            dz = rz - lig_atom["z"]
            distance = math.sqrt(dx * dx + dy * dy + dz * dz)
            if distance > cutoff:
                continue
            contacts.append(
                {
                    "receptor_chain": rec_atom["chain"],
                    "receptor_resname": rec_atom["resname"],
                    "receptor_resid": rec_atom["resid"],
                    "receptor_atom": rec_atom["name"],
                    "ligand_atom": lig_atom["name"],
                    "ligand_element": lig_atom["element"],
                    "distance": round(distance, 3),
                    "interaction_type": classify_interaction(
                        rec_atom, lig_atom, distance, hbond_cutoff
                    ),
                }
            )
    contacts.sort(
        key=lambda item: (
            item["receptor_chain"],
            int(item["receptor_resid"])
            if item["receptor_resid"].isdigit()
            else item["receptor_resid"],
            item["receptor_atom"],
            item["ligand_atom"],
            item["distance"],
        )
    )
    return contacts


def summarize_contacts(contacts: Iterable[Dict[str, str]]) -> List[Dict[str, object]]:
    summary: Dict[
        Tuple[str, str, str],
        Dict[str, object],
    ] = defaultdict(
        lambda: {
            "receptor_chain": "",
            "receptor_resname": "",
            "receptor_resid": "",
            "contact_count": 0,
            "min_distance": float("inf"),
        }
    )
    for entry in contacts:
        key = (
            entry["receptor_chain"],
            entry["receptor_resname"],
            entry["receptor_resid"],
        )
        bucket = summary[key]
        bucket["receptor_chain"], bucket["receptor_resname"], bucket["receptor_resid"] = key
        bucket["contact_count"] += 1
        bucket["min_distance"] = min(bucket["min_distance"], entry["distance"])

    ordered = sorted(
        summary.values(),
        key=lambda item: (
            item["receptor_chain"],
            int(item["receptor_resid"])
            if str(item["receptor_resid"]).isdigit()
            else item["receptor_resid"],
        ),
    )
    for entry in ordered:
        entry["min_distance"] = round(float(entry["min_distance"]), 3)
    return ordered


def write_csv(csv_path: Path, contacts: Iterable[Dict[str, str]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "receptor_chain",
        "receptor_resname",
        "receptor_resid",
        "receptor_atom",
        "ligand_atom",
        "ligand_element",
        "distance",
        "interaction_type",
    ]
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(contacts)


def write_json(
    json_path: Path,
    identifiers: Dict[str, str],
    contacts: List[Dict[str, str]],
    metadata: Dict[str, object],
    cutoff: float,
) -> None:
    payload = {
        "pdb_id": identifiers.get("pdb_id") or metadata.get("pdb_id", ""),
        "ligand_resname": identifiers.get("hetid", ""),
        "ligand_chain": identifiers.get("chain", ""),
        "ligand_resid": identifiers.get("position", ""),
        "cutoff": cutoff,
        "contact_count": len(contacts),
        "contacts": contacts,
        "residue_summary": summarize_contacts(contacts),
        **metadata,
    }
    json_path.write_text(json.dumps(payload, indent=2))


def main() -> None:
    args = parse_args()

    report_path = args.report.resolve()
    complex_path = args.complex.resolve()
    pose_path = args.pose.resolve()
    receptor_path = args.receptor.resolve()
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    identifiers = load_identifiers(report_path)
    ligand_identity = infer_ligand(complex_path, identifiers)

    receptor_atoms, ligand_atoms = parse_atoms(complex_path, ligand_identity)
    contacts = collect_contacts(receptor_atoms, ligand_atoms, args.cutoff, args.hbond_cutoff)

    metadata = {
        "pose": str(pose_path),
        "receptor": str(receptor_path),
        "complex": str(complex_path),
        "report": str(report_path),
        "pdb_id": identifiers.get("pdb_id", ""),
    }

    write_csv(output_dir / "interaction_map.csv", contacts)
    write_json(output_dir / "interaction_map.json", identifiers, contacts, metadata, args.cutoff)
    print(f"Wrote {len(contacts)} contacts to {output_dir}")


if __name__ == "__main__":
    main()
