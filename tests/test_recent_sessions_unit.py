from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from docking_app.sessions import scan_recent_incomplete_rows


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
