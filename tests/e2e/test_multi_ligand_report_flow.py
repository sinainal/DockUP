from __future__ import annotations

import shutil
import time
import urllib.parse
from pathlib import Path

import pytest
import requests

from tests._support.api_client import ApiClient
from tests._support.e2e_flow import (
    cleanup_basic_flow,
    persist_tree_if_requested,
    provision_multi_ligand_run,
    wait_report_idle,
)


@pytest.mark.e2e
@pytest.mark.render
@pytest.mark.slow
def test_e2e_multi_ligand_report_flow(
    server_ready: None,
    api: ApiClient,
    test_cfg,
    e2e_artifacts_dir: Path | None,
) -> None:
    stamp = int(time.time() * 1000)
    artifacts = None
    output_abs = None

    try:
        artifacts = provision_multi_ligand_run(
            api,
            stamp=stamp,
            timeout_sec=test_cfg.e2e_timeout,
            interval_sec=test_cfg.poll_interval,
        )
        assert artifacts.out_root is not None, "Multi-ligand flow did not produce out_root."

        source_path = f"data/dock/{artifacts.out_root.name}"
        receptor_id = str(artifacts.receptor_id or "")

        scan_payload = api.assert_ok(
            api.post("/api/results/scan", {"root_path": source_path}),
            where="POST /api/results/scan",
        )
        run_rows = list(scan_payload.get("runs") or [])
        multi_row = next((row for row in run_rows if row.get("multi_ligand")), None)
        assert multi_row is not None, f"Expected a multi-ligand result row: {run_rows}"
        assert int(multi_row.get("ligand_count") or 0) == 2

        detail_payload = api.assert_ok(
            api.post("/api/results/detail", {"result_dir": multi_row["result_dir"]}),
            where="POST /api/results/detail",
        )
        result = detail_payload.get("result") or {}
        assert result.get("multi_ligand") is True, result
        sites = list(detail_payload.get("sites") or [])
        assert len(sites) == 2, sites
        assert {site.get("site_id") for site in sites} == {"site_1", "site_2"}

        output_abs = (artifacts.out_root / f"report_outputs_multi_e2e_{stamp}").resolve()
        output_path = str(output_abs)
        wait_report_idle(api, timeout_sec=30, interval_sec=1.0)

        render = api.assert_ok(
            api.post(
                "/api/reports/render",
                {
                    "root_path": "data/dock",
                    "source_path": source_path,
                    "output_path": output_path,
                    "render_mode": "multi_ligand_panel",
                    "dpi": 100,
                    "receptors": [receptor_id],
                    "run_by_receptor": {receptor_id: "run1"},
                    "is_preview": True,
                },
                timeout=60,
            ),
            where="POST /api/reports/render",
        )
        assert str(render.get("status") or "") == "started", f"render did not start: {render}"

        status_after_render = wait_report_idle(
            api,
            timeout_sec=max(240, test_cfg.e2e_timeout),
            interval_sec=test_cfg.poll_interval,
        )
        assert not list(status_after_render.get("errors") or []), f"Render errors: {status_after_render}"

        list_query = urllib.parse.urlencode(
            {"root_path": "data/dock", "source_path": source_path, "output_path": output_path}
        )
        report_payload = api.assert_ok(api.get(f"/api/reports/list?{list_query}"), where="GET /api/reports/list final")
        render_images = list(report_payload.get("render_images") or [])
        assert render_images, "No render_images produced for multi-ligand render."
        assert any("multi_ligand_panel" in str(row.get("name") or "") for row in render_images), render_images

        image_query = urllib.parse.urlencode(
            {
                "root_path": "data/dock",
                "source_path": source_path,
                "output_path": output_path,
                "images_root_path": output_path,
            }
        )
        images_payload = api.assert_ok(api.get(f"/api/reports/images?{image_query}"), where="GET /api/reports/images")
        images = list(images_payload.get("images") or [])
        assert images, "reports/images returned no images for multi-ligand run."
        first_path = str(images[0].get("path") or "").strip()
        assert first_path, f"First image path is empty: {images[0]}"

        serve_url = f"{test_cfg.base_url}/api/reports/image/{first_path}"
        served = requests.get(serve_url, timeout=30)
        assert served.status_code == 200, f"Failed to serve generated image: {serve_url}"
        assert "image" in str(served.headers.get("content-type") or "").lower(), (
            f"Unexpected image content-type: {served.headers}"
        )

        persist_tree_if_requested(
            artifacts.out_root,
            e2e_artifacts_dir,
            f"multi_ligand_out_root_{stamp}",
        )
        persist_tree_if_requested(
            output_abs,
            e2e_artifacts_dir,
            f"multi_ligand_report_outputs_{stamp}",
        )
    finally:
        if output_abs and output_abs.exists():
            shutil.rmtree(output_abs, ignore_errors=True)
        if artifacts is not None:
            cleanup_basic_flow(api, artifacts)
