from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from docking_app.config import DOCK_DIR, LIGAND_DIR, RECEPTOR_DIR
from docking_app.routes import core
from docking_app.services import _build_queue
from docking_app.state import STATE


@pytest.fixture(autouse=True)
def restore_state():
    snapshot = copy.deepcopy(STATE)
    cleanup_dir = (DOCK_DIR / "pytest_queue_validation").resolve()
    try:
        yield
    finally:
        shutil.rmtree(cleanup_dir, ignore_errors=True)
        STATE.clear()
        STATE.update(snapshot)


@pytest.fixture(autouse=True)
def ensure_seed_inputs() -> None:
    created_paths: list[Path] = []
    existing = sorted(path for path in LIGAND_DIR.glob("*.sdf") if path.is_file())
    if not existing:
        ligand_path = LIGAND_DIR / "seed_fixture.sdf"
        ligand_path.parent.mkdir(parents=True, exist_ok=True)
        ligand_path.write_text(
            "\n  Ketcher\n\n  0  0  0     0  0            999 V2000\nM  END\n$$$$\n",
            encoding="utf-8",
        )
        created_paths.append(ligand_path)
    receptor_path = RECEPTOR_DIR / "6CM4.pdb"
    if not receptor_path.exists():
        receptor_path.parent.mkdir(parents=True, exist_ok=True)
        receptor_path.write_text(
            "HEADER    TEST RECEPTOR\nATOM      1  N   GLY A   1      11.104  13.207   9.947  1.00 20.00           N\nEND\n",
            encoding="utf-8",
        )
        created_paths.append(receptor_path)
    try:
        yield
    finally:
        for created_path in reversed(created_paths):
            created_path.unlink(missing_ok=True)


def _first_ligand_name() -> str:
    ligands = sorted(path.name for path in LIGAND_DIR.glob("*.sdf") if path.is_file())
    if not ligands:
        ligand_path = LIGAND_DIR / "seed_fixture.sdf"
        ligand_path.parent.mkdir(parents=True, exist_ok=True)
        ligand_path.write_text(
            "\n  Ketcher\n\n  0  0  0     0  0            999 V2000\nM  END\n$$$$\n",
            encoding="utf-8",
        )
        ligands = [ligand_path.name]
    assert ligands, "Expected at least one ligand fixture in workspace/data/ligand."
    return ligands[0]


def _configure_minimal_state(active_ligands: list[str] | None = None) -> None:
    receptor_path = RECEPTOR_DIR / "6CM4.pdb"
    assert receptor_path.exists(), f"Missing receptor fixture: {receptor_path}"
    STATE["receptor_meta"] = [{"pdb_id": "6CM4", "pdb_file": str(receptor_path)}]
    STATE["active_ligands"] = list(active_ligands or [])
    STATE["out_root"] = str((DOCK_DIR / "pytest_queue_validation").resolve())


@pytest.mark.parametrize(
    ("mode", "detail_substring"),
    [
        ("Docking", "dock-ready ligand"),
        ("Redocking", "native ligand"),
    ],
)
def test_build_queue_requires_selected_ligand(mode: str, detail_substring: str):
    ligand_name = _first_ligand_name()
    _configure_minimal_state(active_ligands=[ligand_name])

    payload = {
        "selection_map": {"6CM4": {"chain": "all", "ligand_resname": ""}},
        "grid_data": {"6CM4": {"cx": 1.0, "cy": 2.0, "cz": 3.0, "sx": 20.0, "sy": 20.0, "sz": 20.0}},
        "run_count": 1,
        "padding": 0.0,
        "mode": mode,
        "docking_config": {},
    }

    with pytest.raises(HTTPException) as exc_info:
        _build_queue(payload)

    assert exc_info.value.status_code == 400
    assert "No ligand selected for 6CM4" in str(exc_info.value.detail)
    assert detail_substring in str(exc_info.value.detail)


def test_build_queue_keeps_valid_docking_ligand():
    ligand_name = _first_ligand_name()
    ligand_path = LIGAND_DIR / ligand_name
    assert ligand_path.exists(), f"Missing ligand fixture: {ligand_path}"
    _configure_minimal_state(active_ligands=[ligand_name])

    payload = {
        "selection_map": {"6CM4": {"chain": "all", "ligand_resname": ligand_name}},
        "grid_data": {"6CM4": {"cx": 0.0, "cy": 0.0, "cz": 0.0, "sx": 20.0, "sy": 20.0, "sz": 20.0}},
        "run_count": 1,
        "padding": 0.0,
        "mode": "Docking",
        "docking_config": {},
    }

    entries = _build_queue(payload)

    assert len(entries) == 1
    assert entries[0]["pdb_id"] == "6CM4"
    assert entries[0]["ligand_resname"] == ligand_name
    assert entries[0]["lig_spec"] == str(ligand_path)


def test_build_or_run_queue_syncs_batch_config_from_state(monkeypatch):
    from docking_app.agent import autonomous_docking

    ligand_name = _first_ligand_name()
    _configure_minimal_state(active_ligands=[ligand_name])
    STATE["selection_map"] = {
        "6CM4": {
            "chain": "all",
            "ligand_resname": ligand_name,
            "ligand_resnames": [ligand_name],
            "flex_residues": [],
        }
    }
    STATE["agent_grid_data"] = {
        "6CM4": {"cx": 1.0, "cy": 2.0, "cz": 3.0, "sx": 20.0, "sy": 20.0, "sz": 20.0}
    }
    STATE["docking_config"] = {
        "docking_engine": "vina",
        "docking_mode": "standard",
        "ligand_binding_mode": "single",
    }
    STATE["runs"] = 1
    STATE["grid_pad"] = 0.0
    STATE["out_root_path"] = str((DOCK_DIR / "pytest_queue_validation").resolve())
    STATE["out_root_name"] = "sync_test"
    STATE["queue"] = []
    previous_agent_state = {key: autonomous_docking.AGENT_STATE.get(key) for key in ("setup_rows", "grid_data", "batch_config", "batch_id")}

    calls: list[tuple[str, object]] = []
    progress_events: list[dict[str, object]] = []

    def fake_build_queue(replace_queue: bool = False):
        calls.append(("build_queue", replace_queue))
        assert autonomous_docking.AGENT_STATE.get("batch_config"), "batch_config should be restored from STATE"
        return {"ok": True, "batch_id": "batch-sync", "queue_count": 1, "new_jobs": 1, "job_count": 1, "total_runs": 1}

    def fake_run_queue(test_mode: bool = True, progress_callback=None):
        calls.append(("run_queue", test_mode))
        if progress_callback is not None:
            progress_callback({"type": "status", "stage": "run_queue", "delta": f"fake run_queue test_mode={test_mode}"})
            progress_events.append({"type": "status", "stage": "run_queue", "delta": f"fake run_queue test_mode={test_mode}"})
        return {"ok": True, "started": True, "test_mode": test_mode, "planned_total_runs": 1, "out_root": "sync-root"}

    monkeypatch.setattr(autonomous_docking, "build_queue", fake_build_queue)
    monkeypatch.setattr(autonomous_docking, "run_queue", fake_run_queue)

    try:
        autonomous_docking.AGENT_STATE["setup_rows"] = []
        autonomous_docking.AGENT_STATE["grid_data"] = {}
        autonomous_docking.AGENT_STATE["batch_config"] = {}
        autonomous_docking.AGENT_STATE["batch_id"] = ""

        result = autonomous_docking.build_or_run_queue(action="run_full", progress_callback=lambda row: progress_events.append(dict(row)))

        assert result["ok"] is True
        assert calls == [("build_queue", True), ("run_queue", False)]
        assert any(row.get("stage") == "build_or_run_queue" for row in progress_events)
        assert any(row.get("stage") == "run_queue" for row in progress_events)
    finally:
        for key, value in previous_agent_state.items():
            autonomous_docking.AGENT_STATE[key] = value


def test_build_or_run_queue_can_append_new_config_batch(monkeypatch):
    from docking_app.agent import autonomous_docking

    ligand_name = _first_ligand_name()
    _configure_minimal_state(active_ligands=[ligand_name])
    STATE["selection_map"] = {
        "6CM4": {
            "chain": "all",
            "ligand_resname": ligand_name,
            "ligand_resnames": [ligand_name],
            "flex_residues": [],
        }
    }
    STATE["agent_grid_data"] = {
        "6CM4": {"cx": 1.0, "cy": 2.0, "cz": 3.0, "sx": 20.0, "sy": 20.0, "sz": 20.0}
    }
    STATE["docking_config"] = {
        "docking_engine": "vina",
        "docking_mode": "standard",
        "ligand_binding_mode": "single",
    }
    STATE["runs"] = 1
    STATE["grid_pad"] = 0.0
    STATE["out_root_path"] = str((DOCK_DIR / "pytest_queue_validation").resolve())
    STATE["out_root_name"] = "append_test"
    STATE["queue"] = []

    previous_agent_state = {key: autonomous_docking.AGENT_STATE.get(key) for key in ("setup_rows", "grid_data", "batch_config", "batch_id")}

    calls: list[tuple[str, object]] = []

    def fake_build_queue(replace_queue: bool = False):
        calls.append(("build_queue", replace_queue))
        return {"ok": True, "batch_id": "batch-append", "queue_count": 2, "new_jobs": 1, "job_count": 1, "total_runs": 1}

    monkeypatch.setattr(autonomous_docking, "build_queue", fake_build_queue)

    try:
        autonomous_docking.AGENT_STATE["setup_rows"] = []
        autonomous_docking.AGENT_STATE["grid_data"] = {}
        autonomous_docking.AGENT_STATE["batch_config"] = {}
        autonomous_docking.AGENT_STATE["batch_id"] = ""

        result = autonomous_docking.build_or_run_queue(action="build_only", replace_queue=False)

        assert result["ok"] is True
        assert result["replace_queue"] is False
        assert result["queue"]["replace_queue"] is False
        assert calls == [("build_queue", False)]
    finally:
        for key, value in previous_agent_state.items():
            autonomous_docking.AGENT_STATE[key] = value


def test_build_queue_empty_selection_does_not_create_output_dirs():
    out_root = (DOCK_DIR / "pytest_queue_validation").resolve()
    shutil.rmtree(out_root, ignore_errors=True)
    _configure_minimal_state(active_ligands=[])

    entries = _build_queue(
        {
            "selection_map": {},
            "grid_data": {},
            "run_count": 1,
            "padding": 0.0,
            "mode": "Docking",
            "docking_config": {},
        }
    )

    assert entries == []
    assert not out_root.exists()


def test_queue_build_partial_batch_preserves_other_receptor_selection():
    ligand_name = _first_ligand_name()
    out_root = (DOCK_DIR / "pytest_queue_validation").resolve()
    receptor_path = RECEPTOR_DIR / "6CM4.pdb"
    STATE["receptor_meta"] = [
        {"pdb_id": "6CM4", "pdb_file": str(receptor_path)},
        {"pdb_id": "8IRV", "pdb_file": str(receptor_path)},
    ]
    STATE["selected_receptor"] = "6CM4"
    STATE["selected_chain"] = "A"
    STATE["selected_ligand"] = ligand_name
    STATE["active_ligands"] = [ligand_name]
    STATE["queue"] = []
    STATE["out_root"] = str(out_root)
    STATE["out_root_path"] = "data/dock"
    STATE["out_root_name"] = "pytest_queue_validation"
    STATE["selection_map"] = {
        "6CM4": {"chain": "A", "ligand_resname": ligand_name, "ligand_resnames": [ligand_name], "flex_residues": []},
        "8IRV": {"chain": "all", "ligand_resname": "", "ligand_resnames": [], "flex_residues": []},
    }
    grid = {"cx": 0.0, "cy": 1.0, "cz": 2.0, "sx": 20.0, "sy": 20.0, "sz": 20.0}

    response = core.queue_build(
        {
            "selection_map": {
                "8IRV": {"chain": "all", "ligand_resname": ligand_name, "ligand_resnames": [ligand_name]}
            },
            "grid_data": {"8IRV": grid},
            "run_count": 1,
            "padding": 0.0,
            "mode": "Docking",
            "docking_config": {},
            "replace_queue": False,
        }
    )
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert payload["queue_count"] == 1
    assert STATE["selection_map"]["6CM4"]["ligand_resname"] == ligand_name
    assert STATE["selection_map"]["8IRV"]["ligand_resname"] == ligand_name
    assert STATE["agent_grid_data"]["8IRV"] == grid


def test_build_queue_multi_ligand_requires_exactly_two_ligands():
    ligand_name = _first_ligand_name()
    _configure_minimal_state(active_ligands=[ligand_name])

    payload = {
        "selection_map": {"6CM4": {"chain": "all", "ligand_resname": ligand_name, "ligand_resnames": [ligand_name]}},
        "grid_data": {"6CM4": {"cx": 0.0, "cy": 0.0, "cz": 0.0, "sx": 20.0, "sy": 20.0, "sz": 20.0}},
        "run_count": 1,
        "padding": 0.0,
        "mode": "Multi-Ligand",
        "docking_config": {"docking_mode": "standard"},
    }

    with pytest.raises(HTTPException) as exc_info:
        _build_queue(payload)

    assert exc_info.value.status_code == 400
    assert "Select exactly two ligands" in str(exc_info.value.detail)


def test_build_queue_multi_ligand_writes_ligand_set_manifest(tmp_path: Path):
    ligand_one = _first_ligand_name()
    ligand_two = "second_multi_fixture.sdf"
    ligand_two_path = LIGAND_DIR / ligand_two
    ligand_two_path.write_text(
        "\n  Ketcher\n\n  0  0  0     0  0            999 V2000\nM  END\n$$$$\n",
        encoding="utf-8",
    )
    try:
        _configure_minimal_state(active_ligands=[ligand_one, ligand_two])

        payload = {
            "selection_map": {
                "6CM4": {
                    "chain": "all",
                    "ligand_resname": f"{ligand_one} + {ligand_two}",
                    "ligand_resnames": [ligand_one, ligand_two],
                }
            },
            "grid_data": {"6CM4": {"cx": 0.0, "cy": 0.0, "cz": 0.0, "sx": 20.0, "sy": 20.0, "sz": 20.0}},
            "run_count": 1,
            "padding": 0.0,
            "mode": "Multi-Ligand",
            "docking_config": {"docking_mode": "standard"},
        }

        entries = _build_queue(payload)

        assert len(entries) == 1
        entry = entries[0]
        assert entry["job_type"] == "Multi-Ligand"
        assert entry["ligand_resnames"] == [ligand_one, ligand_two]
        lig_spec = Path(str(entry["lig_spec"]))
        assert lig_spec.exists(), f"Missing multi-ligand manifest: {lig_spec}"
        payload = json.loads(lig_spec.read_text(encoding="utf-8"))
        assert [item["name"] for item in payload["ligands"]] == [ligand_one, ligand_two]
    finally:
        ligand_two_path.unlink(missing_ok=True)
