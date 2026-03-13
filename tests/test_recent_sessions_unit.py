from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import docking_app.sessions as sessions
from docking_app.sessions import collect_resume_sessions, scan_recent_incomplete_rows


def test_recent_scan_skips_successful_runtime_even_without_results(monkeypatch, tmp_path):
    out_root = tmp_path / "dock_root"
    meta_dir = out_root / ".docking_meta"
    meta_dir.mkdir(parents=True)
    manifest_path = tmp_path / "manifest.tsv"
    manifest_path.write_text(
        "6CM4\tall\tASPIRIN_monomer.sdf\t/tmp/ASPIRIN_monomer.sdf\t/tmp/6CM4.pdb\t0\t/tmp/grid.txt\t1\t7.4\tAMBER\tAMBER\t1\t1\t1\tA\t32\t__EMPTY__\t__EMPTY__\t__EMPTY__\t__EMPTY__\n",
        encoding="utf-8",
    )
    runtime_status_path = meta_dir / "runtime_status.json"
    runtime_status_path.write_text(
        (
            "{\n"
            '  "status": "done",\n'
            '  "returncode": 0,\n'
            '  "start_time": %s,\n'
            '  "total_runs": 1,\n'
            '  "completed_runs": 1,\n'
            '  "updated_ts": %s\n'
            "}\n"
        )
        % (time.time() - 5, time.time()),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "docking_app.sessions.collect_resume_sessions",
        lambda: [
            {
                "id": "sess_test",
                "created_ts": time.time() - 10,
                "dock_root": out_root.name,
                "out_root": str(out_root),
                "manifest_snapshot": str(manifest_path),
                "runs": 1,
                "planned_total": 1,
            }
        ],
    )
    monkeypatch.setattr("docking_app.sessions.scan_existing_runs", lambda _out_root: {})

    rows = scan_recent_incomplete_rows(limit=10, include_jobs=False)

    assert rows == []


def test_collect_resume_sessions_dedupes_legacy_entry_with_unquoted_batch_paths(monkeypatch, tmp_path):
    workspace_dir = tmp_path / "workspace"
    dock_dir = workspace_dir / "data" / "dock"
    dock_dir.mkdir(parents=True)
    out_root = dock_dir / "single_run"
    manifest_path = dock_dir / "manifest.tsv"
    manifest_path.write_text(
        "6CM4\tA\tligand.sdf\t/tmp/ligand.sdf\t/tmp/6CM4.pdb\t0\t/tmp/grid.txt\t__EMPTY__\t7.4\tAMBER\tAMBER\t1\t1\t1\tA\t16\t__EMPTY__\t__EMPTY__\t__EMPTY__\t__EMPTY__\n",
        encoding="utf-8",
    )
    (dock_dir / "run_batch.sh").write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"MANIFEST={manifest_path}",
                'RUNS="1"',
                'TOTAL_RUNS="1"',
                f"OUT_ROOT={out_root}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    snapshot_path = dock_dir / ".sessions" / "sess_existing" / "manifest.tsv"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(manifest_path.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(sessions, "BASE", tmp_path)
    monkeypatch.setattr(sessions, "WORKSPACE_DIR", workspace_dir)
    monkeypatch.setattr(sessions, "DOCK_DIR", dock_dir)
    monkeypatch.setattr(
        sessions,
        "load_run_sessions",
        lambda: [
            {
                "id": "sess_existing",
                "created_ts": time.time(),
                "dock_root": out_root.name,
                "out_root": str(out_root),
                "manifest_snapshot": str(snapshot_path),
                "runs": 1,
                "planned_total": 1,
            }
        ],
    )

    rows = collect_resume_sessions()

    assert len(rows) == 1
    assert rows[0]["id"] == "sess_existing"
    assert rows[0]["out_root"] == str(out_root.resolve())
