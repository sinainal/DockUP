import csv
import json
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from typing import List, Tuple, Optional


def _parse_plip_report_residues(report_txt: str) -> List[Tuple[str, str, str]]:
    """Parse PLIP report.txt and return unique (chain, resname, resi) tuples."""
    seen: List[Tuple[str, str, str]] = []
    mode = None
    for line in report_txt.splitlines():
        if "**Hydrophobic Interactions**" in line:
            mode = "hydro"
            continue
        if "**Hydrogen Bonds**" in line:
            mode = "hbond"
            continue
        if mode is None:
            continue
        if line.startswith("|") and not line.startswith("| RESNR"):
            parts = [p.strip() for p in line.strip().strip("|").split("|")]
            if len(parts) < 3:
                continue
            resnr, restype, reschain = parts[0], parts[1], parts[2]
            key = (reschain, restype, resnr)
            if key not in seen:
                seen.append(key)
    return seen


def _parse_plip_report_xml_residues(xml_path: str) -> List[Tuple[str, str, str]]:
    """Parse PLIP report.xml bs_residues with contact=True."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    seen: List[Tuple[str, str, str]] = []
    for bs in root.findall(".//bindingsite/bs_residues/bs_residue[@contact='True']"):
        resname = bs.attrib.get("aa", "").strip()
        text = (bs.text or "").strip()
        if not resname or len(text) < 2:
            continue
        chain = text[-1]
        resi = text[:-1]
        key = (chain, resname, resi)
        if key not in seen:
            seen.append(key)
    return seen


def load_interacting_residues(
    json_path: str,
    plip_csv: Optional[str] = None,
    plip_report_txt: Optional[str] = None,
) -> List[Tuple[str, str, str]]:
    # Prefer PLIP contacts if provided and exists
    if plip_report_txt and os.path.exists(plip_report_txt):
        if plip_report_txt.lower().endswith(".xml"):
            residues = _parse_plip_report_xml_residues(plip_report_txt)
            if residues:
                return residues
        with open(plip_report_txt) as f:
            report = f.read()
        residues = _parse_plip_report_residues(report)
        if residues:
            return residues
    if plip_csv and os.path.exists(plip_csv):
        seen = []
        with open(plip_csv) as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = (row["chain"], row["resname"], row["resi"])
                if key not in seen:
                    seen.append(key)
        if seen:
            return seen
    # fallback to interaction_map.json
    with open(json_path) as f:
        data = json.load(f)
    seen = []
    for c in data.get("contacts", []):
        key = (c["receptor_chain"], c["receptor_resname"], c["receptor_resid"])
        if key not in seen:
            seen.append(key)
    return seen


def run_pymol(pml_path: str, workdir: str = ".") -> float:
    start = time.time()
    subprocess.run(["pymol", "-cq", pml_path], check=True, cwd=workdir)
    return time.time() - start


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)
