from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from docking_app.app import app
from docking_app.config import DOCK_DIR
from docking_app.helpers import to_display_path
from docking_app.routes import core
from docking_app.state import STATE


def test_home_page_renders_without_template_type_error() -> None:
    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert "<title>DockUP</title>" in response.text


def test_results_scan_empty_body_defaults_to_dock_root(monkeypatch) -> None:
    client = TestClient(app)
    previous_root = STATE.get("results_root_path")
    STATE["results_root_path"] = str((DOCK_DIR / "nested_previous_root").resolve())

    try:
        response = client.post("/api/results/scan", json={})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["root_path"] == "data/dock"
    finally:
        STATE["results_root_path"] = previous_root


def test_results_scan_absolute_path_returns_runs() -> None:
    client = TestClient(app)
    response = client.post("/api/results/scan", json={"root_path": str(DOCK_DIR.resolve())})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "runs" in payload
    assert isinstance(payload["runs"], list)


def test_prepare_resume_queue_normalizes_out_root_path_to_display_path(monkeypatch) -> None:
    absolute_out_root = (DOCK_DIR / "resume_probe_root").resolve()
    selected = {
        "id": "resume_probe",
        "dock_root": absolute_out_root.name,
        "resume_out_root": str(absolute_out_root),
        "resumable": True,
        "pending_queue_rows": [
            {
                "resumable": True,
                "job_type": "Docking",
                "pdb_id": "6CM4",
                "chain": "all",
                "ligand_name": "ligand.sdf",
                "ligand_resname": "ligand.sdf",
                "lig_spec": "/tmp/ligand.sdf",
                "pdb_file": "/tmp/6CM4.pdb",
                "grid_file": "/tmp/grid.txt",
                "padding": 0,
                "grid_pad": 0,
                "docking_config": {},
            }
        ],
    }

    previous_out_root = STATE.get("out_root")
    previous_out_root_path = STATE.get("out_root_path")
    previous_out_root_name = STATE.get("out_root_name")
    previous_queue = list(STATE.get("queue", []))

    monkeypatch.setattr(core, "scan_recent_incomplete_rows", lambda limit=500, include_jobs=True: [selected])

    try:
        queue_rows, _meta = core._prepare_resume_queue(item_id="resume_probe", replace_queue=True)
        assert queue_rows
        assert STATE["out_root_path"] == to_display_path(absolute_out_root.parent)
        assert STATE["out_root_name"] == absolute_out_root.name
    finally:
        STATE["out_root"] = previous_out_root
        STATE["out_root_path"] = previous_out_root_path
        STATE["out_root_name"] = previous_out_root_name
        STATE["queue"] = previous_queue
