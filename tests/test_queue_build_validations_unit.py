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
def ensure_seed_ligand() -> None:
    created_path: Path | None = None
    existing = sorted(path for path in LIGAND_DIR.glob("*.sdf") if path.is_file())
    if not existing:
        created_path = LIGAND_DIR / "seed_fixture.sdf"
        created_path.parent.mkdir(parents=True, exist_ok=True)
        created_path.write_text(
            "\n  Ketcher\n\n  0  0  0     0  0            999 V2000\nM  END\n$$$$\n",
            encoding="utf-8",
        )
    try:
        yield
    finally:
        if created_path is not None:
            created_path.unlink(missing_ok=True)


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
