from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .config import DOCK_DIR, LIGAND_DIR, RECEPTOR_DIR

AMINO_ACIDS = {
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLU", "GLN", "GLY",
    "HIS", "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER",
    "THR", "TRP", "TYR", "VAL",
}

DIST_TAG_PRIORITY = ("dist_h-a", "dist", "centdist", "dist_d-a")

KIND_LABELS: dict[str, str] = {
    "hydrogen_bond": "H-bond",
    "hydrophobic_interaction": "Hydrophobic",
    "pi_stack": "pi-stacking",
    "pi_cation_interaction": "pi-cation",
    "salt_bridge": "Salt bridge",
    "halogen_bond": "Halogen bond",
    "water_bridge": "Water bridge",
    "metal_complex": "Metal complex",
}

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

DOCKING_CONFIG_DEFAULTS: dict[str, Any] = {
    "docking_mode": "standard",
    "pdb2pqr_ph": 7.4,
    "pdb2pqr_ff": "AMBER",
    "pdb2pqr_ffout": "AMBER",
    "pdb2pqr_nodebump": True,
    "pdb2pqr_keep_chain": True,
    "mkrec_allow_bad_res": True,
    "mkrec_default_altloc": "A",
    "vina_exhaustiveness": 32,
    "vina_num_modes": None,
    "vina_energy_range": None,
    "vina_cpu": None,
    "vina_seed": None,
}

STATE: dict[str, Any] = {
    "mode": "Docking",
    "receptor_meta": [],
    "selection_map": {},
    "selected_ids": [],
    "selected_receptor": "",
    "selected_ligand": "",
    "selected_chain": "all",
    "active_ligands": [],
    "grid_file_path": "",
    "queue": [],
    "runs": 1,
    "grid_pad": "",
    "docking_config": dict(DOCKING_CONFIG_DEFAULTS),
    "out_root": str(DOCK_DIR),
    "out_root_path": str(DOCK_DIR),
    "out_root_name": "",
    "results_root_path": str(DOCK_DIR),
}

RUN_STATE: dict[str, Any] = {
    "status": "idle",
    "log_lines": [],
    "returncode": None,
    "command": "",
    "out_root": "",
    "start_time": None,
    "total_runs": 0,
    "completed_runs": 0,
}

RUN_LOCK = threading.Lock()
RUN_PROC: Any = None

REPORT_STATE: dict[str, Any] = {
    "status": "idle",
    "task": "",
    "progress": 0,
    "total": 0,
    "expected_time": 0,
    "message": "",
    "errors": [],
    "last_logs": [],
    "cancel_requested": False,
    "current_receptor": "",
    "current_ligand": "",
    "current_run": "",
    "render_mode": "",
    "active_subprocess_pid": None,
    "active_subprocess_label": "",
}

# ──────────────────────────────────────────────────────────────
# State Persistence (survives hot-reloads)
# ──────────────────────────────────────────────────────────────
import json as _json
from pathlib import Path as _Path

_WORKSPACE_DIR = DOCK_DIR.parents[1]          # .../workspace
_STATE_CACHE_PATH = DOCK_DIR / ".state_cache.json"
_RECEPTOR_DIR_RESOLVED = RECEPTOR_DIR.resolve()
_LIGAND_DIR_RESOLVED = LIGAND_DIR.resolve()

# Fields to persist (exclude large pdb_text blobs)
_PERSIST_KEYS = (
    "mode", "selected_receptor", "active_ligands", "queue", "runs", "grid_pad",
    "docking_config", "out_root", "out_root_path", "out_root_name",
    "results_root_path",
)

_PATH_KEYS = frozenset({
    "out_root", "out_root_path", "results_root_path",
})


def _normalize_receptor_id(raw: Any) -> str:
    return str(raw or "").strip().upper()


def _normalize_flex_residue_rows(raw: Any) -> list[dict[str, str]]:
    if isinstance(raw, str):
        values = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(raw, list):
        values = raw
    else:
        return []
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in values:
        if isinstance(item, str):
            parts = [part.strip() for part in item.split(":") if part.strip()]
            if len(parts) < 2:
                continue
            chain = parts[0]
            resno = parts[1]
            resname = parts[2].upper() if len(parts) > 2 else ""
        elif isinstance(item, dict):
            chain = str(item.get("chain") or "").strip()
            resno = str(item.get("resno") or item.get("resid") or "").strip()
            resname = str(item.get("resname") or item.get("residue_name") or "").strip().upper()
        else:
            continue
        if not chain or not resno:
            continue
        key = (chain, resno, resname)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"chain": chain, "resno": resno, "resname": resname})
    return normalized


def _normalize_selection_map(raw_map: Any) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    if not isinstance(raw_map, dict):
        return normalized
    for raw_key, raw_val in raw_map.items():
        pdb_id = _normalize_receptor_id(raw_key)
        if not pdb_id:
            continue
        source = raw_val if isinstance(raw_val, dict) else {}
        ligand_names = source.get("ligand_resnames")
        if not ligand_names:
            ligand_raw = str(source.get("ligand_resname", "") or "").strip()
            ligand_names = [ligand_raw] if ligand_raw and ligand_raw != "all_set" else []
        normalized[pdb_id] = {
            "chain": str(source.get("chain", "all") or "all"),
            "ligand_resname": str(source.get("ligand_resname", "") or ""),
            "ligand_resnames": [str(item or "").strip() for item in ligand_names if str(item or "").strip()],
            "flex_residues": _normalize_flex_residue_rows(source.get("flex_residues") or source.get("flex_residue_spec") or []),
        }
    return normalized


def _normalize_active_ligands(raw_list: Any) -> list[str]:
    available = {p.name for p in _LIGAND_DIR_RESOLVED.glob("*.sdf") if p.is_file()}
    out: list[str] = []
    seen: set[str] = set()
    values = raw_list if isinstance(raw_list, list) else []
    for raw in values:
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        if name not in available:
            continue
        out.append(name)
        seen.add(name)
    return out


def _normalize_cached_receptor_meta(raw_meta: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not isinstance(raw_meta, list):
        return normalized
    for raw_item in raw_meta:
        if not isinstance(raw_item, dict):
            continue
        entry = dict(raw_item)
        pdb_id = _normalize_receptor_id(entry.get("pdb_id"))
        if not pdb_id or pdb_id in seen:
            continue
        if pdb_id.startswith("TMP_PROBE"):
            continue
        pdb_file = str(entry.get("pdb_file") or "").strip()
        if pdb_file:
            resolved = Path(pdb_file).expanduser().resolve()
            if resolved != _RECEPTOR_DIR_RESOLVED and _RECEPTOR_DIR_RESOLVED not in resolved.parents:
                continue
            if not resolved.exists() or not resolved.is_file():
                continue
            entry["pdb_file"] = str(resolved)
        entry["pdb_id"] = pdb_id
        normalized.append(entry)
        seen.add(pdb_id)
    return normalized


def _path_is_stale(raw: str) -> bool:
    """True when an absolute path does NOT live under current WORKSPACE_DIR."""
    if not raw:
        return False
    s = str(raw)
    if "/old/" in s or "\\old\\" in s:
        return True
    p = _Path(s)
    if p.is_absolute() and not s.startswith(str(_WORKSPACE_DIR)):
        return True
    return False


def _fix_path(raw: str) -> str:
    """Re-root a stale absolute path under current WORKSPACE_DIR."""
    if not raw:
        return raw
    parts = _Path(raw).parts
    for i, segment in enumerate(parts):
        if segment == "data":
            return str(_WORKSPACE_DIR / _Path(*parts[i:]))
    return raw


def save_state_cache() -> None:
    """Write key STATE fields to disk so they survive hot-reloads."""
    try:
        payload: dict[str, Any] = {k: STATE[k] for k in _PERSIST_KEYS if k in STATE}
        stripped_meta = []
        for m in STATE.get("receptor_meta", []):
            entry = {k: v for k, v in m.items() if k != "pdb_text"}
            entry["needs_refetch"] = not m.get("pdb_file")
            stripped_meta.append(entry)
        payload["receptor_meta"] = stripped_meta
        tmp = _STATE_CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(_json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        tmp.replace(_STATE_CACHE_PATH)
    except Exception:
        pass


def load_state_cache() -> None:
    """Restore STATE from disk cache, migrating stale paths."""
    if not _STATE_CACHE_PATH.exists():
        return
    try:
        raw = _json.loads(_STATE_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return

    for k in _PERSIST_KEYS:
        if k not in raw:
            continue
        val = raw[k]
        if k in _PATH_KEYS and isinstance(val, str) and _path_is_stale(val):
            val = _fix_path(val)
        STATE[k] = val

    STATE["selection_map"] = _normalize_selection_map(STATE.get("selection_map", {}))
    STATE["active_ligands"] = _normalize_active_ligands(raw.get("active_ligands", STATE.get("active_ligands", [])))

    cached_meta = _normalize_cached_receptor_meta(raw.get("receptor_meta", []))
    STATE["receptor_meta"] = cached_meta
    known_ids = {item.get("pdb_id", "") for item in cached_meta}
    if known_ids:
        STATE["selection_map"] = {
            pid: STATE["selection_map"].get(
                pid,
                {"chain": "all", "ligand_resname": "", "ligand_resnames": [], "flex_residues": []},
            )
            for pid in known_ids
            if pid
        }
    else:
        STATE["selection_map"] = {}
    for pdb_id in known_ids:
        STATE["selection_map"].setdefault(
            pdb_id,
            {"chain": "all", "ligand_resname": "", "ligand_resnames": [], "flex_residues": []},
        )
    if STATE["receptor_meta"]:
        selected = _normalize_receptor_id(STATE.get("selected_receptor", ""))
        if selected not in known_ids:
            selected = STATE["receptor_meta"][0]["pdb_id"]
        STATE["selected_receptor"] = selected
    else:
        STATE["selected_receptor"] = ""
        STATE["selected_ligand"] = ""
        STATE["selected_chain"] = "all"

    # Drop queue if any job has stale paths
    queue = STATE.get("queue", [])
    if queue and any(
        _path_is_stale(str(j.get("pdb_file", "")))
        or _path_is_stale(str(j.get("lig_spec", "")))
        for j in queue
    ):
        STATE["queue"] = []


# Load cache immediately on import (covers hot-reload scenario)
load_state_cache()
