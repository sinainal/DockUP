#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path
from typing import Any

from rdkit import Chem
from rdkit.Chem import AllChem, rdForceFieldHelpers, rdPartialCharges


DISTANCE_CUTOFF = 4.0
HBOND_CUTOFF = 3.2
WATER_NAMES = {"HOH", "WAT", "DOD"}
GRID_VALUE_RE = re.compile(r"^(center_[xyz]|size_[xyz])\s*=\s*([+-]?\d+(?:\.\d+)?)\s*$")
VINA_TABLE_RE = re.compile(
    r"^\s*(?P<mode>\d+)\s+(?P<affinity>-?\d+(?:\.\d+)?)\s+(?P<rmsd_lb>-?\d+(?:\.\d+)?)\s+(?P<rmsd_ub>-?\d+(?:\.\d+)?)\s*$"
)


def _load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
_load_dotenv(ROOT_DIR / ".env")


def _env_bool(name: str, default: bool) -> bool:
    value = str(os.environ.get(name, "")).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _candidate_paths(name: str) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for raw in (
        os.environ.get(name),
        str(Path(sys.executable).expanduser().parent / name),
        str(Path(sys.executable).resolve().parent / name),
        shutil.which(name),
    ):
        path = str(raw or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        out.append(Path(path))
    return out


def _find_executable(name: str, *, env_name: str = "") -> Path:
    candidates: list[Path] = []
    if env_name:
        raw = str(os.environ.get(env_name, "")).strip()
        if raw:
            candidates.append(Path(raw))
    candidates.extend(_candidate_paths(name))
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise FileNotFoundError(f"Required executable not found: {name}")


def _resolve_python() -> Path:
    return Path(os.environ.get("DOCKUP_PYTHON") or sys.executable).expanduser()


def _discover_plip_command(python_bin: Path) -> list[str]:
    py_dir = python_bin.parent
    py_plip = py_dir / "plip"
    if py_plip.exists() and os.access(py_plip, os.X_OK):
        return [str(py_plip)]
    try:
        probe = subprocess.run(
            [str(python_bin), "-c", "import plip"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        probe = None
    if probe and probe.returncode == 0:
        return [str(python_bin), "-m", "plip.plipcmd"]
    system_plip = shutil.which("plip")
    if system_plip:
        return [system_plip]
    return []


def _maybe_wrap_stdbuf(cmd: list[str]) -> list[str]:
    stdbuf_bin = shutil.which("stdbuf")
    if not stdbuf_bin:
        return cmd
    return [stdbuf_bin, "-o0", "-e0", *cmd]


def _run_command(cmd: list[str], *, cwd: Path, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd))
    run_cmd = _maybe_wrap_stdbuf(cmd) if capture_output else cmd
    if capture_output:
        proc = subprocess.Popen(
            run_cmd,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        stdout_chunks: list[str] = []
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(1)
            if chunk == "":
                break
            stdout_chunks.append(chunk)
            sys.stdout.write(chunk)
            sys.stdout.flush()
        returncode = proc.wait()
        completed = subprocess.CompletedProcess(
            cmd,
            returncode,
            stdout="".join(stdout_chunks),
            stderr="",
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Command failed with exit code {completed.returncode}: {' '.join(cmd)}"
            )
        return completed
    completed = subprocess.run(
        run_cmd,
        cwd=str(cwd),
        text=True,
        capture_output=capture_output,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {completed.returncode}: {' '.join(cmd)}"
        )
    return completed


def _normalize_optional_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    return str(Path(raw).expanduser().resolve())


def _sanitize_folder_name(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return "Ligand_Set"
    text = re.sub(r"[^A-Za-z0-9_.+-]+", "_", text)
    text = text.replace("+", "_plus_")
    return text.strip("._") or "Ligand_Set"


def _read_grid_file(path: Path) -> dict[str, float]:
    values: dict[str, float] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = GRID_VALUE_RE.match(raw_line.strip())
        if not match:
            continue
        values[match.group(1)] = float(match.group(2))
    required = {"center_x", "center_y", "center_z", "size_x", "size_y", "size_z"}
    if not required.issubset(values.keys()):
        missing = ", ".join(sorted(required.difference(values.keys())))
        raise ValueError(f"Grid file is missing required fields: {missing}")
    return values


def _normalize_padding(raw: str) -> tuple[float, float, float]:
    text = str(raw or "").strip()
    if not text:
        return (0.0, 0.0, 0.0)
    parts = [part for part in text.replace(",", " ").split() if part]
    if len(parts) == 1:
        parts *= 3
    elif len(parts) == 2:
        parts.append(parts[-1])
    elif len(parts) > 3:
        parts = parts[:3]
    values = [float(part) for part in parts]
    return (values[0], values[1], values[2])


def _apply_padding(grid: dict[str, float], padding: tuple[float, float, float]) -> dict[str, float]:
    return {
        **grid,
        "size_x": grid["size_x"] + padding[0],
        "size_y": grid["size_y"] + padding[1],
        "size_z": grid["size_z"] + padding[2],
    }


def _write_grid_file(path: Path, grid: dict[str, float]) -> None:
    path.write_text(
        "\n".join(
            [
                f"center_x = {grid['center_x']:.3f}",
                f"center_y = {grid['center_y']:.3f}",
                f"center_z = {grid['center_z']:.3f}",
                f"size_x = {grid['size_x']:.3f}",
                f"size_y = {grid['size_y']:.3f}",
                f"size_z = {grid['size_z']:.3f}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _extract_receptor_chain(pdb_path: Path, chain: str, out_path: Path) -> None:
    selected_chain = str(chain or "").strip()
    use_chain = selected_chain and selected_chain.lower() != "all"
    lines: list[str] = []
    for raw in pdb_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not raw.startswith(("ATOM", "HETATM")):
            continue
        atom_chain = (raw[21].strip() or "_") if len(raw) > 21 else "_"
        if use_chain and atom_chain != selected_chain:
            continue
        resname = raw[17:20].strip()
        if raw.startswith("ATOM"):
            lines.append(raw.rstrip())
            continue
        if resname in WATER_NAMES:
            continue
    if not lines:
        raise RuntimeError(f"No receptor atoms found in {pdb_path} for chain={chain}")
    out_path.write_text("\n".join(lines) + "\nEND\n", encoding="utf-8")


def _process_ligand_sdf(source_path: Path, fixed_sdf_path: Path) -> None:
    supplier = Chem.SDMolSupplier(str(source_path), removeHs=False, sanitize=True)
    mol = next((mol for mol in supplier if mol is not None), None)
    if mol is None:
        supplier = Chem.SDMolSupplier(str(source_path), removeHs=False, sanitize=False)
        mol = next((mol for mol in supplier if mol is not None), None)
    if mol is None:
        raise RuntimeError(f"RDKit could not read ligand file: {source_path}")
    mol = Chem.AddHs(mol, addCoords=True)
    try:
        rdPartialCharges.ComputeGasteigerCharges(mol)
    except Exception:
        pass
    optimized = False
    try:
        rdForceFieldHelpers.UFFOptimizeMolecule(mol)
        optimized = True
    except Exception:
        try:
            if AllChem.MMFFHasAllMoleculeParams(mol):
                AllChem.MMFFOptimizeMolecule(mol)
                optimized = True
        except Exception:
            optimized = False
    writer = Chem.SDWriter(str(fixed_sdf_path))
    writer.write(mol)
    writer.close()
    print(f"Prepared ligand SDF: {fixed_sdf_path.name} (optimized={optimized})")


def _prepare_receptor_pdbqt(
    mk_prepare_receptor: Path,
    *,
    receptor_pdb: Path,
    grid: dict[str, float],
    out_path: Path,
    allow_bad_res: bool,
    default_altloc: str,
    cwd: Path,
) -> None:
    cmd = [
        str(mk_prepare_receptor),
        "--read_pdb",
        str(receptor_pdb),
        "--box_center",
        f"{grid['center_x']:.3f}",
        f"{grid['center_y']:.3f}",
        f"{grid['center_z']:.3f}",
        "--box_size",
        f"{grid['size_x']:.3f}",
        f"{grid['size_y']:.3f}",
        f"{grid['size_z']:.3f}",
        "--write_pdbqt",
        str(out_path),
    ]
    if allow_bad_res:
        cmd.append("--allow_bad_res")
    if default_altloc:
        cmd.extend(["--default_altloc", default_altloc])
    _run_command(cmd, cwd=cwd)


def _prepare_ligand_pdbqt(
    mk_prepare_ligand: Path,
    *,
    fixed_sdf_path: Path,
    out_path: Path,
    cwd: Path,
) -> None:
    cmd = [
        str(mk_prepare_ligand),
        "-i",
        str(fixed_sdf_path),
        "-o",
        str(out_path),
    ]
    _run_command(cmd, cwd=cwd)


def _parse_vina_modes(stdout: str) -> list[dict[str, float | int]]:
    rows: list[dict[str, float | int]] = []
    for line in stdout.splitlines():
        match = VINA_TABLE_RE.match(line)
        if not match:
            continue
        rows.append(
            {
                "mode": int(match.group("mode")),
                "affinity_kcal_mol": float(match.group("affinity")),
                "rmsd_lb": float(match.group("rmsd_lb")),
                "rmsd_ub": float(match.group("rmsd_ub")),
            }
        )
    return rows


def _split_first_model(vina_out_path: Path) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    in_model = False
    for raw_line in vina_out_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.rstrip()
        if line.startswith("MODEL"):
            in_model = True
            current = []
            continue
        if not in_model:
            continue
        if line.startswith("ENDMDL"):
            if current:
                blocks.append(current)
            break
        if line.startswith("TORSDOF"):
            if current:
                blocks.append(current)
                current = []
            continue
        if line.startswith(("ATOM", "HETATM")):
            current.append(line)
    return blocks


def _element_from_pdbqt_line(line: str) -> str:
    element = line[76:78].strip()
    if element:
        return element
    name = line[12:16].strip()
    return (name[0] if name else "C").upper()


def _write_pose_pdb(block: list[str], out_path: Path, *, resname: str, chain: str, resid: int) -> None:
    serial = 1
    out_lines: list[str] = []
    for line in block:
        element = _element_from_pdbqt_line(line).upper()
        if element == "H":
            continue
        atom_name = line[12:16].strip() or element
        x = float(line[30:38])
        y = float(line[38:46])
        z = float(line[46:54])
        out_lines.append(
            f"HETATM{serial:5d} {atom_name:<4} {resname:>3} {chain}{resid:4d}    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00           {element:>2}"
        )
        serial += 1
    if not out_lines:
        raise RuntimeError(f"No heavy atoms found while writing pose PDB: {out_path}")
    out_path.write_text("\n".join(out_lines) + "\nEND\n", encoding="utf-8")


def _build_complex_pdb(receptor_pdb: Path, pose_paths: list[Path], out_path: Path) -> None:
    rec_lines = [
        line.rstrip()
        for line in receptor_pdb.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.startswith("ATOM")
    ]
    lig_lines: list[str] = []
    for pose_path in pose_paths:
        lig_lines.extend(
            line.rstrip()
            for line in pose_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.startswith(("ATOM", "HETATM"))
        )
    if not rec_lines or not lig_lines:
        raise RuntimeError("Cannot build complex PDB without receptor and ligand atoms.")
    out_path.write_text("\n".join(rec_lines + lig_lines) + "\nEND\n", encoding="utf-8")


def _parse_atoms(
    pdb_path: Path,
    ligand_identity: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    receptor_atoms: list[dict[str, Any]] = []
    ligand_atoms: list[dict[str, Any]] = []
    for raw in pdb_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not raw.startswith(("ATOM", "HETATM")):
            continue
        element = (raw[76:78].strip() or raw[12:16].strip()[:1]).upper()
        if element == "H":
            continue
        atom = {
            "chain": raw[21].strip(),
            "resname": raw[17:20].strip(),
            "resid": raw[22:26].strip(),
            "name": raw[12:16].strip(),
            "element": element,
            "x": float(raw[30:38]),
            "y": float(raw[38:46]),
            "z": float(raw[46:54]),
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
        raise RuntimeError(f"No ligand atoms matched {ligand_identity} in {pdb_path}")
    return receptor_atoms, ligand_atoms


def _classify_contact(rec_atom: dict[str, Any], lig_atom: dict[str, Any], distance: float) -> str:
    if rec_atom["element"] == "C" and lig_atom["element"] == "C":
        return "hydrophobic"
    if distance <= HBOND_CUTOFF and rec_atom["element"] in {"N", "O"} and lig_atom["element"] in {"N", "O"}:
        return "hbond_candidate"
    return "contact"


def _collect_contacts(
    receptor_atoms: list[dict[str, Any]],
    ligand_atoms: list[dict[str, Any]],
    *,
    site_id: str,
    ligand_display_name: str,
) -> list[dict[str, Any]]:
    contacts: list[dict[str, Any]] = []
    for rec_atom in receptor_atoms:
        for lig_atom in ligand_atoms:
            dx = rec_atom["x"] - lig_atom["x"]
            dy = rec_atom["y"] - lig_atom["y"]
            dz = rec_atom["z"] - lig_atom["z"]
            distance = (dx * dx + dy * dy + dz * dz) ** 0.5
            if distance > DISTANCE_CUTOFF:
                continue
            contacts.append(
                {
                    "site_id": site_id,
                    "ligand_display_name": ligand_display_name,
                    "receptor_chain": rec_atom["chain"],
                    "receptor_resname": rec_atom["resname"],
                    "receptor_resid": rec_atom["resid"],
                    "receptor_atom": rec_atom["name"],
                    "ligand_atom": lig_atom["name"],
                    "ligand_element": lig_atom["element"],
                    "distance": round(distance, 3),
                    "interaction_type": _classify_contact(rec_atom, lig_atom, distance),
                }
            )
    contacts.sort(
        key=lambda item: (
            item["receptor_chain"],
            int(item["receptor_resid"]) if str(item["receptor_resid"]).isdigit() else str(item["receptor_resid"]),
            item["receptor_atom"],
            item["ligand_atom"],
            item["distance"],
        )
    )
    return contacts


def _summarize_contacts(contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entry in contacts:
        key = (entry["receptor_chain"], entry["receptor_resname"], entry["receptor_resid"])
        bucket = buckets.setdefault(
            key,
            {
                "receptor_chain": entry["receptor_chain"],
                "receptor_resname": entry["receptor_resname"],
                "receptor_resid": entry["receptor_resid"],
                "contact_count": 0,
                "min_distance": None,
                "site_ids": [],
                "ligand_display_names": [],
            },
        )
        bucket["contact_count"] += 1
        distance = entry["distance"]
        current_min = bucket.get("min_distance")
        if current_min is None or distance < current_min:
            bucket["min_distance"] = distance
        site_id = str(entry.get("site_id") or "").strip()
        ligand_label = str(entry.get("ligand_display_name") or "").strip()
        if site_id and site_id not in bucket["site_ids"]:
            bucket["site_ids"].append(site_id)
        if ligand_label and ligand_label not in bucket["ligand_display_names"]:
            bucket["ligand_display_names"].append(ligand_label)
    rows = list(buckets.values())
    rows.sort(
        key=lambda item: (
            item["receptor_chain"],
            int(item["receptor_resid"]) if str(item["receptor_resid"]).isdigit() else str(item["receptor_resid"]),
            item["receptor_resname"],
        )
    )
    return rows


def _write_single_site_report(root: ET.Element, site_index: int, out_path: Path) -> None:
    site_id = str(site_index)
    copied_root = deepcopy(root)
    for child in list(copied_root):
        if child.tag == "bindingsite" and str(child.get("id") or "") != site_id:
            copied_root.remove(child)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(copied_root).write(out_path, encoding="utf-8", xml_declaration=True)


def _parse_bindingsites(report_xml: Path) -> tuple[ET.Element, list[ET.Element]]:
    tree = ET.parse(report_xml)
    root = tree.getroot()
    sites = list(root.findall(".//bindingsite"))
    return root, sites


def _safe_relative(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except Exception:
        return str(path.resolve())


def _build_site_payloads(
    *,
    out_dir: Path,
    complex_pdb: Path,
    report_xml: Path,
    receptor_pdb: Path,
    prepared_ligands: list[dict[str, Any]],
    pdb_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root, bindingsites = _parse_bindingsites(report_xml)
    if len(bindingsites) < 2:
        raise RuntimeError(f"PLIP did not report two distinct binding sites: {report_xml}")

    prepared_by_identity = {
        (
            str(item["ligand_resname"]),
            str(item["ligand_chain"]),
            str(item["ligand_resid"]),
        ): item
        for item in prepared_ligands
    }
    multi_root = out_dir / "multi_ligand"
    multi_root.mkdir(parents=True, exist_ok=True)

    site_rows: list[dict[str, Any]] = []
    merged_contacts: list[dict[str, Any]] = []
    for index, bindingsite in enumerate(bindingsites, start=1):
        identifiers = bindingsite.find("identifiers")
        if identifiers is None:
            continue
        ligand_resname = (identifiers.findtext("hetid") or "").strip()
        ligand_chain = (identifiers.findtext("chain") or "").strip()
        ligand_resid = (identifiers.findtext("position") or "").strip()
        prepared = prepared_by_identity.get((ligand_resname, ligand_chain, ligand_resid))
        if prepared is None:
            if index <= len(prepared_ligands):
                prepared = prepared_ligands[index - 1]
            else:
                raise RuntimeError(
                    f"Could not match PLIP bindingsite {(ligand_resname, ligand_chain, ligand_resid)} to prepared ligands."
                )

        site_id = f"site_{index}"
        site_dir = multi_root / site_id
        site_plip_dir = site_dir / "plip"
        site_plip_dir.mkdir(parents=True, exist_ok=True)
        _write_single_site_report(root, index, site_plip_dir / "report.xml")

        pose_target = site_dir / f"{pdb_id}_pose.pdb"
        ligand_fixed_target = site_dir / f"{pdb_id}_ligand_fixed.sdf"
        meta_path = site_dir / "meta.json"
        shutil.copy2(prepared["pose_pdb"], pose_target)
        shutil.copy2(prepared["fixed_sdf"], ligand_fixed_target)

        receptor_atoms, ligand_atoms = _parse_atoms(
            complex_pdb,
            {
                "chain": str(prepared["ligand_chain"]),
                "resname": str(prepared["ligand_resname"]),
                "resid": str(prepared["ligand_resid"]),
            },
        )
        contacts = _collect_contacts(
            receptor_atoms,
            ligand_atoms,
            site_id=site_id,
            ligand_display_name=str(prepared["display_name"]),
        )
        residue_summary = _summarize_contacts(contacts)
        merged_contacts.extend(contacts)

        site_payload = {
            "multi_ligand": True,
            "site_id": site_id,
            "ligand_display_name": str(prepared["display_name"]),
            "ligand_source_name": str(prepared["source_name"]),
            "ligand_resname": str(prepared["ligand_resname"]),
            "ligand_chain": str(prepared["ligand_chain"]),
            "ligand_resid": str(prepared["ligand_resid"]),
            "pose_path": str(pose_target.resolve()),
            "receptor_path": str(receptor_pdb.resolve()),
            "complex_path": str(complex_pdb.resolve()),
            "report_path": str((site_plip_dir / "report.xml").resolve()),
            "contact_count": len(contacts),
            "residue_summary": residue_summary,
            "contacts": contacts,
            "cutoff": DISTANCE_CUTOFF,
        }
        (site_dir / "interaction_map.json").write_text(json.dumps(site_payload, indent=2), encoding="utf-8")
        meta_path.write_text(
            json.dumps(
                {
                    "site_id": site_id,
                    "ligand_display_name": str(prepared["display_name"]),
                    "ligand_source_name": str(prepared["source_name"]),
                    "ligand_resname": str(prepared["ligand_resname"]),
                    "ligand_chain": str(prepared["ligand_chain"]),
                    "ligand_resid": str(prepared["ligand_resid"]),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        site_rows.append(
            {
                "site_id": site_id,
                "ligand_display_name": str(prepared["display_name"]),
                "ligand_source_name": str(prepared["source_name"]),
                "ligand_resname": str(prepared["ligand_resname"]),
                "ligand_chain": str(prepared["ligand_chain"]),
                "ligand_resid": str(prepared["ligand_resid"]),
                "contact_count": len(contacts),
                "residue_count": len(residue_summary),
                "site_dir": str(site_dir.resolve()),
                "site_dir_rel": _safe_relative(site_dir, out_dir),
                "interaction_map_path": str((site_dir / "interaction_map.json").resolve()),
                "interaction_map_rel": _safe_relative(site_dir / "interaction_map.json", out_dir),
                "report_path": str((site_plip_dir / "report.xml").resolve()),
                "report_rel": _safe_relative(site_plip_dir / "report.xml", out_dir),
                "pose_path": str(pose_target.resolve()),
                "pose_rel": _safe_relative(pose_target, out_dir),
                "ligand_sdf_path": str(ligand_fixed_target.resolve()),
                "ligand_sdf_rel": _safe_relative(ligand_fixed_target, out_dir),
            }
        )

    merged_summary = _summarize_contacts(merged_contacts)
    merged_payload = {
        "multi_ligand": True,
        "ligand_display_name": " + ".join(str(item["display_name"]) for item in prepared_ligands),
        "ligand_count": len(prepared_ligands),
        "contact_count": len(merged_contacts),
        "cutoff": DISTANCE_CUTOFF,
        "contacts": merged_contacts,
        "residue_summary": merged_summary,
        "sites": site_rows,
    }
    return site_rows, merged_payload


def _write_results_json(
    out_dir: Path,
    *,
    run_dir_name: str,
    best_affinity: float | None,
    modes: list[dict[str, float | int]],
    docking_mode: str,
    ligand_display_name: str,
    site_rows: list[dict[str, Any]],
    interaction_map: dict[str, Any],
) -> None:
    payload = {
        run_dir_name: {
            "best_affinity": best_affinity,
            "rmsd": None,
            "docking_mode": docking_mode,
            "job_type": "Multi-Ligand",
            "multi_ligand": True,
            "ligand_display_name": ligand_display_name,
            "ligand_resname": ligand_display_name,
            "ligand_count": len(site_rows),
            "interaction_count": int(interaction_map.get("contact_count") or 0),
            "residue_count": len(interaction_map.get("residue_summary") or []),
            "multi_ligand_sites": site_rows,
            "modes": modes,
        }
    }
    (out_dir / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_ligand_manifest(path: Path) -> list[dict[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError(f"Invalid ligand-set manifest: {path}")
    rows = raw.get("ligands")
    if not isinstance(rows, list):
        raise RuntimeError(f"Ligand-set manifest does not contain a ligand list: {path}")
    normalized: list[dict[str, str]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        lig_path = _normalize_optional_path(str(item.get("path") or ""))
        if not name or not lig_path:
            continue
        normalized.append({"name": name, "path": lig_path})
    if len(normalized) != 2:
        raise RuntimeError(f"Multi-Ligand mode expects exactly 2 ligands, found {len(normalized)} in {path}")
    return normalized


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the DockUP multi-ligand workflow with exactly two dock-ready ligands."
    )
    parser.add_argument("pdb_id")
    parser.add_argument("chain")
    parser.add_argument("ligand")
    parser.add_argument("--lig_spec", required=True, help="JSON manifest containing two ligand paths.")
    parser.add_argument("--pdb_file", required=True, help="Receptor PDB path.")
    parser.add_argument("--grid_pad", default="")
    parser.add_argument("--grid_file", required=True)
    parser.add_argument("--run_id", default="1")
    parser.add_argument("--out_root", default="")
    parser.add_argument("--pdb2pqr_ph", default="")
    parser.add_argument("--pdb2pqr_ff", default="")
    parser.add_argument("--pdb2pqr_ffout", default="")
    parser.add_argument("--pdb2pqr_nodebump", default="")
    parser.add_argument("--pdb2pqr_keep_chain", default="")
    parser.add_argument("--mkrec_allow_bad_res", default="1")
    parser.add_argument("--mkrec_default_altloc", default="A")
    parser.add_argument("--vina_exhaustiveness", default="32")
    parser.add_argument("--vina_num_modes", default="")
    parser.add_argument("--vina_energy_range", default="")
    parser.add_argument("--vina_cpu", default="")
    parser.add_argument("--vina_seed", default="")
    parser.add_argument("--flexres", default="")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if str(args.flexres or "").strip():
        raise SystemExit("Multi-Ligand mode currently supports standard docking only.")

    python_bin = _resolve_python()
    vina_bin = _find_executable("vina", env_name="DOCKUP_VINA")
    mk_prepare_receptor = _find_executable("mk_prepare_receptor.py")
    mk_prepare_ligand = _find_executable("mk_prepare_ligand.py")
    plip_cmd = _discover_plip_command(python_bin)

    pdb_id = str(args.pdb_id or "").strip().upper()
    chain = str(args.chain or "").strip() or "all"
    ligand_manifest = Path(_normalize_optional_path(args.lig_spec))
    receptor_path = Path(_normalize_optional_path(args.pdb_file))
    grid_file = Path(_normalize_optional_path(args.grid_file))
    run_id = int(args.run_id or 1)
    out_root_raw = str(args.out_root or "").strip()
    ligand_rows = _load_ligand_manifest(ligand_manifest)
    ligand_label = " + ".join(Path(item["name"]).stem for item in ligand_rows)
    ligand_suffix = _sanitize_folder_name(ligand_label)

    if out_root_raw:
        out_root = Path(_normalize_optional_path(out_root_raw))
        out_dir = out_root / pdb_id / ligand_suffix / f"run{run_id}"
    else:
        out_dir = SCRIPT_DIR / pdb_id / ligand_suffix / f"run{run_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = Path(
        tempfile.mkdtemp(prefix=".multi_run_", dir=str(out_dir))
    ).resolve()

    try:
        rec_raw = work_dir / f"{pdb_id}_rec_raw.pdb"
        _extract_receptor_chain(receptor_path, chain, rec_raw)

        grid = _read_grid_file(grid_file)
        padding = _normalize_padding(args.grid_pad)
        grid = _apply_padding(grid, padding)
        grid_out = work_dir / f"{pdb_id}_gridbox.txt"
        _write_grid_file(grid_out, grid)

        receptor_pdbqt = work_dir / f"{pdb_id}_receptor.pdbqt"
        _prepare_receptor_pdbqt(
            mk_prepare_receptor,
            receptor_pdb=rec_raw,
            grid=grid,
            out_path=receptor_pdbqt,
            allow_bad_res=_env_bool("DOCKUP_MKREC_ALLOW_BAD_RES", str(args.mkrec_allow_bad_res) != "0"),
            default_altloc=str(args.mkrec_default_altloc or "A").strip() or "A",
            cwd=work_dir,
        )

        prepared_ligands: list[dict[str, Any]] = []
        ligand_chains = ["X", "Y"]
        for index, ligand_row in enumerate(ligand_rows, start=1):
            source_name = str(ligand_row["name"])
            display_name = Path(source_name).stem
            source_path = Path(ligand_row["path"])
            fixed_sdf = work_dir / f"{pdb_id}_ligand_{index}_fixed.sdf"
            ligand_pdbqt = work_dir / f"{pdb_id}_ligand_{index}.pdbqt"
            _process_ligand_sdf(source_path, fixed_sdf)
            _prepare_ligand_pdbqt(
                mk_prepare_ligand,
                fixed_sdf_path=fixed_sdf,
                out_path=ligand_pdbqt,
                cwd=work_dir,
            )
            prepared_ligands.append(
                {
                    "source_name": source_name,
                    "display_name": display_name,
                    "source_path": source_path,
                    "fixed_sdf": fixed_sdf,
                    "ligand_pdbqt": ligand_pdbqt,
                    "ligand_resname": f"M{index:02d}"[-3:],
                    "ligand_chain": ligand_chains[index - 1],
                    "ligand_resid": str(index),
                }
            )

        vina_out = work_dir / f"{pdb_id}_out_vina.pdbqt"
        vina_cmd = [
            str(vina_bin),
            "--receptor",
            str(receptor_pdbqt),
            "--ligand",
            str(prepared_ligands[0]["ligand_pdbqt"]),
            str(prepared_ligands[1]["ligand_pdbqt"]),
            "--config",
            str(grid_out),
            "--exhaustiveness",
            str(args.vina_exhaustiveness or "32"),
            "--out",
            str(vina_out),
        ]
        if str(args.vina_num_modes or "").strip():
            vina_cmd.extend(["--num_modes", str(args.vina_num_modes).strip()])
        if str(args.vina_energy_range or "").strip():
            vina_cmd.extend(["--energy_range", str(args.vina_energy_range).strip()])
        if str(args.vina_cpu or "").strip():
            vina_cmd.extend(["--cpu", str(args.vina_cpu).strip()])
        if str(args.vina_seed or "").strip():
            vina_cmd.extend(["--seed", str(args.vina_seed).strip()])
        vina_completed = _run_command(vina_cmd, cwd=work_dir, capture_output=True)
        vina_stdout = str(vina_completed.stdout or "")
        modes = _parse_vina_modes(vina_stdout)
        best_affinity = float(modes[0]["affinity_kcal_mol"]) if modes else None

        blocks = _split_first_model(vina_out)
        if len(blocks) < 2:
            raise RuntimeError("Vina multi-ligand output did not contain two ligand blocks in the first model.")
        pose_paths: list[Path] = []
        for index, block in enumerate(blocks[:2], start=1):
            pose_path = work_dir / f"{pdb_id}_pose_{index}.pdb"
            ligand_meta = prepared_ligands[index - 1]
            _write_pose_pdb(
                block,
                pose_path,
                resname=str(ligand_meta["ligand_resname"]),
                chain=str(ligand_meta["ligand_chain"]),
                resid=index,
            )
            ligand_meta["pose_pdb"] = pose_path
            pose_paths.append(pose_path)

        complex_pdb = work_dir / f"{pdb_id}_complex.pdb"
        _build_complex_pdb(rec_raw, pose_paths, complex_pdb)

        plip_dir = work_dir / "plip"
        plip_dir.mkdir(parents=True, exist_ok=True)
        if plip_cmd:
            _run_command(plip_cmd + ["-f", str(complex_pdb), "-o", str(plip_dir), "-x", "-q", "--name", "report"], cwd=work_dir)
        else:
            raise RuntimeError("PLIP executable/module not found.")
        report_xml = plip_dir / "report.xml"
        if not report_xml.exists():
            raise RuntimeError(f"PLIP did not produce report.xml under {plip_dir}")

        (out_dir / "plip").mkdir(parents=True, exist_ok=True)
        for path in [receptor_pdbqt, vina_out, grid_out, rec_raw, complex_pdb]:
            shutil.copy2(path, out_dir / path.name)
        shutil.copy2(report_xml, out_dir / "plip" / "report.xml")

        site_rows, merged_interaction_map = _build_site_payloads(
            out_dir=out_dir,
            complex_pdb=out_dir / f"{pdb_id}_complex.pdb",
            report_xml=out_dir / "plip" / "report.xml",
            receptor_pdb=out_dir / f"{pdb_id}_rec_raw.pdb",
            prepared_ligands=prepared_ligands,
            pdb_id=pdb_id,
        )
        (out_dir / "interaction_map.json").write_text(json.dumps(merged_interaction_map, indent=2), encoding="utf-8")
        (out_dir / "multi_ligand" / "sites.json").write_text(json.dumps({"sites": site_rows}, indent=2), encoding="utf-8")
        _write_results_json(
            out_dir,
            run_dir_name=out_dir.name,
            best_affinity=best_affinity,
            modes=modes,
            docking_mode="standard",
            ligand_display_name=ligand_label,
            site_rows=site_rows,
            interaction_map=merged_interaction_map,
        )

        print(f"Run complete. Results in: {out_dir}")
        if best_affinity is not None:
            print(f"Best affinity (mode 1): {best_affinity:.3f}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
