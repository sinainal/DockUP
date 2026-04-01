from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from docking_app.pocket_finder import runner
from docking_app.routes import pocket


@pytest.mark.unit
def test_run_p2rank_async_fails_before_marking_running(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    receptor_file = tmp_path / "input.pdb"
    receptor_file.write_text("ATOM\n", encoding="utf-8")

    snapshot = runner.get_runtime_state()
    runner.clear_runtime_state()
    monkeypatch.setattr(
        runner,
        "preflight_p2rank_runtime",
        lambda: (_ for _ in ()).throw(FileNotFoundError("P2Rank executable not found")),
    )

    try:
        with pytest.raises(FileNotFoundError, match="P2Rank executable not found"):
            runner.run_p2rank_async("6CM4", receptor_file, chain="all")
        assert runner.get_runtime_state().get("status") == "idle"
    finally:
        runner.clear_runtime_state()
        runner._set_state(**snapshot)


@pytest.mark.unit
def test_run_binding_site_finder_returns_503_when_runtime_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receptor_file = tmp_path / "receptor.pdb"
    receptor_file.write_text("ATOM\n", encoding="utf-8")

    monkeypatch.setattr(pocket, "_selected_receptor_id", lambda _raw=None: "6CM4")
    monkeypatch.setattr(pocket, "_selected_chain_for_receptor", lambda _pdb_id, _raw=None: "all")
    monkeypatch.setattr(pocket, "_running_other_receptor", lambda _requested, _chain="all": False)
    monkeypatch.setattr(pocket, "_selected_receptor_file", lambda _pdb_id: receptor_file)
    monkeypatch.setattr(
        pocket,
        "run_p2rank_async",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("P2Rank executable not found")),
    )

    with pytest.raises(HTTPException) as exc_info:
        pocket.run_binding_site_finder({"pdb_id": "6CM4", "chain": "all", "force_rerun": True})

    assert exc_info.value.status_code == 503
    assert "P2Rank executable not found" in str(exc_info.value.detail)
