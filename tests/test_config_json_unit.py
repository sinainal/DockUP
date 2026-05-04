from __future__ import annotations

import copy
import io
import json

import pandas as pd
from fastapi.testclient import TestClient

from docking_app.app import create_app
from docking_app.state import STATE


def test_config_save_can_return_compact_json() -> None:
    client = TestClient(create_app())
    payload = {
        "format": "json",
        "mode": "Docking",
        "run_count": 3,
        "padding": 1.5,
        "out_root_path": "data/dock",
        "out_root_name": "agent_batch",
        "docking_config": {"docking_engine": "vina_gpu_21", "vina_exhaustiveness": 12},
        "selection_map": {
            "1abc": {
                "chain": "A",
                "ligand_resname": "aspirin.sdf",
                "ligand_resnames": ["aspirin.sdf"],
                "flex_residues": ["A:114"],
            }
        },
        "grid_data": {"1abc": {"cx": 1, "cy": 2, "cz": 3, "sx": 20, "sy": 21, "sz": 22}},
    }

    response = client.post("/api/config/save", json=payload)
    data = response.json()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"] == "attachment; filename=docking_config.json"
    assert data == {
        "schema": "dockup.config.v1",
        "mode": "Docking",
        "run_count": 3,
        "padding": 1.5,
        "out_root_path": "data/dock",
        "out_root_name": "agent_batch",
        "docking_config": {
            "docking_engine": "vina_gpu_21",
            "docking_mode": "standard",
            "ligand_binding_mode": "single",
            "pdb2pqr_ph": 7.4,
            "pdb2pqr_ff": "AMBER",
            "pdb2pqr_ffout": "AMBER",
            "pdb2pqr_nodebump": True,
            "pdb2pqr_keep_chain": True,
            "mkrec_allow_bad_res": True,
            "mkrec_default_altloc": "A",
            "vina_exhaustiveness": 12,
            "vina_num_modes": None,
            "vina_energy_range": None,
            "vina_cpu": None,
            "vina_seed": None,
        },
        "selection_map": {
            "1ABC": {
                "chain": "A",
                "ligand_resname": "aspirin.sdf",
                "ligand_resnames": ["aspirin.sdf"],
                "flex_residues": [{"chain": "A", "resno": "114", "resname": ""}],
            }
        },
        "grid_data": {"1ABC": {"cx": 1.0, "cy": 2.0, "cz": 3.0, "sx": 20.0, "sy": 21.0, "sz": 22.0}},
    }


def test_config_load_accepts_json_and_updates_state() -> None:
    snapshot = copy.deepcopy(STATE)
    try:
        STATE["receptor_meta"] = [{"pdb_id": "1ABC", "pdb_file": ""}]
        STATE["selection_map"] = {"1ABC": {"chain": "all", "ligand_resname": "", "ligand_resnames": [], "flex_residues": []}}
        client = TestClient(create_app())
        config = {
            "schema": "dockup.config.v1",
            "mode": "Docking",
            "run_count": 4,
            "padding": 2,
            "out_root_path": "data/dock",
            "out_root_name": "json_loaded",
            "docking_config": {"docking_engine": "vina", "vina_exhaustiveness": 9},
            "selection_map": {"1ABC": {"chain": "B", "ligand": "dopamine.sdf"}},
            "grid_data": {"1ABC": {"cx": 4, "cy": 5, "cz": 6, "sx": 18, "sy": 19, "sz": 20}},
        }

        response = client.post(
            "/api/config/load",
            files={"file": ("config.json", json.dumps(config).encode("utf-8"), "application/json")},
        )
        data = response.json()

        assert response.status_code == 200
        assert data["ok"] is True
        assert data["schema"] == "dockup.config.v1"
        assert data["run_count"] == 4
        assert data["padding"] == 2.0
        assert data["out_root_name"] == "json_loaded"
        assert data["selection_map"]["1ABC"]["chain"] == "B"
        assert data["selection_map"]["1ABC"]["ligand_resname"] == "dopamine.sdf"
        assert data["grid_data"]["1ABC"]["cx"] == 4.0
        assert STATE["agent_grid_data"]["1ABC"]["cx"] == 4.0
        assert data["docking_config"]["vina_exhaustiveness"] == 9
    finally:
        STATE.clear()
        STATE.update(snapshot)


def test_config_load_still_accepts_xlsx() -> None:
    snapshot = copy.deepcopy(STATE)
    try:
        STATE["receptor_meta"] = [{"pdb_id": "1ABC", "pdb_file": ""}]
        STATE["selection_map"] = {"1ABC": {"chain": "all", "ligand_resname": "", "ligand_resnames": [], "flex_residues": []}}
        client = TestClient(create_app())
        frame = pd.DataFrame(
            [
                {
                    "type": "Docking",
                    "pdb_id": "1ABC",
                    "chain": "A",
                    "ligand": "ligand.sdf",
                    "ligands": "ligand.sdf",
                    "grid_center_x": 1.0,
                    "grid_center_y": 2.0,
                    "grid_center_z": 3.0,
                    "grid_size_x": 20.0,
                    "grid_size_y": 21.0,
                    "grid_size_z": 22.0,
                    "run_count": 2,
                    "padding": 1.0,
                    "vina_exhaustiveness": 8,
                }
            ]
        )
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name="Configuration", index=False)
        buffer.seek(0)

        response = client.post(
            "/api/config/load",
            files={
                "file": (
                    "config.xlsx",
                    buffer.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        data = response.json()

        assert response.status_code == 200
        assert data["ok"] is True
        assert data["selection_map"]["1ABC"]["ligand_resname"] == "ligand.sdf"
        assert data["grid_data"]["1ABC"]["sx"] == 20.0
        assert STATE["agent_grid_data"]["1ABC"]["sx"] == 20.0
        assert data["run_count"] == 2
    finally:
        STATE.clear()
        STATE.update(snapshot)


def test_config_save_uses_persisted_grid_data_when_payload_omits_grid_data() -> None:
    snapshot = copy.deepcopy(STATE)
    try:
        STATE["mode"] = "Docking"
        STATE["runs"] = 1
        STATE["grid_pad"] = 0.0
        STATE["out_root_path"] = "data/dock"
        STATE["out_root_name"] = ""
        STATE["docking_config"] = {}
        STATE["selection_map"] = {
            "1ABC": {"chain": "A", "ligand_resname": "ligand.sdf", "ligand_resnames": ["ligand.sdf"], "flex_residues": []}
        }
        STATE["agent_grid_data"] = {"1ABC": {"cx": 1, "cy": 2, "cz": 3, "sx": 20, "sy": 21, "sz": 22}}
        client = TestClient(create_app())

        response = client.post("/api/config/save", json={"format": "json"})
        data = response.json()

        assert response.status_code == 200
        assert data["grid_data"]["1ABC"] == {"cx": 1.0, "cy": 2.0, "cz": 3.0, "sx": 20.0, "sy": 21.0, "sz": 22.0}
    finally:
        STATE.clear()
        STATE.update(snapshot)
