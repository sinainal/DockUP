from __future__ import annotations

import shutil
import time
import urllib.parse
from pathlib import Path

import pytest
import requests

from tests._support.api_client import ApiClient
from tests._support.e2e_flow import cleanup_basic_flow, persist_tree_if_requested, provision_single_docking_run, wait_report_idle


@pytest.mark.e2e
@pytest.mark.render
@pytest.mark.slow
def test_e2e_full_report_flow(
    server_ready: None,
    api: ApiClient,
    test_cfg,
    e2e_artifacts_dir: Path | None,
) -> None:
    stamp = int(time.time() * 1000)
    artifacts = None
    output_abs = None

    try:
        artifacts = provision_single_docking_run(
            api,
            stamp=stamp,
            timeout_sec=test_cfg.e2e_timeout,
            interval_sec=test_cfg.poll_interval,
        )
        assert artifacts.out_root is not None, "Basic flow did not produce out_root."

        source_path = f"data/dock/{artifacts.out_root.name}"
        receptor_id = str(artifacts.receptor_id or "6CM4")
        output_abs = (artifacts.out_root / f"report_outputs_e2e_{stamp}").resolve()
        output_path = str(output_abs)

        wait_report_idle(api, timeout_sec=30, interval_sec=1.0)

        graphs = api.assert_ok(
            api.post(
                "/api/reports/graphs",
                {
                    "root_path": "data/dock",
                    "source_path": source_path,
                    "output_path": output_path,
                    "linked_path": "",
                    "scripts": [],
                },
                timeout=60,
            ),
            where="POST /api/reports/graphs",
        )
        assert str(graphs.get("status") or "") == "started", f"graphs did not start: {graphs}"

        status_after_graphs = wait_report_idle(
            api,
            timeout_sec=max(120, test_cfg.e2e_timeout // 2),
            interval_sec=test_cfg.poll_interval,
        )
        assert not list(status_after_graphs.get("errors") or []), f"Graph generation errors: {status_after_graphs}"

        render = api.assert_ok(
            api.post(
                "/api/reports/render",
                {
                    "root_path": "data/dock",
                    "source_path": source_path,
                    "output_path": output_path,
                    "linked_path": "",
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
            timeout_sec=max(180, test_cfg.e2e_timeout // 2),
            interval_sec=test_cfg.poll_interval,
        )
        assert not list(status_after_render.get("errors") or []), f"Render errors: {status_after_render}"

        list_query = urllib.parse.urlencode(
            {"root_path": "data/dock", "source_path": source_path, "output_path": output_path}
        )
        report_payload = api.assert_ok(api.get(f"/api/reports/list?{list_query}"), where="GET /api/reports/list final")
        render_images = list(report_payload.get("render_images") or [])
        plot_images = list(report_payload.get("plot_images") or [])
        assert render_images or plot_images, "No render_images or plot_images produced."

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
        assert images, "reports/images returned no images."
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
            f"full_flow_out_root_{stamp}",
        )
        persist_tree_if_requested(
            output_abs,
            e2e_artifacts_dir,
            f"full_flow_report_outputs_{stamp}",
        )
    finally:
        if output_abs and output_abs.exists():
            shutil.rmtree(output_abs, ignore_errors=True)
        if artifacts is not None:
            cleanup_basic_flow(api, artifacts)
