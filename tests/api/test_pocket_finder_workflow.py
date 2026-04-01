from __future__ import annotations

import time

import pytest

from tests._support.api_client import ApiClient
from tests._support.e2e_flow import clear_loaded_receptors


pytestmark = [pytest.mark.api, pytest.mark.slow]


def _wait_pocket_done(api: ApiClient, pdb_id: str, chain: str, *, timeout_sec: int, interval_sec: float) -> dict:
    deadline = time.time() + timeout_sec
    last = {}
    while time.time() < deadline:
        last = api.assert_ok(
            api.get(f"/api/pockets/status?pdb_id={pdb_id}&chain={chain}", timeout=30),
            where="GET /api/pockets/status",
        )
        status = str(last.get("status") or "").strip().lower()
        if status in {"done", "error"}:
            return last
        time.sleep(interval_sec)
    raise TimeoutError(f"Pocket finder did not finish in {timeout_sec}s. Last status: {last}")


def test_pocket_finder_run_results_and_gridbox(server_ready: None, api: ApiClient, test_cfg) -> None:
    pdb_id = "6CM4"
    chain = "all"
    try:
        clear_loaded_receptors(api)
        api.assert_ok(api.post("/api/receptors/add", {"pdb_ids": pdb_id}), where="POST /api/receptors/add")
        api.assert_ok(api.post("/api/receptors/select", {"pdb_id": pdb_id}), where="POST /api/receptors/select")

        api.assert_ok(
            api.post("/api/pockets/clear", {"pdb_id": pdb_id, "chain": chain}, timeout=30),
            where="POST /api/pockets/clear",
        )

        started = api.assert_ok(
            api.post(
                "/api/pockets/run",
                {"pdb_id": pdb_id, "chain": chain, "force_rerun": True},
                timeout=60,
            ),
            where="POST /api/pockets/run",
        )
        assert str(started.get("status") or "").strip().lower() in {"running", "done"}, started

        finished = _wait_pocket_done(
            api,
            pdb_id,
            chain,
            timeout_sec=min(max(300, test_cfg.e2e_timeout), 900),
            interval_sec=max(1.0, float(test_cfg.poll_interval)),
        )
        assert str(finished.get("status") or "").strip().lower() == "done", finished

        results = api.assert_ok(
            api.get(f"/api/pockets/results?pdb_id={pdb_id}&chain={chain}", timeout=60),
            where="GET /api/pockets/results",
        )
        pockets = list(results.get("pockets") or [])
        assert pockets, f"Pocket finder returned no pockets: {results}"

        first = pockets[0]
        pocket_rank = int(first.get("rank") or 0)
        assert pocket_rank > 0, first

        protein_file = api.get(f"/api/pockets/file?kind=protein&pdb_id={pdb_id}&chain={chain}", timeout=60)
        assert protein_file.status_code == 200, protein_file.text[:400]

        points_file = api.get(f"/api/pockets/file?kind=points&pdb_id={pdb_id}&chain={chain}", timeout=60)
        assert points_file.status_code == 200, points_file.text[:400]

        gridbox = api.assert_ok(
            api.post(
                "/api/pockets/gridbox",
                {
                    "pdb_id": pdb_id,
                    "chain": chain,
                    "pocket_rank": pocket_rank,
                    "mode": "fit",
                    "padding": 2.0,
                },
                timeout=30,
            ),
            where="POST /api/pockets/gridbox",
        )
        grid_data = dict(gridbox.get("grid_data") or {})
        for key in ("cx", "cy", "cz", "sx", "sy", "sz"):
            assert key in grid_data, gridbox
            assert float(grid_data[key]) > 0 or key.startswith("c"), gridbox
    finally:
        try:
            api.post("/api/pockets/clear", {"pdb_id": pdb_id, "chain": chain}, timeout=30)
        except Exception:
            pass
        try:
            api.post("/api/receptors/remove", {"pdb_id": pdb_id}, timeout=30)
        except Exception:
            pass
