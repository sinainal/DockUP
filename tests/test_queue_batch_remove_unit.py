from __future__ import annotations

import copy

from fastapi.testclient import TestClient

from docking_app.app import create_app
from docking_app.state import STATE


def test_remove_batch_matches_string_and_numeric_batch_ids() -> None:
    snapshot = copy.deepcopy(STATE)
    try:
        STATE["queue"] = [
            {"batch_id": "101", "pdb_id": "A"},
            {"batch_id": 101, "pdb_id": "B"},
            {"batch_id": "202", "pdb_id": "C"},
        ]

        response = TestClient(create_app()).post("/api/queue/remove_batch", json={"batch_id": 101})
        payload = response.json()

        assert response.status_code == 200
        assert payload["queue_count"] == 1
        assert payload["queue"] == [{"batch_id": "202", "pdb_id": "C"}]
    finally:
        STATE.clear()
        STATE.update(snapshot)
