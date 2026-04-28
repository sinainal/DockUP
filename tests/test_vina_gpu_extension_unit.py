from __future__ import annotations

from pathlib import Path

import pytest

from docking_app.helpers import normalize_docking_config
from docking_app.manifest import append_docking_config_args, parse_manifest_rows, write_manifest


pytestmark = pytest.mark.unit


def test_docking_engine_normalization_defaults_to_cpu_vina() -> None:
    assert normalize_docking_config({})["docking_engine"] == "vina"
    assert normalize_docking_config({"docking_engine": "not-real"})["docking_engine"] == "vina"


def test_docking_engine_normalization_accepts_vina_gpu_aliases() -> None:
    assert normalize_docking_config({"docking_engine": "vina-gpu-21"})["docking_engine"] == "vina_gpu_21"
    assert normalize_docking_config({"docking_engine": "vina_gpu_2_1"})["docking_engine"] == "vina_gpu_21"


def test_manifest_roundtrip_preserves_vina_gpu_engine(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.tsv"
    write_manifest(
        [
            {
                "pdb_id": "6CM4",
                "chain": "all",
                "ligand": "LigandA",
                "docking_config": {"docking_engine": "vina_gpu_21", "vina_exhaustiveness": 8},
                "job_type": "Docking",
            }
        ],
        manifest,
    )

    rows = parse_manifest_rows(manifest)

    assert rows[0]["docking_config"]["docking_engine"] == "vina_gpu_21"
    assert rows[0]["job_type"] == "Docking"


def test_old_manifest_schema_keeps_job_type_and_defaults_engine(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.tsv"
    old_columns = [
        "6CM4",
        "all",
        "LigandA",
        "__EMPTY__",
        "__EMPTY__",
        "__EMPTY__",
        "__EMPTY__",
        "__EMPTY__",
        "__EMPTY__",
        "7.4",
        "AMBER",
        "AMBER",
        "1",
        "1",
        "1",
        "A",
        "standard",
        "8",
        "__EMPTY__",
        "__EMPTY__",
        "__EMPTY__",
        "__EMPTY__",
        "Docking",
    ]
    manifest.write_text("\t".join(old_columns) + "\n", encoding="utf-8")

    rows = parse_manifest_rows(manifest)

    assert rows[0]["docking_config"]["docking_engine"] == "vina"
    assert rows[0]["job_type"] == "Docking"


def test_preview_args_include_docking_engine() -> None:
    args: list[str] = []

    append_docking_config_args(args, {"docking_engine": "vina_gpu_21"})

    assert "--docking_engine" in args
    assert args[args.index("--docking_engine") + 1] == "vina_gpu_21"


def test_extension_status_route_available_in_app() -> None:
    from fastapi.testclient import TestClient

    from docking_app.app import create_app

    response = TestClient(create_app()).get("/api/extensions/vina-gpu-21/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "vina_gpu_21"
    assert isinstance(payload.get("requirements"), list)


def test_extension_uninstall_route_resets_default_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from docking_app.app import create_app
    from docking_app.extensions import vina_gpu_21
    from docking_app.state import STATE

    monkeypatch.setattr(vina_gpu_21, "start_uninstall", lambda: {"ok": True, "installed": False})
    STATE["docking_config"] = normalize_docking_config({"docking_engine": "vina_gpu_21"})

    response = TestClient(create_app()).post("/api/extensions/vina-gpu-21/uninstall")

    assert response.status_code == 200
    assert normalize_docking_config(STATE["docking_config"])["docking_engine"] == "vina"
