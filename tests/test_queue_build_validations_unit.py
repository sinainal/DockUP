from __future__ import annotations

import copy
import shutil
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from docking_app.config import DOCK_DIR, LIGAND_DIR, RECEPTOR_DIR
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


def _first_ligand_name() -> str:
    ligands = sorted(path.name for path in LIGAND_DIR.glob("*.sdf") if path.is_file())
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
