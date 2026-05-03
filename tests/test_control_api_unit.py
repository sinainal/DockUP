from __future__ import annotations

import copy

from fastapi.testclient import TestClient

from docking_app.app import create_app
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
