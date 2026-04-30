from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from tests._support.api_client import ApiClient
from tests._support.e2e_flow import (
    cleanup_basic_flow,
    persist_tree_if_requested,
    provision_generated_multi_ligand_run,
    provision_single_docking_run,
)


VINA_GPU_CONFIG = {
    "docking_engine": "vina_gpu_21",
    "docking_mode": "standard",
    "vina_exhaustiveness": 8,
    "vina_num_modes": 5,
}


def _wait_extension_idle(api: ApiClient, *, timeout_sec: int, interval_sec: float) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = api.assert_ok(
            api.get("/api/extensions/vina-gpu-21/status", timeout=30),
            where="GET /api/extensions/vina-gpu-21/status",
        )
        if not ((last.get("job") or {}).get("running")):
            return last
        time.sleep(interval_sec)
    raise TimeoutError(f"Vina-GPU extension job did not finish. Last status: {last}")


def _ensure_vina_gpu_21_ready(api: ApiClient, *, timeout_sec: int, interval_sec: float) -> dict[str, Any]:
    status = api.assert_ok(
        api.get("/api/extensions/vina-gpu-21/status", timeout=30),
        where="GET /api/extensions/vina-gpu-21/status",
    )
    if not status.get("installed"):
        assert status.get("requirements_ok"), f"Vina-GPU requirements are not ready: {status}"
        api.assert_ok(
            api.post("/api/extensions/vina-gpu-21/install", {}, timeout=30),
            where="POST /api/extensions/vina-gpu-21/install",
        )
        status = _wait_extension_idle(api, timeout_sec=timeout_sec, interval_sec=interval_sec)
        assert status.get("installed"), f"Vina-GPU install did not finish installed: {status}"
        assert not (status.get("job") or {}).get("error"), f"Vina-GPU install error: {status}"

    if not status.get("tested"):
        api.assert_ok(
            api.post("/api/extensions/vina-gpu-21/test", {}, timeout=30),
            where="POST /api/extensions/vina-gpu-21/test",
        )
        status = _wait_extension_idle(api, timeout_sec=max(240, timeout_sec // 3), interval_sec=interval_sec)
        assert status.get("tested"), f"Vina-GPU smoke test did not pass: {status}"
        assert not (status.get("job") or {}).get("error"), f"Vina-GPU smoke error: {status}"
    return status


@pytest.mark.e2e
@pytest.mark.slow
def test_e2e_vina_gpu_21_single_ligand_flow(
    server_ready: None,
    api: ApiClient,
    test_cfg,
    e2e_artifacts_dir: Path | None,
) -> None:
    _ensure_vina_gpu_21_ready(api, timeout_sec=max(3600, test_cfg.e2e_timeout), interval_sec=test_cfg.poll_interval)
    artifacts = None
    stamp = int(time.time() * 1000)
    try:
        artifacts = provision_single_docking_run(
            api,
            stamp=stamp,
            timeout_sec=max(1800, test_cfg.e2e_timeout),
            interval_sec=test_cfg.poll_interval,
            docking_config=VINA_GPU_CONFIG,
            receptor_id="2BM2",
        )
        assert artifacts.out_root is not None and artifacts.out_root.exists()
        result_files = list(artifacts.out_root.rglob("results.json"))
        assert result_files, f"No Vina-GPU single-ligand results found under {artifacts.out_root}"
        assert list(artifacts.out_root.rglob("*_vina_gpu*.log")), "Expected Vina-GPU log in single-ligand output."
        persist_tree_if_requested(artifacts.out_root, e2e_artifacts_dir, f"vina_gpu_single_out_root_{stamp}")
    finally:
        if artifacts is not None:
            cleanup_basic_flow(api, artifacts)


@pytest.mark.e2e
@pytest.mark.slow
def test_e2e_vina_gpu_21_multi_ligand_flow(
    server_ready: None,
    api: ApiClient,
    test_cfg,
    e2e_artifacts_dir: Path | None,
) -> None:
    _ensure_vina_gpu_21_ready(api, timeout_sec=max(3600, test_cfg.e2e_timeout), interval_sec=test_cfg.poll_interval)
    artifacts = None
    stamp = int(time.time() * 1000)
    try:
        artifacts = provision_generated_multi_ligand_run(
            api,
            stamp=stamp,
            timeout_sec=max(1800, test_cfg.e2e_timeout),
            interval_sec=test_cfg.poll_interval,
            docking_config=VINA_GPU_CONFIG,
            receptor_id="2BM2",
        )
        assert artifacts.out_root is not None and artifacts.out_root.exists()
        result_files = list(artifacts.out_root.rglob("results.json"))
        assert result_files, f"No Vina-GPU multi-ligand results found under {artifacts.out_root}"
        assert list(artifacts.out_root.rglob("*_vina_gpu*.log")), "Expected Vina-GPU log in multi-ligand output."
        persist_tree_if_requested(artifacts.out_root, e2e_artifacts_dir, f"vina_gpu_multi_out_root_{stamp}")
    finally:
        if artifacts is not None:
            cleanup_basic_flow(api, artifacts)
