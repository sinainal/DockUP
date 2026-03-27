from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
from PIL import Image
from starlette.background import BackgroundTasks

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from docking_app.models import RenderPayload
from docking_app.routes import report
from docking_app.state import REPORT_STATE


@pytest.mark.unit
def test_normalize_render_mode_supports_otofigure_aliases() -> None:
    assert report._normalize_render_mode("classic") == report.REPORT_RENDER_MODE_CLASSIC
    assert report._normalize_render_mode("multi_run") == report.REPORT_RENDER_MODE_OTOFIGURE
    assert report._normalize_render_mode("otofigure") == report.REPORT_RENDER_MODE_OTOFIGURE


@pytest.mark.unit
def test_trigger_render_dispatches_otofigure_builder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    snapshot = copy.deepcopy(REPORT_STATE)
    source_dir = tmp_path / "dopamine_trimer"
    output_root = tmp_path / "report_outputs"
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = source_dir / "6CM4" / "Ethylene_trimer" / "run1"
    run_dir.mkdir(parents=True, exist_ok=True)

    inventory = {"6CM4": {"Ethylene_trimer": [("run1", run_dir)]}}
    rows = [{"id": "6CM4", "ready": True, "run_options": ["run1"], "default_run": "run1"}]
    called: list[str] = []

    def fake_classic_builder(
        dtype,
        _inventory,
        out_dir,
        _temp_root,
        _dpi,
        preferred_run="run1",
        output_stem="",
        preview_mode=False,
        ligand_order_index=None,
    ):
        called.append("classic")
        out_path = Path(out_dir) / f"{output_stem or dtype}_classic.png"
        Image.new("RGB", (32, 32), "white").save(out_path)
        return out_path, [preferred_run]

    def fake_otofigure_builder(
        dtype,
        _inventory,
        out_dir,
        _temp_root,
        _dpi,
        preferred_run="run1",
        output_stem="",
        preview_mode=False,
        ligand_order_index=None,
    ):
        called.append("otofigure")
        out_path = Path(out_dir) / f"{output_stem or dtype}_otofigure.png"
        Image.new("RGB", (32, 32), "white").save(out_path)
        return out_path, [preferred_run]

    monkeypatch.setattr(report, "_resolve_report_root", lambda _root_path: tmp_path)
    monkeypatch.setattr(report, "_resolve_report_source", lambda _report_root, _source_path: source_dir)
    monkeypatch.setattr(report, "_resolve_report_output_root", lambda _report_root, _source_dir, _output_path: output_root)
    monkeypatch.setattr(report, "_collect_receptor_rows", lambda _source_dir: rows)
    monkeypatch.setattr(report, "_collect_entities_from_rows", lambda _rows: (["6CM4"], ["Ethylene_trimer"]))
    monkeypatch.setattr(report, "_load_source_metadata", lambda *_args, **_kwargs: {"ligand_order": ["Ethylene_trimer"]})
    monkeypatch.setattr(report, "_collect_receptor_inventory", lambda _source_dir: inventory)
    monkeypatch.setattr(report, "_render_dtype_panel", fake_classic_builder)
    monkeypatch.setattr(report, "_render_dtype_otofigure_panel", fake_otofigure_builder)

    background_tasks = BackgroundTasks()
    try:
        response = report.trigger_render(
            RenderPayload(
                root_path="data/dock",
                source_path="data/dock/dopamine_trimer",
                output_path=str(output_root),
                dpi=100,
                render_mode="otofigure",
                receptors=["6CM4"],
                run_by_receptor={"6CM4": "run1"},
                is_preview=True,
            ),
            background_tasks,
        )
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["status"] == "started"
        assert len(background_tasks.tasks) == 1

        task = background_tasks.tasks[0]
        task.func(*task.args, **task.kwargs)

        assert called == ["otofigure"]
        render_dir = output_root / "render_images"
        assert any(render_dir.glob("*_otofigure_*.png"))
    finally:
        REPORT_STATE.clear()
        REPORT_STATE.update(snapshot)


@pytest.mark.unit
def test_report_resolve_dock_directory_accepts_new_relative_data_dock_path(tmp_path: Path) -> None:
    default_dir = tmp_path / "default"
    default_dir.mkdir(parents=True, exist_ok=True)
    resolved = report.resolve_dock_directory(
        "data/dock/6CM4/report_outputs_browser_otofigure",
        default=default_dir,
        allow_create=True,
    )
    assert str(resolved).endswith("data/dock/6CM4/report_outputs_browser_otofigure")


@pytest.mark.unit
def test_select_otofigure_ligand_runs_prefers_highest_run_count() -> None:
    inventory = {
        "3PBL": {
            "LigandA": [("run1", Path("/tmp/a1"))],
            "LigandB": [
                ("run1", Path("/tmp/b1")),
                ("run2", Path("/tmp/b2")),
                ("run3", Path("/tmp/b3")),
            ],
        }
    }

    ligand_name, run_entries = report._select_otofigure_ligand_runs(inventory, "3PBL")

    assert ligand_name == "LigandB"
    assert [name for name, _ in run_entries] == ["run1", "run2", "run3"]
