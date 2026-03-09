"""Service-layer functions for uploading, fetching, parsing, and scanning.

Duplicate utility functions (_boolish, _to_optional_int, _to_optional_float,
_normalize_docking_config) have been removed — import them from helpers.py.
"""
from __future__ import annotations

import json
import hashlib
import re
import shlex
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import requests
from fastapi import HTTPException, UploadFile

from . import state
from .config import BASE, DOCK_DIR, LIGAND_DIR, RECEPTOR_DIR, WORKSPACE_DIR
from .helpers import normalize_docking_config
from .manifest import RUN_META_DIR_NAME
from .state import (
    AMINO_ACIDS,
    DIST_TAG_PRIORITY,
    KIND_LABELS,
    KIND_ORDER,
    RUN_STATE,
    STATE,
)


# ---------------------------------------------------------------------------
# File upload / list
# ---------------------------------------------------------------------------

def _sanitize_upload_filename(filename: str) -> str:
    raw = str(filename or "").strip().replace("\\", "/")
    name = Path(raw).name.strip()
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid upload filename.")
    return name


def _save_uploads(files: list[UploadFile], out_dir: Path) -> list[str]:
    saved = []
    out_dir_resolved = out_dir.resolve()
    for f in files:
        safe_name = _sanitize_upload_filename(f.filename)
        out_path = (out_dir / safe_name).resolve()
        if out_path != out_dir_resolved and out_dir_resolved not in out_path.parents:
            raise HTTPException(status_code=400, detail="Invalid upload filename.")
        out_path.write_bytes(f.file.read())
        saved.append(str(out_path))
    return saved


def _existing_files(out_dir: Path, suffixes: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for suf in suffixes:
        files.extend(sorted(out_dir.glob(f"*{suf}")))
    return files


# ---------------------------------------------------------------------------
# PDB fetching / parsing
# ---------------------------------------------------------------------------

def _fetch_pdb_text(pdb_id: str) -> str | None:
    url = f"https://files.rcsb.org/download/{pdb_id.upper()}.pdb"
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None


def _parse_pdb_chains_and_ligands(pdb_text: str) -> tuple[list[str], dict[str, list[str]]]:
    chains = set()
    ligands_by_chain: dict[str, set[str]] = {}
    for line in pdb_text.splitlines():
        if line.startswith("ATOM"):
            chain = line[21].strip() or "_"
            chains.add(chain)
        elif line.startswith("HETATM"):
            resn = line[17:20].strip()
            if not resn or resn in AMINO_ACIDS or resn in {"HOH", "WAT", "H"}:
                continue
            chain = line[21].strip() or "_"
            resi = line[22:26].strip()
            lig_id = f"{resn} {resi}"
            ligands_by_chain.setdefault(chain, set()).add(lig_id)
    chains_sorted = sorted(chains) if chains else ["_"]

    def sort_key(s: str) -> tuple[str, int]:
        parts = s.split()
        try:
            return (parts[0], int(parts[1]))
        except (ValueError, IndexError):
            return (parts[0], 0)

    ligands_clean = {c: sorted(list(ligs), key=sort_key) for c, ligs in ligands_by_chain.items()}
    return chains_sorted, ligands_clean


# ---------------------------------------------------------------------------
# Receptor metadata
# ---------------------------------------------------------------------------

def _normalize_receptor_id(raw: Any) -> str:
    return str(raw or "").strip().upper()


def _resolve_receptor_file_for_id(pdb_id: str) -> Path:
    normalized = _normalize_receptor_id(pdb_id)
    for cand in _existing_files(RECEPTOR_DIR, (".pdb",)):
        if _normalize_receptor_id(cand.stem) == normalized:
            return cand
    return RECEPTOR_DIR / f"{normalized}.pdb"


def _load_receptor_meta(pdb_ids: list[str], pdb_files: list[Path]) -> list[dict[str, Any]]:
    meta: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for pdb in pdb_ids:
        pdb_id = _normalize_receptor_id(pdb)
        if not pdb_id or pdb_id in seen_ids:
            continue
        text = _fetch_pdb_text(pdb)
        if text is None:
            continue
        pdb_file = _resolve_receptor_file_for_id(pdb_id)
        try:
            if not pdb_file.exists() or pdb_file.read_text(errors="ignore") != text:
                pdb_file.write_text(text, encoding="utf-8")
        except OSError:
            pass
        chains, ligands_by_chain = _parse_pdb_chains_and_ligands(text)
        chains = ["all"] + [c for c in chains if c != "all"]
        if ligands_by_chain:
            all_ligs = sorted({lig for ligs in ligands_by_chain.values() for lig in ligs})
            ligands_by_chain = dict(ligands_by_chain)
            ligands_by_chain["all"] = all_ligs
        meta.append(
            {
                "pdb_id": pdb_id,
                "pdb_file": str(pdb_file.resolve()),
                "pdb_text": text,
                "chains": chains or ["all"],
                "ligands_by_chain": ligands_by_chain or {"all": []},
                "error": "",
            }
        )
        seen_ids.add(pdb_id)
    for f in pdb_files:
        text = f.read_text(errors="ignore")
        pdb_id = _normalize_receptor_id(f.stem)
        if not pdb_id or pdb_id in seen_ids:
            continue
        chains, ligands_by_chain = _parse_pdb_chains_and_ligands(text)
        chains = ["all"] + [c for c in chains if c != "all"]
        if ligands_by_chain:
            all_ligs = sorted({lig for ligs in ligands_by_chain.values() for lig in ligs})
            ligands_by_chain = dict(ligands_by_chain)
            ligands_by_chain["all"] = all_ligs
        meta.append(
            {
                "pdb_id": pdb_id,
                "pdb_file": str(f),
                "pdb_text": text,
                "chains": chains or ["all"],
                "ligands_by_chain": ligands_by_chain or {"all": []},
                "error": "",
            }
        )
        seen_ids.add(pdb_id)
    return meta


def _summarize_receptors(meta: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in meta:
        pdb_id = _normalize_receptor_id(item.get("pdb_id", ""))
        lig_count = sum(len(v) for v in item.get("ligands_by_chain", {}).values())
        source = "file" if item.get("pdb_file") else "pdb"
        rows.append(
            {
                "pdb_id": pdb_id,
                "chains_str": ", ".join(item.get("chains", [])),
                "chains": item.get("chains", []),
                "ligands_by_chain": item.get("ligands_by_chain", {}),
                "ligands": lig_count,
                "source": source,
                "status": "ok" if not item.get("error") else "error",
            }
        )
    return rows


def _ligand_table(meta: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    ligands_by_chain = meta.get("ligands_by_chain", {})
    for chain, ligs in ligands_by_chain.items():
        if chain == "all":
            continue
        for lig in ligs:
            rows.append({"ligand": lig, "chain": chain})
    rows = sorted(rows, key=lambda r: (r["chain"], r["ligand"]))
    return rows


# ---------------------------------------------------------------------------
# Grid file parsing
# ---------------------------------------------------------------------------

def _parse_grid_file(path: str) -> dict[str, float] | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    data: dict[str, float] = {}
    for line in p.read_text().splitlines():
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip().lower()
        try:
            data[key] = float(val.strip())
        except ValueError:
            continue
    required = {"center_x", "center_y", "center_z", "size_x", "size_y", "size_z"}
    return data if required.issubset(data.keys()) else None


def _get_meta(pdb_id: str) -> dict[str, Any] | None:
    normalized = _normalize_receptor_id(pdb_id)
    if not normalized:
        return None
    for item in STATE["receptor_meta"]:
        if _normalize_receptor_id(item.get("pdb_id")) == normalized:
            # State cache deliberately strips `pdb_text`; reload it lazily from the
            # stored local receptor file so the viewer can still open after restart.
            if not item.get("pdb_text"):
                pdb_file = str(item.get("pdb_file") or "").strip()
                if pdb_file:
                    try:
                        text = Path(pdb_file).read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        text = ""
                    if text:
                        item["pdb_text"] = text
                        chains, ligands_by_chain = _parse_pdb_chains_and_ligands(text)
                        item["chains"] = ["all"] + [c for c in chains if c != "all"] if chains else ["all"]
                        if ligands_by_chain:
                            all_ligs = sorted({lig for ligs in ligands_by_chain.values() for lig in ligs})
                            ligands_by_chain = dict(ligands_by_chain)
                            ligands_by_chain["all"] = all_ligs
                            item["ligands_by_chain"] = ligands_by_chain
                        else:
                            item["ligands_by_chain"] = {"all": []}
            return item
    return None


def _init_selection_map(meta: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    selection: dict[str, dict[str, str]] = {}
    for item in meta:
        pdb_id = _normalize_receptor_id(item.get("pdb_id"))
        if not pdb_id:
            continue
        selection[pdb_id] = {"chain": "all", "ligand_resname": ""}
    return selection


# ---------------------------------------------------------------------------
# PLIP report parsing
# ---------------------------------------------------------------------------

def _safe_float(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_plip_report(report_xml: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    if not report_xml.exists():
        return [], [], {}
    try:
        tree = ET.parse(report_xml)
        root = tree.getroot()
    except Exception:
        return [], [], {}

    ligand_info: dict[str, str] = {}
    ident = root.find(".//bindingsite/identifiers")
    if ident is not None:
        ligand_info = {
            "ligand_resname": (ident.findtext("hetid") or ident.findtext("longname") or "").strip(),
            "ligand_chain": (ident.findtext("chain") or "").strip(),
            "ligand_resid": (ident.findtext("position") or "").strip(),
        }

    interactions: list[dict[str, Any]] = []
    residue_map: dict[tuple[str, str, str], dict[str, Any]] = {}

    for inter in root.findall(".//bindingsite/interactions//*"):
        kind = (inter.tag or "").strip()
        if kind.endswith("_interactions") or kind.endswith("_bonds") or kind.endswith("_stacks") or kind.endswith("_bridges") or kind.endswith("_complexes"):
            continue
        resnr = (inter.findtext("resnr") or "").strip()
        restype = (inter.findtext("restype") or "").strip()
        reschain = (inter.findtext("reschain") or "").strip()
        if not (kind and resnr and restype and reschain):
            continue
        dist_val = None
        for tag in DIST_TAG_PRIORITY:
            raw = (inter.findtext(tag) or "").strip()
            if raw:
                dist_val = _safe_float(raw)
                if dist_val is not None:
                    break
        ligcoo = inter.find("ligcoo")
        protcoo = inter.find("protcoo")
        lig_coords = None
        prot_coords = None
        if ligcoo is not None:
            try:
                lig_coords = [
                    float(ligcoo.findtext("x") or 0),
                    float(ligcoo.findtext("y") or 0),
                    float(ligcoo.findtext("z") or 0),
                ]
            except (TypeError, ValueError):
                pass
        if protcoo is not None:
            try:
                prot_coords = [
                    float(protcoo.findtext("x") or 0),
                    float(protcoo.findtext("y") or 0),
                    float(protcoo.findtext("z") or 0),
                ]
            except (TypeError, ValueError):
                pass

        prot_atom = (inter.findtext("protatomname") or inter.findtext("donoratom") or inter.findtext("acceptoratom") or "").strip()
        lig_atom = (inter.findtext("ligatomname") or "").strip()

        interactions.append(
            {
                "kind": kind,
                "kind_label": KIND_LABELS.get(kind, kind),
                "receptor_chain": reschain,
                "receptor_resname": restype,
                "receptor_resid": resnr,
                "receptor_atom": prot_atom,
                "ligand_atom": lig_atom,
                "distance": dist_val,
                "lig_coords": lig_coords,
                "prot_coords": prot_coords,
            }
        )

        key = (reschain, restype, resnr)
        entry = residue_map.setdefault(
            key,
            {"types": set(), "instance_count": 0, "min_distance": None},
        )
        entry["types"].add(kind)
        entry["instance_count"] += 1
        if dist_val is not None:
            cur = entry.get("min_distance")
            entry["min_distance"] = dist_val if cur is None else min(cur, dist_val)

    residue_rows: list[dict[str, Any]] = []
    for (reschain, restype, resnr), info in residue_map.items():
        types = list(info["types"])
        ordered = [k for k in KIND_ORDER if k in types]
        ordered += sorted([k for k in types if k not in ordered])
        residue_rows.append(
            {
                "receptor_chain": reschain,
                "receptor_resname": restype,
                "receptor_resid": resnr,
                "interaction_types": ordered,
                "instance_count": info["instance_count"],
                "min_distance": info["min_distance"],
            }
        )

    def _sort_key(row: dict[str, Any]) -> tuple[Any, Any, Any]:
        resnr = str(row.get("receptor_resid") or "")
        try:
            resnr_val: Any = int(resnr)
        except ValueError:
            resnr_val = resnr
        return (str(row.get("receptor_chain") or ""), resnr_val, str(row.get("receptor_resname") or ""))

    residue_rows.sort(key=_sort_key)
    return interactions, residue_rows, ligand_info


def _summarize_plip(report_xml: Path) -> dict[str, Any]:
    interactions, residues, ligand_info = _parse_plip_report(report_xml)
    return {
        "interaction_count": len(interactions),
        "residue_count": len(residues),
        "ligand_resname": ligand_info.get("ligand_resname", ""),
        "ligand_chain": ligand_info.get("ligand_chain", ""),
        "ligand_resid": ligand_info.get("ligand_resid", ""),
    }


# ---------------------------------------------------------------------------
# Results scanning
# ---------------------------------------------------------------------------

def _parse_results_folder(folder: Path) -> dict[str, Any] | None:
    results_path = folder / "results.json"
    if not results_path.exists():
        return None
    try:
        data = json.loads(results_path.read_text())
    except (OSError, json.JSONDecodeError):
        data = {}
    payload: dict[str, Any] = {}
    if isinstance(data, dict) and data:
        first_key = next(iter(data))
        payload = data.get(first_key, {}) or {}
    best_affinity = _safe_float(payload.get("best_affinity"))
    rmsd = _safe_float(payload.get("rmsd"))

    pdb_id = folder.name
    run_id = None
    ligand_from_folder = ""

    parent = folder.parent
    grandparent = parent.parent if parent else None
    hierarchical_match = re.match(r"^run(?P<run>\d+)$", folder.name, re.IGNORECASE)
    new_match = re.match(r"^(?P<pdb>[^_]+)_(?P<ligand>.+)_run(?P<run>\d+)$", folder.name, re.IGNORECASE)
    old_match = re.match(r"^(?P<pdb>.+)_results_run(?P<run>\d+)$", folder.name, re.IGNORECASE)

    if hierarchical_match and grandparent:
        pdb_id = grandparent.name
        ligand_from_folder = parent.name
        try:
            run_id = int(hierarchical_match.group("run"))
        except ValueError:
            run_id = None
    elif new_match:
        pdb_id = new_match.group("pdb")
        ligand_from_folder = new_match.group("ligand")
        try:
            run_id = int(new_match.group("run"))
        except ValueError:
            run_id = None
    elif old_match:
        pdb_id = old_match.group("pdb")
        ligand_from_folder = "Native"
        try:
            run_id = int(old_match.group("run"))
        except ValueError:
            run_id = None

    interaction_map = folder / "interaction_map.json"
    ligand_resname = ""
    ligand_chain = ""
    ligand_resid = ""
    residue_count = None
    pose_path = ""
    receptor_path = ""
    complex_path = ""
    report_path = ""
    if interaction_map.exists():
        try:
            imap = json.loads(interaction_map.read_text())
        except (OSError, json.JSONDecodeError):
            imap = {}
        ligand_resname = str(imap.get("ligand_resname") or "")
        ligand_chain = str(imap.get("ligand_chain") or "")
        ligand_resid = str(imap.get("ligand_resid") or "")
        residue_count = len(imap.get("residue_summary", []) or [])

    pdb_id_upper = pdb_id.upper()
    pdb_id_lower = pdb_id.lower()

    for name in (f"{pdb_id_upper}_complex.pdb", f"{pdb_id_lower}_complex.pdb", f"{pdb_id}_complex.pdb"):
        candidate = folder / name
        if candidate.exists():
            complex_path = str(candidate)
            break

    for name in (f"{pdb_id_upper}_pose.pdb", f"{pdb_id_lower}_pose.pdb", f"{pdb_id}_pose.pdb"):
        candidate = folder / name
        if candidate.exists():
            pose_path = str(candidate)
            break

    for name in (f"{pdb_id_upper}_rec_raw.pdb", f"{pdb_id_lower}_rec_raw.pdb", f"{pdb_id}_rec_raw.pdb"):
        candidate = folder / name
        if candidate.exists():
            receptor_path = str(candidate)
            break

    report_xml = folder / "plip" / "report.xml"
    interaction_count = 0
    if report_xml.exists():
        summary = _summarize_plip(report_xml)
        interaction_count = summary.get("interaction_count", 0)
        residue_count = summary.get("residue_count", residue_count)
        if not ligand_resname:
            ligand_resname = summary.get("ligand_resname", "") or ligand_resname
        if not ligand_chain:
            ligand_chain = summary.get("ligand_chain", "") or ligand_chain
        if not ligand_resid:
            ligand_resid = summary.get("ligand_resid", "") or ligand_resid
        if not report_path:
            report_path = str(report_xml)

    ligand_display = ligand_from_folder
    if not ligand_display or ligand_display == "results":
        if ligand_resname and ligand_resname != "UNL":
            ligand_display = ligand_resname
        else:
            ligand_display = "Native"

    return {
        "name": folder.name,
        "pdb_id": pdb_id,
        "run_id": run_id,
        "best_affinity": best_affinity,
        "rmsd": rmsd,
        "ligand_resname": ligand_resname,
        "ligand_display_name": ligand_display,
        "ligand_chain": ligand_chain,
        "ligand_resid": ligand_resid,
        "interaction_count": interaction_count,
        "residue_count": residue_count,
        "result_dir": str(folder),
        "has_interaction_map": interaction_map.exists(),
        "pose_path": pose_path,
        "receptor_path": receptor_path,
        "complex_path": complex_path,
        "report_path": report_path,
    }


def _scan_results(root_path: str) -> dict[str, Any]:
    root = Path(root_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=400, detail="Results path not found.")
    entries: list[dict[str, Any]] = []
    for results_file in sorted(root.rglob("results.json")):
        folder = results_file.parent
        entry = _parse_results_folder(folder)
        if entry:
            entries.append(entry)
    entries.sort(
        key=lambda r: (
            str(r.get("pdb_id", "")),
            str(r.get("ligand_display_name") or r.get("ligand_resname") or ""),
            r.get("run_id") or 0,
        )
    )

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in entries:
        ligand_key = str(entry.get("ligand_display_name") or entry.get("ligand_resname") or "")
        key = (str(entry.get("pdb_id") or ""), ligand_key)
        groups.setdefault(key, []).append(entry)

    averages: list[dict[str, Any]] = []
    for (pdb_id, ligand_label), items in groups.items():
        aff_vals = [v for v in (it.get("best_affinity") for it in items) if v is not None]
        rmsd_vals = [v for v in (it.get("rmsd") for it in items) if v is not None]
        avg_aff = sum(aff_vals) / len(aff_vals) if aff_vals else None
        avg_rmsd = sum(rmsd_vals) / len(rmsd_vals) if rmsd_vals else None
        averages.append(
            {
                "pdb_id": pdb_id,
                "ligand_display_name": ligand_label,
                "ligand_resname": ligand_label,
                "run_count": len(items),
                "avg_affinity": avg_aff,
                "avg_rmsd": avg_rmsd,
                "min_affinity": min(aff_vals) if aff_vals else None,
                "max_affinity": max(aff_vals) if aff_vals else None,
            }
        )

    averages.sort(
        key=lambda r: (
            str(r.get("pdb_id") or ""),
            str(r.get("ligand_display_name") or r.get("ligand_resname") or ""),
        )
    )
    return {"root_path": str(root), "runs": entries, "averages": averages}


# ---------------------------------------------------------------------------
# Queue building
# ---------------------------------------------------------------------------

def _build_queue(payload: dict[str, Any]) -> list[dict[str, Any]]:
    # Respect an explicit empty selection_map from payload.
    if "selection_map" in payload:
        raw_selection = payload.get("selection_map")
        selection_map = raw_selection if isinstance(raw_selection, dict) else {}
    else:
        selection_map = STATE.get("selection_map", {})
    grid_data = payload.get("grid_data", {})
    padding = payload.get("padding", 0.0)
    run_count = payload.get("run_count", 10)
    mode = payload.get("mode", "Docking")
    docking_config = normalize_docking_config(
        payload.get("docking_config") or STATE.get("docking_config") or {}
    )

    ligand_files = _existing_files(LIGAND_DIR, (".sdf",))
    ligand_file_map = {lig.name: lig for lig in ligand_files}
    active_ligands = [str(name or "").strip() for name in STATE.get("active_ligands", [])]
    active_ligands = [name for name in active_ligands if name in ligand_file_map]
    active_set = set(active_ligands)
    entries: list[dict[str, Any]] = []

    batch_id = int(time.time() * 1000)

    def _safe_out_root() -> Path:
        raw = str(STATE.get("out_root") or "").strip()
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = (WORKSPACE_DIR / candidate).resolve()
        else:
            candidate = candidate.resolve()
        dock_root = DOCK_DIR.resolve()
        if candidate != dock_root and dock_root not in candidate.parents:
            return dock_root
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    def _grid_signature(pdb_id: str, grid: dict[str, Any]) -> str:
        payload = {
            "pdb_id": str(pdb_id or "").strip().upper(),
            "cx": float(grid.get("cx", 0.0)),
            "cy": float(grid.get("cy", 0.0)),
            "cz": float(grid.get("cz", 0.0)),
            "sx": float(grid.get("sx", 0.0)),
            "sy": float(grid.get("sy", 0.0)),
            "sz": float(grid.get("sz", 0.0)),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    out_root = _safe_out_root()
    grid_store_dir = out_root / "_grid"
    grid_store_dir.mkdir(parents=True, exist_ok=True)

    for meta in STATE["receptor_meta"]:
        pdb_id = meta["pdb_id"]

        if pdb_id not in selection_map:
            continue

        sel = selection_map[pdb_id]
        chain = sel.get("chain", "all")
        selected_ligand = str(sel.get("ligand_resname", "") or sel.get("ligand", "")).strip()

        grid_info = grid_data.get(pdb_id)
        if not grid_info:
            raise HTTPException(
                status_code=400,
                detail=f"Grid parameters not set for {pdb_id}. Please create/set a gridbox before building the queue.",
            )

        if not selected_ligand:
            if mode == "Redocking":
                detail = (
                    f"No ligand selected for {pdb_id}. Please choose a native ligand before building the queue."
                )
            else:
                detail = (
                    f"No ligand selected for {pdb_id}. Please choose a dock-ready ligand "
                    "or 'All Ligands (Dock All)' before building the queue."
                )
            raise HTTPException(status_code=400, detail=detail)

        target_ligands = []

        if mode == "Redocking":
            target_ligands = [{"name": selected_ligand, "path": ""}]
        else:
            if selected_ligand == "all_set":
                if not active_ligands:
                    raise HTTPException(
                        status_code=400,
                        detail="No dock-ready ligands selected. Use Add Selected in Ligands section.",
                    )
                target_ligands = [{"name": name, "path": str(ligand_file_map[name])} for name in active_ligands]
            else:
                if selected_ligand not in active_set:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Ligand '{selected_ligand}' is not in dock-ready ligands.",
                    )
                lig = ligand_file_map.get(selected_ligand)
                if lig is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Ligand file not found for '{selected_ligand}'.",
                    )
                target_ligands = [{"name": lig.name, "path": str(lig)}]

        grid_sig = _grid_signature(pdb_id, grid_info)
        grid_file_path = grid_store_dir / f"{pdb_id}_{grid_sig}.txt"
        if not grid_file_path.exists():
            grid_file_path.write_text(
                "\n".join(
                    [
                        f"center_x = {grid_info['cx']}",
                        f"center_y = {grid_info['cy']}",
                        f"center_z = {grid_info['cz']}",
                        f"size_x = {grid_info['sx']}",
                        f"size_y = {grid_info['sy']}",
                        f"size_z = {grid_info['sz']}",
                    ]
                )
                + "\n"
            )

        for lig_obj in target_ligands:
            final_grid = None
            if grid_info:
                final_grid = grid_info.copy()
                if padding > 0:
                    final_grid["sx"] += padding
                    final_grid["sy"] += padding
                    final_grid["sz"] += padding

            ligand_label = lig_obj["name"] or ""
            ligand_resname = ligand_label
            if mode == "Redocking" and ligand_label:
                ligand_resname = ligand_label.split()[0]

            entries.append({
                "batch_id": batch_id,
                "job_type": mode,
                "pdb_id": pdb_id,
                "chain": chain,
                "ligand_name": ligand_label,
                "ligand_resname": ligand_resname,
                "lig_spec": lig_obj["path"],
                "pdb_file": meta.get("pdb_file", ""),
                "grid_params": final_grid,
                "grid_pad": padding,
                "grid_file": str(grid_file_path),
                "padding": padding,
                "run_count": run_count,
                "docking_config": docking_config,
            })

    return entries


# ---------------------------------------------------------------------------
# Run execution
# ---------------------------------------------------------------------------

def _start_run(
    manifest_path: Path,
    runs: int,
    out_root: str,
    total_runs: int,
    initial_command: str = "",
    is_test_mode: bool = False,
) -> None:
    script_dir = BASE / "scripts"
    batch_script = DOCK_DIR / "run_batch.sh"
    out_root_path = Path(out_root).expanduser()
    if not out_root_path.is_absolute():
        # data/dock/... lives under WORKSPACE_DIR, not BASE
        ws_candidate = (WORKSPACE_DIR / out_root_path).resolve()
        if str(out_root).startswith("data/") or str(out_root).startswith("data\\"):
            out_root_path = ws_candidate
        elif ws_candidate.parent.exists():
            out_root_path = ws_candidate
        else:
            out_root_path = (BASE / out_root_path).resolve()
    else:
        # Even if absolute, fix paths from old/wrong workspace
        ws_str = str(WORKSPACE_DIR.resolve())
        if not str(out_root_path).startswith(ws_str):
            # Try to salvage by finding 'data/' segment and re-rooting
            parts = out_root_path.parts
            try:
                idx = next(i for i, x in enumerate(parts) if x == "data")
                rel = Path(*parts[idx:])
                out_root_path = (WORKSPACE_DIR / rel).resolve()
            except StopIteration:
                pass  # Can't fix, will fail later with a clear error
    dock_root = DOCK_DIR.resolve()
    if out_root_path != dock_root and dock_root not in out_root_path.parents:
        raise HTTPException(status_code=400, detail="out_root must stay inside data/dock.")
    out_root_path = out_root_path.resolve()
    out_root_path.mkdir(parents=True, exist_ok=True)
    run_meta_dir = out_root_path / RUN_META_DIR_NAME
    run_meta_dir.mkdir(parents=True, exist_ok=True)
    batch_stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int((time.time() % 1) * 1000):03d}"
    batch_log_path = run_meta_dir / f"batch_{batch_stamp}.log"
    runtime_status_path = run_meta_dir / "runtime_status.json"

    def _write_runtime_status() -> None:
        payload = {
            "status": RUN_STATE.get("status", "idle"),
            "returncode": RUN_STATE.get("returncode"),
            "start_time": RUN_STATE.get("start_time"),
            "total_runs": int(RUN_STATE.get("total_runs", 0) or 0),
            "completed_runs": int(RUN_STATE.get("completed_runs", 0) or 0),
            "command": str(RUN_STATE.get("command", "")),
            "batch_log_path": str(batch_log_path),
            "updated_ts": float(time.time()),
        }
        tmp_path = runtime_status_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        tmp_path.replace(runtime_status_path)

    if is_test_mode:
        mock_logic = (
            "    echo \"RUN1: [TEST MODE] mocking docking for $pdb $chain $ligand\"\n"
            "    sleep 0.5\n"
            "    LIGAND_SUFFIX=\"${ligand%.sdf}\"\n"
            "    LIGAND_SUFFIX=\"${LIGAND_SUFFIX// /_}\"\n"
            "    LIGAND_SUFFIX=\"${LIGAND_SUFFIX:-Native}\"\n"
            "    MOCK_DIR=\"$OUT_ROOT/$pdb/$LIGAND_SUFFIX/run$run_id\"\n"
            "    mkdir -p \"$MOCK_DIR\"\n"
            "    echo '{\"mock\": {\"best_affinity\": -8.5, \"rmsd\": 1.2}}' > \"$MOCK_DIR/results.json\"\n"
            "    total_elapsed=$(( $(date +%s) - batch_start_epoch ))\n"
            "    echo \"[$(ts)] DONE $run_idx/$run_total | $pdb $chain $ligand (run_id=$run_id) | run=${SECONDS}s | batch=${total_elapsed}s\"\n"
        )
    else:
        mock_logic = (
            "    args=(\"$pdb\" \"$chain\" \"$ligand\" --run_id \"$run_id\")\n"
            "    ! is_empty \"$lig_spec\" && args+=(--lig_spec \"$lig_spec\")\n"
            "    ! is_empty \"$pdb_file\" && args+=(--pdb_file \"$pdb_file\")\n"
            "    ! is_empty \"$grid_pad\" && args+=(--grid_pad \"$grid_pad\")\n"
            "    ! is_empty \"$grid_file\" && args+=(--grid_file \"$grid_file\")\n"
            "    ! is_empty \"$pdb2pqr_ph\" && args+=(--pdb2pqr_ph \"$pdb2pqr_ph\")\n"
            "    ! is_empty \"$pdb2pqr_ff\" && args+=(--pdb2pqr_ff \"$pdb2pqr_ff\")\n"
            "    ! is_empty \"$pdb2pqr_ffout\" && args+=(--pdb2pqr_ffout \"$pdb2pqr_ffout\")\n"
            "    ! is_empty \"$pdb2pqr_nodebump\" && args+=(--pdb2pqr_nodebump \"$pdb2pqr_nodebump\")\n"
            "    ! is_empty \"$pdb2pqr_keep_chain\" && args+=(--pdb2pqr_keep_chain \"$pdb2pqr_keep_chain\")\n"
            "    ! is_empty \"$mkrec_allow_bad_res\" && args+=(--mkrec_allow_bad_res \"$mkrec_allow_bad_res\")\n"
            "    ! is_empty \"$mkrec_default_altloc\" && args+=(--mkrec_default_altloc \"$mkrec_default_altloc\")\n"
            "    ! is_empty \"$vina_exhaustiveness\" && args+=(--vina_exhaustiveness \"$vina_exhaustiveness\")\n"
            "    ! is_empty \"$vina_num_modes\" && args+=(--vina_num_modes \"$vina_num_modes\")\n"
            "    ! is_empty \"$vina_energy_range\" && args+=(--vina_energy_range \"$vina_energy_range\")\n"
            "    ! is_empty \"$vina_cpu\" && args+=(--vina_cpu \"$vina_cpu\")\n"
            "    ! is_empty \"$vina_seed\" && args+=(--vina_seed \"$vina_seed\")\n"
            "    ! is_empty \"$OUT_ROOT\" && args+=(--out_root \"$OUT_ROOT\")\n"
            "    echo \"[$(ts)] RUN $run_idx/$run_total | $pdb $chain $ligand (run_id=$run_id)\"\n"
            "    echo \"RUN1: $SCRIPT_DIR/run1.sh ${args[*]}\"\n"
            "    if bash \"$SCRIPT_DIR/run1.sh\" \"${args[@]}\"; then\n"
            "      total_elapsed=$(( $(date +%s) - batch_start_epoch ))\n"
            "      echo \"[$(ts)] DONE $run_idx/$run_total | $pdb $chain $ligand (run_id=$run_id) | run=${SECONDS}s | batch=${total_elapsed}s\"\n"
            "    else\n"
            "      code=$?\n"
            "      total_elapsed=$(( $(date +%s) - batch_start_epoch ))\n"
            "      echo \"[$(ts)] FAIL $run_idx/$run_total | $pdb $chain $ligand (run_id=$run_id) | exit=$code | run=${SECONDS}s | batch=${total_elapsed}s\"\n"
            "      exit $code\n"
            "    fi\n"
        )

    script_lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"SCRIPT_DIR={shlex.quote(str(script_dir))}",
        f"MANIFEST={shlex.quote(str(manifest_path))}",
        f"RUNS=\"{runs}\"",
        f"TOTAL_RUNS=\"{total_runs}\"",
        f"OUT_ROOT={shlex.quote(str(out_root_path))}",
        "ts() { date '+%Y-%m-%d %H:%M:%S'; }",
        "is_empty() { [[ -z \"$1\" || \"$1\" == \"__EMPTY__\" ]]; }",
        "job_total=$(grep -vE '^\\s*$|^#' \"$MANIFEST\" | wc -l | awk '{print $1}')",
        "run_total=${TOTAL_RUNS:-$((job_total * RUNS))}",
        "run_idx=0",
        "batch_start_epoch=$(date +%s)",
        "echo \"[$(ts)] Batch start | jobs=$job_total runs=$RUNS total_runs=$run_total\"",
        "while IFS=$'\\t' read -r pdb chain ligand lig_spec pdb_file grid_pad grid_file force_run_id pdb2pqr_ph pdb2pqr_ff pdb2pqr_ffout pdb2pqr_nodebump pdb2pqr_keep_chain mkrec_allow_bad_res mkrec_default_altloc vina_exhaustiveness vina_num_modes vina_energy_range vina_cpu vina_seed; do",
        "  [[ -z \"$pdb\" || \"$pdb\" =~ ^# ]] && continue",
        "  run_start=1",
        "  run_end=$RUNS",
        "  if ! is_empty \"$force_run_id\"; then",
        "    run_start=$force_run_id",
        "    run_end=$force_run_id",
        "  fi",
        "  for ((run_id=run_start; run_id<=run_end; run_id++)); do",
        "    run_idx=$((run_idx + 1))",
        "    start_ts=$(ts)",
        "    SECONDS=0",
        mock_logic,
        "  done",
        "done < \"$MANIFEST\"",
        "echo \"[$(ts)] Batch done\"",
    ]
    batch_script.write_text("\n".join(script_lines) + "\n")
    batch_script.chmod(0o755)
    cmd = ["bash", str(batch_script)]
    state.RUN_PROC = subprocess.Popen(
        cmd,
        cwd=str(BASE),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )

    def _reader() -> None:
        proc = state.RUN_PROC
        if not proc or not proc.stdout:
            return
        done_line_re = re.compile(r"\bDONE\s+(\d+)/(\d+)\b")
        with batch_log_path.open("a", encoding="utf-8", errors="ignore") as log_handle:
            for line in proc.stdout:
                line_clean = line.rstrip()
                if line_clean.startswith("RUN1:"):
                    RUN_STATE["command"] = line_clean.replace("RUN1:", "", 1).strip()
                RUN_STATE["log_lines"].append(line_clean)
                done_match = done_line_re.search(line_clean)
                if done_match:
                    done_idx = int(done_match.group(1))
                    done_total = int(done_match.group(2))
                    if done_total > 0 and int(RUN_STATE.get("total_runs", 0) or 0) <= 0:
                        RUN_STATE["total_runs"] = done_total
                    total_bound = max(1, int(RUN_STATE.get("total_runs", done_total) or done_total))
                    RUN_STATE["completed_runs"] = max(
                        int(RUN_STATE.get("completed_runs", 0) or 0),
                        min(done_idx, total_bound),
                    )
                elif "Run complete." in line_clean:
                    RUN_STATE["completed_runs"] = min(
                        int(RUN_STATE.get("completed_runs", 0) or 0) + 1,
                        max(1, int(RUN_STATE.get("total_runs", 0) or 1)),
                    )
                RUN_STATE["log_lines"] = RUN_STATE["log_lines"][-400:]
                log_handle.write(line_clean + "\n")
                log_handle.flush()
                _write_runtime_status()
        proc.wait()
        RUN_STATE["returncode"] = proc.returncode
        if RUN_STATE.get("status") in {"stopping", "stopped"}:
            RUN_STATE["status"] = "stopped"
        else:
            RUN_STATE["status"] = "done" if proc.returncode == 0 else "error"
        state.RUN_PROC = None
        _write_runtime_status()

    RUN_STATE["status"] = "running"
    RUN_STATE["returncode"] = None
    RUN_STATE["log_lines"] = []
    RUN_STATE["command"] = initial_command or " ".join(cmd)
    RUN_STATE["out_root"] = str(out_root_path)
    RUN_STATE["start_time"] = time.time()
    RUN_STATE["total_runs"] = total_runs
    RUN_STATE["completed_runs"] = 0
    RUN_STATE["batch_log_path"] = str(batch_log_path)
    _write_runtime_status()
    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
