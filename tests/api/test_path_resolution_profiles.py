from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from tests._support.api_client import ApiClient


pytestmark = [pytest.mark.api]


@pytest.mark.strict_clean
def test_path_profile_strict_clean(server_ready: None, api: ApiClient, test_cfg) -> None:
    stamp = int(time.time() * 1000)
    run_dir = test_cfg.dock_dir / f"strict_clean_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        rel_path = f"data/dock/{run_dir.name}"
        abs_path = str(run_dir.resolve())

        rel_scan = api.assert_ok(api.post("/api/results/scan", {"root_path": rel_path}), where="results/scan rel")
        assert isinstance(rel_scan.get("runs"), list), f"Unexpected rel scan payload: {rel_scan}"

        abs_scan = api.assert_ok(api.post("/api/results/scan", {"root_path": abs_path}), where="results/scan abs")
        assert isinstance(abs_scan.get("runs"), list), f"Unexpected abs scan payload: {abs_scan}"

        outside = api.post("/api/results/scan", {"root_path": "/tmp"})
        assert outside.status_code == 400, f"Expected 400 for outside path, got {outside.status_code}: {outside.text}"

        missing_source = api.get(
            "/api/reports/root-metadata",
            params={"root_path": "data/dock", "source_path": f"data/dock/does_not_exist_{stamp}"},
        )
        assert missing_source.status_code == 400, (
            f"Expected 400 for missing strict source path, got {missing_source.status_code}: {missing_source.text}"
        )
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


@pytest.mark.legacy_data
def test_path_profile_legacy_data(server_ready: None, api: ApiClient, test_cfg) -> None:
    source_dir = test_cfg.dock_dir / "dimer_final_linked"
    if not source_dir.exists():
        pytest.skip("Legacy dataset not present: data/dock/dimer_final_linked")

    source_path = "data/dock/dimer_final_linked"
    listed = api.assert_ok(
        api.get("/api/reports/list", params={"root_path": "data/dock", "source_path": source_path}),
        where="reports/list legacy source",
    )
    assert "receptors" in listed and isinstance(listed.get("receptors"), list), f"Unexpected legacy list payload: {listed}"

    meta = api.get("/api/reports/root-metadata", params={"root_path": "data/dock", "source_path": source_path})
    assert meta.status_code == 200, f"Expected 200 for legacy root-metadata, got {meta.status_code}: {meta.text}"

    output_dir = source_dir / "report_outputs"
    if output_dir.exists():
        images = api.get(
            "/api/reports/images",
            params={
                "root_path": "data/dock",
                "source_path": source_path,
                "output_path": "data/dock/dimer_final_linked/report_outputs",
                "images_root_path": "data/dock/dimer_final_linked/report_outputs",
            },
        )
        assert images.status_code == 200, f"Expected 200 for legacy images path, got {images.status_code}: {images.text}"

