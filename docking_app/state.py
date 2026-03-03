from __future__ import annotations

import threading
from typing import Any

from .config import DOCK_DIR

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
    "message": "",
    "errors": [],
    "last_logs": [],
}

# ──────────────────────────────────────────────────────────────
# State Persistence (survives hot-reloads)
# ──────────────────────────────────────────────────────────────
import json as _json
from pathlib import Path as _Path

_WORKSPACE_DIR = DOCK_DIR.parents[1]          # .../workspace
_STATE_CACHE_PATH = DOCK_DIR / ".state_cache.json"

# Fields to persist (exclude large pdb_text blobs)
_PERSIST_KEYS = (
    "mode", "selection_map", "selected_receptor", "selected_ligand",
    "selected_chain", "grid_file_path", "queue", "runs", "grid_pad",
    "docking_config", "out_root", "out_root_path", "out_root_name",
    "results_root_path",
)

_PATH_KEYS = frozenset({
    "out_root", "out_root_path", "results_root_path", "grid_file_path",
})


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

    cached_meta = raw.get("receptor_meta", [])
    if cached_meta:
        STATE["receptor_meta"] = cached_meta

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

