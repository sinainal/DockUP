from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests._support.api_client import ApiClient
from tests._support.e2e_flow import cleanup_basic_flow, persist_tree_if_requested, provision_single_docking_run


@pytest.mark.e2e
@pytest.mark.slow
def test_e2e_basic_docking_flow(
    server_ready: None,
    api: ApiClient,
    test_cfg,
    e2e_artifacts_dir: Path | None,
) -> None:
    artifacts = None
    stamp = int(time.time() * 1000)
    try:
        artifacts = provision_single_docking_run(
            api,
            stamp=stamp,
            timeout_sec=test_cfg.e2e_timeout,
            interval_sec=test_cfg.poll_interval,
        )
        assert artifacts.out_root is not None and artifacts.out_root.exists(), "Expected output root to exist."
        persist_tree_if_requested(
            artifacts.out_root,
            e2e_artifacts_dir,
            f"basic_flow_out_root_{stamp}",
        )
    finally:
        if artifacts is not None:
            cleanup_basic_flow(api, artifacts)
