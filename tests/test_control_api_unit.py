from __future__ import annotations

import copy
from pathlib import Path

from fastapi.testclient import TestClient

from docking_app.app import create_app
from docking_app.config import LIGAND_DIR
from docking_app.state import STATE


def test_control_state_returns_standard_envelope() -> None:
    response = TestClient(create_app()).get("/api/control/state")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["action"] == "state.get"
    assert "trace_id" in payload
    assert isinstance(payload["data"], dict)
    assert isinstance(payload["before"], dict)
    assert isinstance(payload["after"], dict)
    assert payload["ui_hints"]["refresh"] == ["state"]


def test_control_viewer_show_selects_and_verifies_receptor_payload() -> None:
    previous = copy.deepcopy(STATE)
    STATE.clear()
    STATE.update(
        {
            "mode": "Docking",
            "receptor_meta": [
                {
                    "pdb_id": "TST1",
                    "pdb_text": "ATOM      1  N   GLY A   1       1.000   1.000   1.000\nEND\n",
                    "chains": ["all", "A"],
                    "ligands_by_chain": {"A": ["LIG 101"], "all": ["LIG 101"]},
                    "pdb_file": "",
                }
            ],
            "selection_map": {"TST1": {"chain": "A", "ligand_resname": "", "ligand_resnames": [], "flex_residues": []}},
            "selected_receptor": "",
            "selected_ids": [],
            "selected_ligand": "",
            "selected_chain": "all",
            "active_ligands": [],
            "grid_file_path": "",
            "agent_grid_data": {},
            "queue": [],
            "runs": 1,
            "grid_pad": 0.0,
            "docking_config": {},
            "out_root": "",
            "out_root_path": "",
            "out_root_name": "",
            "results_root_path": "",
        }
    )
    try:
        response = TestClient(create_app()).post("/api/control/viewer/show", json={"pdb_id": "TST1", "chain": "A"})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True
        assert payload["action"] == "viewer.show"
        assert payload["data"]["pdb_id"] == "TST1"
        assert payload["data"]["pdb_text_length"] > 0
        assert payload["after"]["selected_receptor"] == "TST1"
        assert payload["ui_hints"]["refresh"] == ["state", "viewer"]
    finally:
        STATE.clear()
        STATE.update(previous)


def test_control_event_bridge_publishes_ui_hint_events() -> None:
    from docking_app.control.events import clear_events

    previous = copy.deepcopy(STATE)
    clear_events()
    STATE.clear()
    STATE.update(
        {
            "mode": "Docking",
            "receptor_meta": [
                {
                    "pdb_id": "EVT1",
                    "pdb_text": "ATOM      1  N   TRP A  90       1.000   1.000   1.000\nEND\n",
                    "chains": ["all", "A"],
                    "ligands_by_chain": {"all": []},
                    "pdb_file": "",
                }
            ],
            "selection_map": {"EVT1": {"chain": "all", "ligand_resname": "", "ligand_resnames": [], "flex_residues": []}},
            "selected_receptor": "EVT1",
            "selected_ids": ["EVT1"],
            "selected_ligand": "",
            "selected_chain": "all",
            "active_ligands": [],
            "grid_file_path": "",
            "agent_grid_data": {},
            "queue": [],
            "runs": 1,
            "grid_pad": 0.0,
            "docking_config": {},
            "out_root": "",
            "out_root_path": "",
            "out_root_name": "",
            "results_root_path": "",
        }
    )
    client = TestClient(create_app())
    try:
        response = client.post("/api/control/viewer/residues", json={"pdb_id": "EVT1", "residue": "TRP", "chain": "all"})
        assert response.status_code == 200, response.text

        event_response = client.get("/api/control/events/latest")
        assert event_response.status_code == 200, event_response.text
        event_payload = event_response.json()
        assert event_payload["ok"] is True
        assert event_payload["event"]["action"] == "viewer.residues"
        assert event_payload["event"]["ui_hints"]["viewer_selection"]["label"] == "EVT1 TRP (1)"

        after_response = client.get(f"/api/control/events/latest?after_id={event_payload['latest_id']}")
        assert after_response.status_code == 200, after_response.text
        assert after_response.json()["event"] is None
    finally:
        clear_events()
        STATE.clear()
        STATE.update(previous)


def test_control_receptor_select_does_not_mutate_on_missing_receptor() -> None:
    previous = copy.deepcopy(STATE)
    STATE.clear()
    STATE.update(
        {
            "mode": "Docking",
            "receptor_meta": [
                {
                    "pdb_id": "KEEP",
                    "pdb_text": "ATOM      1  N   GLY A   1       1.000   1.000   1.000\nEND\n",
                    "chains": ["all", "A"],
                    "ligands_by_chain": {"all": []},
                    "pdb_file": "",
                }
            ],
            "selection_map": {"KEEP": {"chain": "all", "ligand_resname": "", "ligand_resnames": [], "flex_residues": []}},
            "selected_receptor": "KEEP",
            "selected_ids": ["KEEP"],
            "selected_ligand": "",
            "selected_chain": "all",
            "active_ligands": [],
            "grid_file_path": "",
            "agent_grid_data": {},
            "queue": [],
            "runs": 1,
            "grid_pad": 0.0,
            "docking_config": {},
            "out_root": "",
            "out_root_path": "",
            "out_root_name": "",
            "results_root_path": "",
        }
    )
    try:
        response = TestClient(create_app()).post("/api/control/receptors/select", json={"pdb_id": "MISS"})
        assert response.status_code == 400, response.text
        payload = response.json()
        assert payload["ok"] is False
        assert payload["action"] == "receptor.select"
        assert payload["message"] == "receptor not available: MISS"
        assert "selected_receptor" not in payload["ui_hints"]
        assert STATE["selected_receptor"] == "KEEP"
    finally:
        STATE.clear()
        STATE.update(previous)


def test_control_ligand_fetch_reports_recoverable_failure(monkeypatch) -> None:
    from docking_app.routes import core

    def fake_fetch(_payload):
        from fastapi.responses import JSONResponse

        return JSONResponse({"saved": [], "failed": ["not-a-ligand"], "ligands": []})

    monkeypatch.setattr(core, "fetch_ligands", fake_fetch)

    response = TestClient(create_app()).post("/api/control/ligands/fetch", json={"ligand_ids": "not-a-ligand"})

    assert response.status_code == 400, response.text
    payload = response.json()
    assert payload["ok"] is False
    assert payload["action"] == "ligand.fetch"
    assert payload["error"]["code"] == "ligand_fetch_failed"
    assert "ligand.fetch" in payload["error"]["next_actions"]


def test_control_docking_lifecycle_builds_and_starts_test_queue() -> None:
    previous = copy.deepcopy(STATE)
    ligand_path = LIGAND_DIR / "control_stage3_probe.sdf"
    ligand_path.write_text("control stage3\nDockUP\n\n  0  0  0     0  0            999 V2000\nM  END\n$$$$\n", encoding="utf-8")
    STATE.clear()
    STATE.update(
        {
            "mode": "Docking",
            "receptor_meta": [
                {
                    "pdb_id": "LIV1",
                    "pdb_text": "ATOM      1  N   GLY A   1       1.000   1.000   1.000\nEND\n",
                    "chains": ["all", "A"],
                    "ligands_by_chain": {"all": []},
                    "pdb_file": str(Path("/tmp/LIV1.pdb")),
                }
            ],
            "selection_map": {"LIV1": {"chain": "A", "ligand_resname": "control_stage3_probe.sdf", "ligand_resnames": [], "flex_residues": []}},
            "selected_receptor": "LIV1",
            "selected_ids": ["LIV1"],
            "selected_ligand": "control_stage3_probe.sdf",
            "selected_chain": "A",
            "active_ligands": ["control_stage3_probe.sdf"],
            "grid_file_path": "",
            "agent_grid_data": {"LIV1": {"cx": 1.0, "cy": 2.0, "cz": 3.0, "sx": 20.0, "sy": 20.0, "sz": 20.0}},
            "queue": [],
            "runs": 1,
            "grid_pad": 0.0,
            "docking_config": {},
            "out_root": "",
            "out_root_path": "data/dock",
            "out_root_name": "control_stage3_probe",
            "results_root_path": "",
        }
    )
    client = TestClient(create_app())
    try:
        config_response = client.post("/api/control/config/set", json={"engine": "vina", "mode": "standard", "run_count": 1})
        assert config_response.status_code == 200, config_response.text
        queue_response = client.post("/api/control/queue/build", json={"replace_queue": True})
        assert queue_response.status_code == 200, queue_response.text
        queue_payload = queue_response.json()
        assert queue_payload["ok"] is True
        assert queue_payload["action"] == "queue.build"
        assert queue_payload["data"]["queue_count"] == 1

        run_response = client.post("/api/control/run/start", json={"test_mode": True})
        assert run_response.status_code == 200, run_response.text
        run_payload = run_response.json()
        assert run_payload["ok"] is True
        assert run_payload["action"] == "run.start"
        assert run_payload["data"]["status"] == "running"
    finally:
        client.post("/api/control/run/stop")
        ligand_path.unlink(missing_ok=True)
        STATE.clear()
        STATE.update(previous)


def test_control_workspace_select_activates_explicit_dock_ligand() -> None:
    previous = copy.deepcopy(STATE)
    ligand_path = LIGAND_DIR / "control_workspace_probe.sdf"
    ligand_path.write_text("control workspace\nDockUP\n\n  0  0  0     0  0            999 V2000\nM  END\n$$$$\n", encoding="utf-8")
    STATE.clear()
    STATE.update(
        {
            "mode": "Docking",
            "receptor_meta": [
                {
                    "pdb_id": "WRK1",
                    "pdb_text": "ATOM      1  N   GLY A   1       1.000   1.000   1.000\nEND\n",
                    "chains": ["all", "A"],
                    "ligands_by_chain": {"all": []},
                    "pdb_file": "",
                }
            ],
            "selection_map": {"WRK1": {"chain": "A", "ligand_resname": "", "ligand_resnames": [], "flex_residues": []}},
            "selected_receptor": "WRK1",
            "selected_ids": ["WRK1"],
            "selected_ligand": "",
            "selected_chain": "A",
            "active_ligands": [],
            "grid_file_path": "",
            "agent_grid_data": {},
            "queue": [],
            "runs": 1,
            "grid_pad": 0.0,
            "docking_config": {},
            "out_root": "",
            "out_root_path": "data/dock",
            "out_root_name": "",
            "results_root_path": "",
        }
    )
    try:
        response = TestClient(create_app()).post(
            "/api/control/workspace/select",
            json={"receptor": "WRK1", "chain": "A", "native_ligand": "", "dock_ligands": "control_workspace_probe.sdf"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True
        assert payload["action"] == "workspace.select"
        assert "control_workspace_probe.sdf" in STATE["active_ligands"]
        assert payload["after"]["active_ligand_count"] == 1
    finally:
        ligand_path.unlink(missing_ok=True)
        STATE.clear()
        STATE.update(previous)


def test_control_queue_prepare_preserves_config_and_uses_dock_all() -> None:
    previous = copy.deepcopy(STATE)
    ligand_a = LIGAND_DIR / "control_prepare_a.sdf"
    ligand_b = LIGAND_DIR / "control_prepare_b.sdf"
    ligand_text = "control prepare\nDockUP\n\n  0  0  0     0  0            999 V2000\nM  END\n$$$$\n"
    ligand_a.write_text(ligand_text, encoding="utf-8")
    ligand_b.write_text(ligand_text, encoding="utf-8")
    STATE.clear()
    STATE.update(
        {
            "mode": "Docking",
            "receptor_meta": [
                {
                    "pdb_id": "PRP1",
                    "pdb_text": "ATOM      1  N   GLY A   1       1.000   1.000   1.000\nEND\n",
                    "chains": ["all", "A"],
                    "ligands_by_chain": {"A": ["NAT 101"], "all": ["NAT 101"]},
                    "pdb_file": str(Path("/tmp/PRP1.pdb")),
                }
            ],
            "selection_map": {"PRP1": {"chain": "A", "ligand_resname": "", "ligand_resnames": [], "flex_residues": []}},
            "selected_receptor": "PRP1",
            "selected_ids": ["PRP1"],
            "selected_ligand": "",
            "selected_chain": "A",
            "active_ligands": [],
            "grid_file_path": "",
            "agent_grid_data": {},
            "queue": [{"batch_id": "old"}],
            "runs": 9,
            "grid_pad": 4.0,
            "docking_config": {"docking_engine": "vina_gpu_21", "docking_mode": "standard", "vina_exhaustiveness": 32},
            "out_root": "",
            "out_root_path": "data/dock",
            "out_root_name": "",
            "results_root_path": "",
        }
    )
    try:
        response = TestClient(create_app()).post(
            "/api/control/queue/prepare",
            json={
                "mode": "Docking",
                "chains": {"PRP1": "A"},
                "ligands": ["control_prepare_a.sdf", "control_prepare_b.sdf"],
                "grid_data": {"PRP1": {"cx": 1, "cy": 2, "cz": 3, "sx": 15, "sy": 15, "sz": 15}},
                "run_count": 5,
                "padding": 10,
                "out_root_name": "control_prepare",
                "replace_queue": True,
                "reset_queue": True,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True
        assert payload["data"]["queue_count"] == 2
        assert payload["data"]["total_runs"] == 10
        assert payload["data"]["selection_map"]["PRP1"]["ligand_resname"] == "all_set"
        assert payload["data"]["active_ligands"] == ["control_prepare_a.sdf", "control_prepare_b.sdf"]
        assert payload["data"]["queue"][0]["grid_params"]["sx"] == 25.0
        assert STATE["selected_ligand"] == "all_set"
        assert STATE["docking_config"]["docking_engine"] == "vina_gpu_21"
    finally:
        ligand_a.unlink(missing_ok=True)
        ligand_b.unlink(missing_ok=True)
        STATE.clear()
        STATE.update(previous)
