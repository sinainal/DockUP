"""Run session management — tracking docking sessions for resume / recent display.

Extracted from routes.py L406-L798 to keep route handlers thin.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import BASE, DOCK_DIR, WORKSPACE_DIR
from .helpers import normalize_docking_config, read_json, safe_mtime, write_json
from .manifest import (
    RUN_DIR_NAME_RE,
    RUN_META_DIR_NAME,
    normalize_ligand_folder_name,
    parse_manifest_rows,
    run_job_key,
    scan_existing_runs,
)
from .state import RUN_STATE, STATE

logger = logging.getLogger(__name__)

RUN_SESSION_DIR = DOCK_DIR / ".sessions"
RUN_SESSION_INDEX = RUN_SESSION_DIR / "index.json"


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------

def load_run_sessions() -> list[dict[str, Any]]:
    raw = read_json(RUN_SESSION_INDEX, {"sessions": []})
    if isinstance(raw, dict):
        rows = raw.get("sessions", [])
    elif isinstance(raw, list):
        rows = raw
    else:
        rows = []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        session_id = str(row.get("id") or "").strip()
        out_root = str(row.get("out_root") or "").strip()
        manifest_snapshot = str(row.get("manifest_snapshot") or "").strip()
        if not (session_id and out_root and manifest_snapshot):
            continue
        entry = {
            "id": session_id,
            "created_ts": float(row.get("created_ts") or 0.0),
            "dock_root": str(row.get("dock_root") or Path(out_root).name),
            "out_root": out_root,
            "manifest_snapshot": manifest_snapshot,
            "runs": int(row.get("runs") or 1),
            "planned_total": int(row.get("planned_total") or 0),
        }
        out.append(entry)
    return out


def save_run_sessions(entries: list[dict[str, Any]]) -> None:
    write_json(RUN_SESSION_INDEX, {"sessions": entries})


def register_run_session(
    out_root: str,
    runs: int,
    manifest_path: Path,
    planned_total: int = 0,
) -> dict[str, Any]:
    out_root_abs = str(Path(out_root).expanduser().resolve())
    manifest_abs = Path(manifest_path).expanduser().resolve()
    run_count = max(1, int(runs or 1))
    session_id = f"sess_{int(time.time() * 1000)}"
    session_dir = RUN_SESSION_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = session_dir / "manifest.tsv"
    try:
        snapshot_path.write_text(
            manifest_abs.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("register_run_session: manifest copy failed: %s", exc)
        snapshot_path.write_text("", encoding="utf-8")

    entry = {
        "id": session_id,
        "created_ts": float(time.time()),
        "dock_root": Path(out_root_abs).name,
        "out_root": out_root_abs,
        "manifest_snapshot": str(snapshot_path.resolve()),
        "runs": run_count,
        "planned_total": max(0, int(planned_total or 0)),
    }
    sessions = load_run_sessions()
    sessions.append(entry)
    sessions.sort(key=lambda row: float(row.get("created_ts") or 0.0), reverse=True)
    sessions = sessions[:200]
    save_run_sessions(sessions)
    return entry


# ---------------------------------------------------------------------------
# Legacy session discovery
# ---------------------------------------------------------------------------

def build_legacy_session_entry() -> dict[str, Any] | None:
    plan: dict[str, Any] = {"out_root": "", "runs": 0, "manifest": "", "planned_total": 0}
    batch_path = DOCK_DIR / "run_batch.sh"
    manifest_path = DOCK_DIR / "manifest.tsv"
    if not batch_path.exists():
        return None

    try:
        content = batch_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        content = ""

    out_root = str(DOCK_DIR.resolve())
    match_out = re.search(r'^OUT_ROOT="([^"]+)"', content, flags=re.MULTILINE)
    if match_out:
        out_root = str(match_out.group(1) or "").strip()
    out_root_path = Path(out_root).expanduser()
    if not out_root_path.is_absolute():
        ws = (WORKSPACE_DIR / out_root_path).resolve()
        out_root_path = ws if (str(out_root).startswith("data/") or ws.parent.exists()) else (BASE / out_root_path).resolve()
    out_root_abs = str(out_root_path.resolve())
    plan["out_root"] = out_root_abs

    match_runs = re.search(r'^RUNS="(\d+)"', content, flags=re.MULTILINE)
    if match_runs:
        try:
            plan["runs"] = int(match_runs.group(1))
        except (TypeError, ValueError):
            plan["runs"] = 0
    match_total_runs = re.search(r'^TOTAL_RUNS="(\d+)"', content, flags=re.MULTILINE)
    if match_total_runs:
        try:
            plan["planned_total"] = int(match_total_runs.group(1))
        except (TypeError, ValueError):
            plan["planned_total"] = 0

    match_manifest = re.search(r'^MANIFEST="([^"]+)"', content, flags=re.MULTILINE)
    if match_manifest:
        plan["manifest"] = str(match_manifest.group(1) or "").strip()
    if not plan["manifest"]:
        plan["manifest"] = str(manifest_path.resolve())

    manifest_candidate = Path(plan["manifest"]).expanduser()
    if not manifest_candidate.is_absolute():
        ws = (WORKSPACE_DIR / manifest_candidate).resolve()
        manifest_candidate = ws if (str(plan["manifest"]).startswith("data/") or ws.parent.exists()) else (BASE / manifest_candidate).resolve()
    if not manifest_candidate.exists():
        return None

    created_ts = max(safe_mtime(batch_path), safe_mtime(manifest_candidate))
    return {
        "id": f"legacy::{out_root_abs}",
        "created_ts": created_ts,
        "dock_root": Path(out_root_abs).name,
        "out_root": out_root_abs,
        "manifest_snapshot": str(manifest_candidate.resolve()),
        "runs": max(1, int(plan["runs"] or 1)),
        "planned_total": max(0, int(plan["planned_total"] or 0)),
        "legacy": True,
    }


# ---------------------------------------------------------------------------
# Resume session collection
# ---------------------------------------------------------------------------

def collect_resume_sessions() -> list[dict[str, Any]]:
    sessions = load_run_sessions()
    cleaned: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    seen_out_roots: set[str] = set()
    for row in sessions:
        manifest_path = Path(str(row.get("manifest_snapshot") or "")).expanduser()
        out_root = str(row.get("out_root") or "").strip()
        if not out_root:
            continue
        if not manifest_path.is_absolute():
            ws = (WORKSPACE_DIR / manifest_path).resolve()
            manifest_path = ws if (str(manifest_path).startswith("data/") or ws.parent.exists()) else (BASE / manifest_path).resolve()
        if not manifest_path.exists():
            continue
        key = (str(Path(out_root).resolve()), str(manifest_path.resolve()))
        if key in seen:
            continue
        seen.add(key)
        item = dict(row)
        item["out_root"] = str(Path(out_root).expanduser().resolve())
        item["manifest_snapshot"] = str(manifest_path.resolve())
        item["runs"] = max(1, int(item.get("runs") or 1))
        item["planned_total"] = max(0, int(item.get("planned_total") or 0))
        cleaned.append(item)
        seen_out_roots.add(item["out_root"])

    legacy = build_legacy_session_entry()
    if legacy:
        legacy_out_root = str(Path(legacy["out_root"]).resolve())
        key = (legacy_out_root, str(Path(legacy["manifest_snapshot"]).resolve()))
        if legacy_out_root not in seen_out_roots and key not in seen:
            cleaned.append(legacy)

    cleaned.sort(key=lambda row: float(row.get("created_ts") or 0.0), reverse=True)
    return cleaned


# ---------------------------------------------------------------------------
# Scan recent incomplete rows
# ---------------------------------------------------------------------------

def scan_recent_incomplete_rows(
    limit: int = 50,
    include_jobs: bool = False,
) -> list[dict[str, Any]]:
    """Scan recent docking sessions and return rows with incomplete runs."""
    normalized_limit = max(1, min(200, int(limit or 50)))
    now_ts = time.time()
    active_status = str(RUN_STATE.get("status") or "")
    active_out_root_raw = str(RUN_STATE.get("out_root") or "").strip()
    active_out_root = ""
    if active_out_root_raw:
        try:
            active_out_root = str(Path(active_out_root_raw).expanduser().resolve())
        except OSError:
            active_out_root = active_out_root_raw
    rows: list[dict[str, Any]] = []
    for session in collect_resume_sessions():
        out_root = Path(str(session.get("out_root") or "")).expanduser().resolve()
        manifest_path = Path(str(session.get("manifest_snapshot") or "")).expanduser().resolve()
        run_count = max(1, int(session.get("runs") or 1))
        manifest_rows = parse_manifest_rows(manifest_path)
        if not manifest_rows:
            continue

        latest_meta_path = out_root / RUN_META_DIR_NAME / "latest.json"
        runtime_status_path = out_root / RUN_META_DIR_NAME / "runtime_status.json"
        latest_meta = read_json(latest_meta_path, {})
        runtime_status = read_json(runtime_status_path, {})
        planned_total_hint = max(0, int(session.get("planned_total") or 0))
        if isinstance(latest_meta, dict):
            planned_total_hint = max(planned_total_hint, int(latest_meta.get("planned_total_runs") or 0))
        runtime_state = ""
        runtime_start_ts = 0.0
        runtime_updated_ts = 0.0
        runtime_total_hint = 0
        runtime_completed_hint = 0
        if isinstance(runtime_status, dict):
            runtime_state = str(runtime_status.get("status") or "").strip().lower()
            try:
                runtime_start_ts = float(runtime_status.get("start_time") or 0.0)
            except (TypeError, ValueError):
                runtime_start_ts = 0.0
            try:
                runtime_updated_ts = float(runtime_status.get("updated_ts") or 0.0)
            except (TypeError, ValueError):
                runtime_updated_ts = 0.0
            try:
                runtime_total_hint = max(0, int(runtime_status.get("total_runs") or 0))
            except (TypeError, ValueError):
                runtime_total_hint = 0
            try:
                runtime_completed_hint = max(0, int(runtime_status.get("completed_runs") or 0))
            except (TypeError, ValueError):
                runtime_completed_hint = 0
            if runtime_total_hint > 0:
                planned_total_hint = max(planned_total_hint, runtime_total_hint)

        expected_total = 0
        completed_total = 0
        pending_queue_rows: list[dict[str, Any]] = []
        last_update_ts = max(
            float(session.get("created_ts") or 0.0),
            safe_mtime(manifest_path),
            safe_mtime(latest_meta_path),
            safe_mtime(runtime_status_path),
            runtime_updated_ts,
        )

        manifest_jobs: dict[tuple[str, str, int], dict[str, Any]] = {}
        for mrow in manifest_rows:
            pdb_id = str(mrow.get("pdb_id") or "").strip()
            chain = str(mrow.get("chain") or "").strip()
            ligand = str(mrow.get("ligand") or "").strip()
            lig_spec = str(mrow.get("lig_spec") or "").strip()
            pdb_file = str(mrow.get("pdb_file") or "").strip()
            grid_pad = str(mrow.get("grid_pad") or "").strip()
            grid_file = str(mrow.get("grid_file") or "").strip()
            forced_run = str(mrow.get("force_run_id") or "").strip()
            row_cfg = normalize_docking_config(
                mrow.get("docking_config") or STATE.get("docking_config") or {}
            )

            run_ids: list[int] = []
            if forced_run:
                try:
                    forced_num = int(forced_run)
                except (TypeError, ValueError):
                    forced_num = 0
                if forced_num > 0:
                    run_ids = [forced_num]
            if not run_ids:
                run_ids = list(range(1, run_count + 1))

            ligand_folder = normalize_ligand_folder_name(ligand, lig_spec)
            ligand_name = ligand if Path(ligand).suffix else (f"{ligand}.sdf" if ligand else "")

            for rid in run_ids:
                key = run_job_key(pdb_id, ligand_folder, rid)
                if key in manifest_jobs:
                    continue
                manifest_jobs[key] = {
                    "pdb_id": pdb_id,
                    "chain": chain,
                    "ligand_name": ligand_name,
                    "ligand_resname": ligand_name,
                    "lig_spec": lig_spec,
                    "pdb_file": pdb_file,
                    "grid_pad": grid_pad,
                    "grid_file": grid_file,
                    "padding": grid_pad,
                    "run_count": 1,
                    "force_run_id": rid,
                    "docking_config": row_cfg,
                }

        existing_runs = scan_existing_runs(out_root)
        expected_keys = set(manifest_jobs.keys()) | set(existing_runs.keys())
        expected_total = len(expected_keys)
        if planned_total_hint > 0:
            expected_total = max(expected_total, planned_total_hint)
        if runtime_total_hint > 0:
            expected_total = max(expected_total, runtime_total_hint)

        for run_data in existing_runs.values():
            run_ts = float(run_data.get("last_update_ts") or 0.0)
            if run_ts > last_update_ts:
                last_update_ts = run_ts
            if run_data.get("has_results"):
                completed_total += 1
        if runtime_completed_hint > 0:
            completed_total = max(completed_total, runtime_completed_hint)

        untracked_pending = 0
        for key, job in manifest_jobs.items():
            existing = existing_runs.get(key)
            if existing and existing.get("has_results"):
                continue
            resumable = bool(job["pdb_id"] and job["chain"] and (job["ligand_name"] or job["lig_spec"]))
            reason = "" if resumable else "Missing required manifest fields (pdb/chain/ligand)."
            pending_queue_rows.append(
                {
                    **job,
                    "resumable": resumable,
                    "resume_reason": reason,
                }
            )

        for key, run_data in existing_runs.items():
            if key in manifest_jobs:
                continue
            if run_data.get("has_results"):
                continue
            untracked_pending += 1

        pending_total = max(0, expected_total - completed_total)
        if pending_total <= 0:
            continue

        resumable_count = sum(1 for item in pending_queue_rows if item.get("resumable"))
        resumable = resumable_count > 0
        resume_reason = "" if resumable else "No resumable items found under this dock root."
        if untracked_pending:
            extra_reason = (
                f"{untracked_pending} pending run folder(s) were found without matching manifest rows."
            )
            if resume_reason:
                resume_reason = f"{resume_reason} {extra_reason}"
            else:
                resume_reason = extra_reason
        row_out_root = str(out_root)
        runtime_recent = runtime_updated_ts > 0 and (now_ts - runtime_updated_ts) <= 900
        runtime_running = (
            runtime_state in {"running", "stopping"}
            and (runtime_recent or active_out_root == row_out_root)
        )
        is_running = (
            active_status in {"running", "stopping"}
            and bool(active_out_root)
            and active_out_root == row_out_root
        ) or runtime_running
        if is_running and runtime_start_ts > 0:
            elapsed_seconds = int(max(0, now_ts - runtime_start_ts))
        else:
            elapsed_seconds = int(max(0, now_ts - last_update_ts)) if last_update_ts > 0 else 0
        last_update = (
            datetime.fromtimestamp(last_update_ts).strftime("%Y-%m-%d %H:%M:%S")
            if last_update_ts > 0
            else ""
        )

        row_data: dict[str, Any] = {
            "id": str(session.get("id") or ""),
            "dock_root": str(session.get("dock_root") or out_root.name),
            "out_root": row_out_root,
            "expected_runs_total": expected_total,
            "completed_runs_total": completed_total,
            "pending_run_count": pending_total,
            "incomplete_jobs": pending_total,
            "resumable_jobs": resumable_count,
            "untracked_pending_count": untracked_pending,
            "last_update": last_update,
            "elapsed_seconds": elapsed_seconds,
            "resumable": resumable,
            "resume_reason": resume_reason,
            "resume_out_root": str(out_root),
            "is_running": is_running,
            "last_update_ts": last_update_ts,
        }
        if include_jobs:
            row_data["pending_queue_rows"] = pending_queue_rows
        rows.append(row_data)

    collapsed: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(
            row.get("resume_out_root")
            or row.get("out_root")
            or row.get("dock_root")
            or row.get("id")
            or ""
        )
        if not key:
            key = str(row.get("id") or "")
        prev = collapsed.get(key)
        if prev is None:
            collapsed[key] = row
            continue
        cur_expected = int(row.get("expected_runs_total") or 0)
        prev_expected = int(prev.get("expected_runs_total") or 0)
        cur_completed = int(row.get("completed_runs_total") or 0)
        prev_completed = int(prev.get("completed_runs_total") or 0)
        cur_ts = float(row.get("last_update_ts") or 0.0)
        prev_ts = float(prev.get("last_update_ts") or 0.0)
        if (cur_expected, cur_completed, cur_ts) > (prev_expected, prev_completed, prev_ts):
            collapsed[key] = row

    rows = list(collapsed.values())
    rows.sort(
        key=lambda row: (float(row.get("last_update_ts") or 0.0), str(row.get("id") or "")),
        reverse=True,
    )
    for row in rows:
        row.pop("last_update_ts", None)
        if not include_jobs:
            row.pop("pending_queue_rows", None)
    return rows[:normalized_limit]
