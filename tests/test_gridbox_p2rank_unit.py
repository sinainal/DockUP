from __future__ import annotations

import copy
from pathlib import Path

import pytest

from docking_app.agent import autonomous_docking
from docking_app.state import STATE


@pytest.fixture(autouse=True)
def restore_state():
    state_snapshot = copy.deepcopy(STATE)
    agent_snapshot = copy.deepcopy(autonomous_docking.AGENT_STATE)
    try:
        yield
    finally:
        STATE.clear()
        STATE.update(state_snapshot)
        autonomous_docking.AGENT_STATE.clear()
        autonomous_docking.AGENT_STATE.update(agent_snapshot)


def _write_receptor_fixture(path: Path, *, include_native: bool = True) -> str:
    lines = [
        "ATOM      1  N   GLY B   1      10.000  10.000  10.000  1.00 20.00           N",
        "ATOM      2  CA  GLY B   1      11.000  10.000  10.500  1.00 20.00           C",
        "ATOM      3  C   GLY B   1      11.500  11.000  11.000  1.00 20.00           C",
        "ATOM      4  O   GLY B   1      12.000  11.500  11.500  1.00 20.00           O",
    ]
    if include_native:
        lines.extend(
            [
                "HETATM    5  C1  NXL B 308      20.000  20.000  20.000  1.00 20.00           C",
                "HETATM    6  C2  NXL B 308      21.000  20.500  20.000  1.00 20.00           C",
                "HETATM    7  C3  NXL B 308      21.500  21.000  20.500  1.00 20.00           C",
                "HETATM    8  C4  NXL B 308      22.000  21.500  21.000  1.00 20.00           C",
                "HETATM    9  C5  NXL B 308      22.500  22.000  21.500  1.00 20.00           C",
                "HETATM   10  C6  NXL B 308      23.000  22.500  22.000  1.00 20.00           C",
            ]
        )
    lines.extend(
        [
            "HETATM   11  CL  CL  B 900      14.000  14.000  14.000  1.00 20.00          CL",
            "HETATM   12  CL2 CL  B 900      14.500  14.500  14.500  1.00 20.00          CL",
            "HETATM   13  S   SO4 B 901      15.000  15.000  15.000  1.00 20.00           S",
            "END",
        ]
    )
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    return text


def _install_receptor_meta(pdb_id: str, pdb_file: Path, pdb_text: str, ligands: list[str]) -> None:
    STATE["receptor_meta"] = [
        {
            "pdb_id": pdb_id,
            "pdb_file": str(pdb_file),
            "pdb_text": pdb_text,
            "chains": ["all", "B"],
            "ligands_by_chain": {"B": ligands, "all": ligands},
            "error": "",
        }
    ]
    STATE["selection_map"] = {pdb_id: {"chain": "B", "ligand_resname": "", "ligand_resnames": [], "flex_residues": []}}
    STATE["selected_receptor"] = pdb_id
    STATE["selected_chain"] = "B"
    STATE["selected_ligand"] = ""
    autonomous_docking.AGENT_STATE["inventory"] = {
        "receptors": {
            pdb_id: {
                "chains": ["all", "B"],
                "native_ligands": {"B": ligands, "all": ligands},
            }
        },
        "ligands": [],
    }


def test_resolve_chain_native_prefers_main_ligand_over_helper_ions(tmp_path: Path) -> None:
    pdb_file = tmp_path / "6CM4.pdb"
    pdb_text = _write_receptor_fixture(pdb_file, include_native=True)
    _install_receptor_meta("6CM4", pdb_file, pdb_text, ["CL 900", "NXL 308", "SO4 901"])

    chain, native = autonomous_docking._resolve_chain_native("6CM4", "auto", "auto")

    assert chain == "B"
    assert native.startswith("NXL")


def test_resolve_chain_native_preserves_uppercase_chain_on_no_native_fallback(tmp_path: Path) -> None:
    pdb_file = tmp_path / "7P2R.pdb"
    pdb_text = _write_receptor_fixture(pdb_file, include_native=False)
    _install_receptor_meta("7P2R", pdb_file, pdb_text, [])

    chain, native = autonomous_docking._resolve_chain_native("7P2R", "A", "auto")

    assert chain == "A"
    assert native == "auto"


def test_wait_for_p2rank_gridbox_emits_loading_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdb_file = tmp_path / "7P2R.pdb"
    pdb_text = _write_receptor_fixture(pdb_file, include_native=False)
    _install_receptor_meta("7P2R", pdb_file, pdb_text, [])

    output_dir = tmp_path / "p2rank" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    calls = {"run": 0, "latest": 0, "runtime": 0}
    runtime_states = iter(
        [
            {"status": "running", "message": "Running P2Rank for 7P2R (B)...", "error": ""},
            {"status": "done", "message": "Binding site prediction ready for 7P2R (B).", "error": ""},
        ]
    )
    events: list[dict[str, object]] = []

    monkeypatch.setattr(
        autonomous_docking,
        "run_p2rank_async",
        lambda *args, **kwargs: calls.__setitem__("run", calls["run"] + 1) or {"status": "running"},
    )

    def fake_latest_output_dir(pdb_id: str, chain: str = "all"):
        calls["latest"] += 1
        return None if calls["latest"] == 1 else output_dir

    monkeypatch.setattr(autonomous_docking, "latest_output_dir", fake_latest_output_dir)

    def fake_get_runtime_state():
        calls["runtime"] += 1
        return next(runtime_states, {"status": "done", "message": "Binding site prediction ready for 7P2R (B).", "error": ""})

    monkeypatch.setattr(autonomous_docking, "get_runtime_state", fake_get_runtime_state)
    monkeypatch.setattr(
        autonomous_docking,
        "compute_gridbox_for_pocket",
        lambda *_args, **_kwargs: {"cx": 1.0, "cy": 2.0, "cz": 3.0, "sx": 20.0, "sy": 20.0, "sz": 20.0},
    )

    grid, warnings = autonomous_docking._wait_for_p2rank_gridbox(
        "7P2R",
        "B",
        pocket_rank=1,
        mode="fit",
        fixed_size=20.0,
        padding=0.0,
        progress_callback=events.append,
        timeout_seconds=5.0,
    )

    assert grid == {"cx": 1.0, "cy": 2.0, "cz": 3.0, "sx": 20.0, "sy": 20.0, "sz": 20.0}
    assert warnings == []
    assert calls["run"] == 1
    assert any(
        str(event.get("type")) == "status"
        and str(event.get("stage")) == "p2rank"
        and "Running P2Rank" in str(event.get("delta") or "")
        for event in events
    )


def test_set_gridbox_falls_back_to_p2rank_when_native_is_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdb_file = tmp_path / "8PRK.pdb"
    pdb_text = _write_receptor_fixture(pdb_file, include_native=False)
    _install_receptor_meta("8PRK", pdb_file, pdb_text, [])
    autonomous_docking.AGENT_STATE["setup_rows"] = [["8PRK", "B", "", 20.0, "all"]]

    output_dir = tmp_path / "p2rank" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_states = iter(
        [
            {"status": "running", "message": "Running P2Rank for 8PRK (B)...", "error": ""},
            {"status": "done", "message": "Binding site prediction ready for 8PRK (B).", "error": ""},
        ]
    )
    events: list[dict[str, object]] = []

    monkeypatch.setattr(autonomous_docking, "run_p2rank_async", lambda *args, **kwargs: {"status": "running"})
    monkeypatch.setattr(autonomous_docking, "latest_output_dir", lambda *_args, **_kwargs: None if len(events) < 1 else output_dir)
    monkeypatch.setattr(
        autonomous_docking,
        "get_runtime_state",
        lambda: next(runtime_states, {"status": "done", "message": "Binding site prediction ready for 8PRK (B).", "error": ""}),
    )
    monkeypatch.setattr(
        autonomous_docking,
        "compute_gridbox_for_pocket",
        lambda *_args, **_kwargs: {"cx": 4.0, "cy": 5.0, "cz": 6.0, "sx": 20.0, "sy": 20.0, "sz": 20.0},
    )

    result = autonomous_docking.set_gridbox(
        method="native_ligand",
        size=20.0,
        padding=2.0,
        pocket_rank=1,
        p2rank_mode="fit",
        progress_callback=events.append,
    )

    assert result["ok"] is True
    assert result["gridbox_mode"] == "native_ligand"
    assert result["gridboxes"]["8PRK"]["center"] == [4.0, 5.0, 6.0]
    assert result["gridboxes"]["8PRK"]["size"] == [22.0, 22.0, 22.0]
    assert any(
        str(event.get("type")) == "status"
        and str(event.get("stage")) == "p2rank"
        and "Running P2Rank" in str(event.get("delta") or "")
        for event in events
    )
