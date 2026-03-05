"""Manifest reading, writing, and docking configuration argument handling.

Consolidates manifest-related logic that was duplicated across run_start
and run_recent_continue in the original routes.py.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import BASE, DOCK_DIR, WORKSPACE_DIR
from .helpers import (
    normalize_docking_config,
    read_json,
    relative_to_base,
    restore_manifest_value,
    safe_mtime,
    write_json,
)
from .state import STATE

logger = logging.getLogger(__name__)

# Regex for run directory names
RUN_DIR_NAME_RE = re.compile(r"^run(?P<run_id>\d+)$", re.IGNORECASE)
RUN_META_DIR_NAME = ".docking_meta"


# ---------------------------------------------------------------------------
# Manifest ↔ config conversion
# ---------------------------------------------------------------------------

def config_to_manifest_values(cfg: dict[str, Any]) -> list[str]:
    """Serialise a normalised docking config dict into TSV column strings."""
    normalized = normalize_docking_config(cfg)
    return [
        str(normalized.get("pdb2pqr_ph", "")),
        str(normalized.get("pdb2pqr_ff", "")),
        str(normalized.get("pdb2pqr_ffout", "")),
        "1" if normalized.get("pdb2pqr_nodebump") else "0",
        "1" if normalized.get("pdb2pqr_keep_chain") else "0",
        "1" if normalized.get("mkrec_allow_bad_res") else "0",
        str(normalized.get("mkrec_default_altloc", "")),
        str(normalized.get("vina_exhaustiveness", "")),
        "" if normalized.get("vina_num_modes") is None else str(normalized.get("vina_num_modes")),
        "" if normalized.get("vina_energy_range") is None else str(normalized.get("vina_energy_range")),
        "" if normalized.get("vina_cpu") is None else str(normalized.get("vina_cpu")),
        "" if normalized.get("vina_seed") is None else str(normalized.get("vina_seed")),
    ]


def manifest_values_to_config(cols: list[str]) -> dict[str, Any]:
    """Deserialise TSV column strings back into a docking config dict."""
    return normalize_docking_config(
        {
            "pdb2pqr_ph": restore_manifest_value(cols[8] if len(cols) > 8 else ""),
            "pdb2pqr_ff": restore_manifest_value(cols[9] if len(cols) > 9 else ""),
            "pdb2pqr_ffout": restore_manifest_value(cols[10] if len(cols) > 10 else ""),
            "pdb2pqr_nodebump": restore_manifest_value(cols[11] if len(cols) > 11 else ""),
            "pdb2pqr_keep_chain": restore_manifest_value(cols[12] if len(cols) > 12 else ""),
            "mkrec_allow_bad_res": restore_manifest_value(cols[13] if len(cols) > 13 else ""),
            "mkrec_default_altloc": restore_manifest_value(cols[14] if len(cols) > 14 else ""),
            "vina_exhaustiveness": restore_manifest_value(cols[15] if len(cols) > 15 else ""),
            "vina_num_modes": restore_manifest_value(cols[16] if len(cols) > 16 else ""),
            "vina_energy_range": restore_manifest_value(cols[17] if len(cols) > 17 else ""),
            "vina_cpu": restore_manifest_value(cols[18] if len(cols) > 18 else ""),
            "vina_seed": restore_manifest_value(cols[19] if len(cols) > 19 else ""),
        }
    )


def append_docking_config_args(args: list[str], cfg_raw: Any) -> None:
    """Append docking config flags to an argument list (for shell command preview)."""
    cfg = normalize_docking_config(cfg_raw)
    args.extend(["--pdb2pqr_ph", str(cfg.get("pdb2pqr_ph", 7.4))])
    args.extend(["--pdb2pqr_ff", str(cfg.get("pdb2pqr_ff", "AMBER"))])
    args.extend(["--pdb2pqr_ffout", str(cfg.get("pdb2pqr_ffout", "AMBER"))])
    args.extend(["--pdb2pqr_nodebump", "1" if cfg.get("pdb2pqr_nodebump") else "0"])
    args.extend(["--pdb2pqr_keep_chain", "1" if cfg.get("pdb2pqr_keep_chain") else "0"])
    args.extend(["--mkrec_allow_bad_res", "1" if cfg.get("mkrec_allow_bad_res") else "0"])
    args.extend(["--mkrec_default_altloc", str(cfg.get("mkrec_default_altloc", "A"))])
    args.extend(["--vina_exhaustiveness", str(cfg.get("vina_exhaustiveness", 32))])

    vina_num_modes = cfg.get("vina_num_modes")
    if vina_num_modes is not None:
        args.extend(["--vina_num_modes", str(vina_num_modes)])
    vina_energy_range = cfg.get("vina_energy_range")
    if vina_energy_range is not None:
        args.extend(["--vina_energy_range", str(vina_energy_range)])
    vina_cpu = cfg.get("vina_cpu")
    if vina_cpu is not None:
        args.extend(["--vina_cpu", str(vina_cpu)])
    vina_seed = cfg.get("vina_seed")
    if vina_seed is not None:
        args.extend(["--vina_seed", str(vina_seed)])


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------

def parse_manifest_rows(manifest_path: Path) -> list[dict[str, str]]:
    """Parse a manifest.tsv file into a list of row dicts."""
    rows: list[dict[str, str]] = []
    if not manifest_path.exists():
        return rows
    try:
        lines = manifest_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        logger.debug("parse_manifest_rows: cannot read %s: %s", manifest_path, exc)
        return rows

    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = line.split("\t")
        cols += [""] * max(0, 20 - len(cols))
        cfg = manifest_values_to_config(cols)
        rows.append(
            {
                "pdb_id": restore_manifest_value(cols[0]),
                "chain": restore_manifest_value(cols[1]),
                "ligand": restore_manifest_value(cols[2]),
                "lig_spec": restore_manifest_value(cols[3]),
                "pdb_file": restore_manifest_value(cols[4]),
                "grid_pad": restore_manifest_value(cols[5]),
                "grid_file": restore_manifest_value(cols[6]),
                "force_run_id": restore_manifest_value(cols[7]),
                "docking_config": cfg,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Manifest writing — consolidates duplicate code from run_start / run_recent_continue
# ---------------------------------------------------------------------------

def write_manifest(queue: list[dict[str, Any]], manifest_path: Path | None = None) -> Path:
    """Write the queue rows into a manifest.tsv file.

    This consolidates the identical manifest-writing blocks that previously
    existed in both ``run_start`` and ``run_recent_continue``.
    """
    if manifest_path is None:
        manifest_path = DOCK_DIR / "manifest.tsv"
    with manifest_path.open("w", encoding="utf-8") as handle:
        for row in queue:
            row_cfg = normalize_docking_config(
                row.get("docking_config") or STATE.get("docking_config") or {}
            )
            ligand_val = (
                row.get("ligand_resname")
                or row.get("ligand_name")
                or row.get("ligand")
                or ""
            )
            values = [
                row.get("pdb_id", ""),
                row.get("chain", ""),
                ligand_val,
                row.get("lig_spec", ""),
                row.get("pdb_file", ""),
                row.get("grid_pad", ""),
                row.get("grid_file", ""),
                row.get("force_run_id", ""),
                *config_to_manifest_values(row_cfg),
            ]
            values = [
                "__EMPTY__" if v is None or str(v) == "" else str(v) for v in values
            ]
            handle.write("\t".join(values) + "\n")
    return manifest_path


def build_preview_command(
    queue: list[dict[str, Any]],
    out_root: str = "",
) -> str:
    """Build a human-readable preview command string from the first queue item.

    Consolidates the identical preview-command building code that previously
    existed in both ``run_start`` and ``run_recent_continue``.
    """
    if not queue:
        return ""
    first = queue[0]
    ligand_val = (
        first.get("ligand_resname")
        or first.get("ligand_name")
        or first.get("ligand")
        or ""
    )

    forced_run_id = first.get("force_run_id")
    run_id_arg = "1"
    if forced_run_id not in (None, "", "__EMPTY__"):
        try:
            run_id_arg = str(int(forced_run_id))
        except (TypeError, ValueError):
            run_id_arg = "1"

    args = [
        str(first.get("pdb_id", "")),
        str(first.get("chain", "")),
        str(ligand_val),
        "--run_id",
        run_id_arg,
    ]

    def _nonempty(val: str) -> bool:
        return bool(val) and val != "__EMPTY__"

    lig_spec = str(first.get("lig_spec", ""))
    pdb_file = str(first.get("pdb_file", ""))
    grid_pad = str(first.get("grid_pad", ""))
    grid_file = str(first.get("grid_file", ""))

    if _nonempty(lig_spec):
        args += ["--lig_spec", lig_spec]
    if _nonempty(pdb_file):
        args += ["--pdb_file", pdb_file]
    if _nonempty(grid_pad):
        args += ["--grid_pad", grid_pad]
    if _nonempty(grid_file):
        args += ["--grid_file", grid_file]
    if _nonempty(out_root):
        args += ["--out_root", out_root]
    append_docking_config_args(
        args, first.get("docking_config") or STATE.get("docking_config") or {}
    )

    return f"{BASE / 'scripts' / 'run1.sh'} " + " ".join(args)


# ---------------------------------------------------------------------------
# Ligand folder naming
# ---------------------------------------------------------------------------

def normalize_ligand_folder_name(ligand_value: str, lig_spec: str) -> str:
    """Derive a filesystem-safe folder name from a ligand value / spec pair."""
    lig_spec = str(lig_spec or "").strip()
    ligand_value = str(ligand_value or "").strip()
    if lig_spec:
        name = Path(lig_spec).stem
    else:
        src = Path(ligand_value)
        name = src.stem if src.suffix else ligand_value
    name = str(name or "").replace(" ", "_").replace("-", "_").strip()
    return name or "Native"


# ---------------------------------------------------------------------------
# Run job key helpers
# ---------------------------------------------------------------------------

def run_job_key(pdb_id: str, ligand_folder: str, run_id: int) -> tuple[str, str, int]:
    return (
        str(pdb_id or "").strip(),
        str(ligand_folder or "").strip(),
        int(run_id),
    )


def scan_existing_runs(out_root: Path) -> dict[tuple[str, str, int], dict[str, Any]]:
    """Scan an output root for existing run directories and their status."""
    index: dict[tuple[str, str, int], dict[str, Any]] = {}
    if not out_root.exists():
        return index

    for pdb_dir in sorted(out_root.iterdir(), key=lambda item: item.name.lower()):
        if not pdb_dir.is_dir():
            continue
        pdb_id = str(pdb_dir.name or "").strip()
        if not pdb_id or pdb_id.startswith("_") or pdb_id.lower() == "report_outputs":
            continue

        for ligand_dir in sorted(pdb_dir.iterdir(), key=lambda item: item.name.lower()):
            if not ligand_dir.is_dir():
                continue
            ligand_folder = str(ligand_dir.name or "").strip()
            if not ligand_folder or ligand_folder.startswith("_"):
                continue

            for run_dir in sorted(ligand_dir.iterdir(), key=lambda item: item.name.lower()):
                if not run_dir.is_dir():
                    continue
                match = RUN_DIR_NAME_RE.match(run_dir.name)
                if not match:
                    continue
                rid = int(match.group("run_id"))
                key = run_job_key(pdb_id, ligand_folder, rid)
                results_path = run_dir / "results.json"
                has_results = results_path.exists()
                last_update_ts = max(safe_mtime(run_dir), safe_mtime(results_path))
                prev = index.get(key)
                if prev is None or last_update_ts > float(prev.get("last_update_ts") or 0.0):
                    index[key] = {
                        "pdb_id": pdb_id,
                        "ligand_folder": ligand_folder,
                        "run_id": rid,
                        "run_dir": str(run_dir),
                        "has_results": has_results,
                        "last_update_ts": last_update_ts,
                    }
    return index


# ---------------------------------------------------------------------------
# Root-level run metadata
# ---------------------------------------------------------------------------

def persist_root_run_meta(
    out_root: str,
    manifest_path: Path,
    mode: str,
    planned_total_runs: int,
    queue_count: int,
    runs: int,
    session_id: str = "",
    source_session_id: str = "",
) -> dict[str, Any]:
    """Persist a run-metadata entry (latest + history) under the output root."""
    out_root_path = Path(out_root).expanduser()
    if not out_root_path.is_absolute():
        ws_candidate = (WORKSPACE_DIR / out_root_path).resolve()
        if str(out_root).startswith("data/") or str(out_root).startswith("data\\"):
            out_root_path = ws_candidate
        elif ws_candidate.parent.exists():
            out_root_path = ws_candidate
        else:
            out_root_path = (BASE / out_root_path).resolve()
    else:
        out_root_path = out_root_path.resolve()
    out_root_abs = out_root_path
    meta_dir = out_root_abs / RUN_META_DIR_NAME
    meta_dir.mkdir(parents=True, exist_ok=True)

    ts = float(time.time())
    stamp = datetime.fromtimestamp(ts).strftime("%Y%m%d_%H%M%S_%f")
    manifest_copy = meta_dir / f"manifest_{stamp}.tsv"
    try:
        manifest_copy.write_text(
            manifest_path.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("persist_root_run_meta: manifest copy failed: %s", exc)
        manifest_copy.write_text("", encoding="utf-8")

    entry_id = str(session_id or f"root_{int(ts * 1000)}")
    entry = {
        "id": entry_id,
        "mode": str(mode or "run"),
        "created_ts": ts,
        "created_at": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
        "out_root": str(out_root_abs),
        "manifest_snapshot": str(manifest_copy.resolve()),
        "planned_total_runs": max(0, int(planned_total_runs or 0)),
        "queue_count": max(0, int(queue_count or 0)),
        "runs": max(1, int(runs or 1)),
        "source_session_id": str(source_session_id or ""),
    }

    latest_path = meta_dir / "latest.json"
    history_path = meta_dir / "history.json"
    write_json(latest_path, entry)

    history_raw = read_json(history_path, {"entries": []})
    history_rows = history_raw.get("entries", []) if isinstance(history_raw, dict) else []
    if not isinstance(history_rows, list):
        history_rows = []
    history_rows.append(entry)
    history_rows.sort(key=lambda row: float(row.get("created_ts") or 0.0), reverse=True)
    history_rows = history_rows[:200]
    write_json(history_path, {"entries": history_rows})
    return entry
