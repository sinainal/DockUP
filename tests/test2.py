from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
import requests

BASE_URL = "http://localhost:8000"
WORKSPACE = Path("/home/sina/Downloads/ngl/DockUP/docking_app/workspace")
DOCK_DIR = WORKSPACE / "data" / "dock"
SESSIONS_DIR = DOCK_DIR / ".sessions"
SESSIONS_INDEX = SESSIONS_DIR / "index.json"


def get(path: str, **kwargs) -> requests.Response:
    return requests.get(f"{BASE_URL}{path}", timeout=15, **kwargs)


def post(path: str, json_data: Any = None, **kwargs) -> requests.Response:
    return requests.post(f"{BASE_URL}{path}", json=json_data, timeout=20, **kwargs)


def assert_ok(resp: requests.Response, msg: str = "") -> dict[str, Any]:
    assert resp.status_code == 200, f"{msg} -> {resp.status_code}: {resp.text[:300]}"
    return resp.json()


@pytest.fixture(scope="module", autouse=True)
def server_ready() -> None:
    try:
        resp = get("/api/state")
    except requests.RequestException as exc:
        pytest.skip(f"Server not reachable at {BASE_URL}: {exc}")
    if resp.status_code != 200:
        pytest.skip(f"Server not ready: {resp.status_code}")


def clear_loaded_receptors() -> None:
    summary = assert_ok(get("/api/receptors/summary"), "receptor summary")
    for row in summary.get("summary", []):
        pdb_id = str((row or {}).get("pdb_id") or "").strip()
        if pdb_id:
            post("/api/receptors/remove", {"pdb_id": pdb_id})


def clear_queue() -> None:
    probe = assert_ok(
        post(
            "/api/queue/build",
            {
                "run_count": 1,
                "padding": 0.0,
                "selection_map": {},
                "grid_data": {},
                "mode": "Docking",
                "docking_config": {},
                "out_root_path": "data/dock",
                "out_root_name": f"test2_probe_{int(time.time() * 1000)}",
            },
        ),
        "queue build probe",
    )
    queue_rows = list(probe.get("queue") or [])
    batch_ids = {
        int(item.get("batch_id"))
        for item in queue_rows
        if isinstance(item, dict) and item.get("batch_id") is not None
    }
    for batch_id in sorted(batch_ids):
        post("/api/queue/remove_batch", {"batch_id": batch_id})


class TestUiMimicStateFlows:
    def test_store_add_select_build_and_remove_queue_batch(self):
        assert_ok(post("/api/mode", {"mode": "Docking"}), "set mode")
        clear_queue()
        clear_loaded_receptors()
        assert_ok(post("/api/ligands/active/clear", {}), "clear active ligands")

        # 1) UI: receptor fetch/store
        assert_ok(post("/api/receptors/store", {"pdb_ids": "6CM4"}), "store receptor")

        # 2) UI: add from stored receptors to active docking table
        add_data = assert_ok(post("/api/receptors/add", {"pdb_ids": "6CM4"}), "add receptor")
        summary_ids = {str((row or {}).get("pdb_id") or "") for row in add_data.get("summary", [])}
        assert "6CM4" in summary_ids, f"6CM4 not in docking summary: {summary_ids}"

        # 3) UI: ligand pool -> dock-ready
        ligands = assert_ok(get("/api/ligands/list"), "ligands list").get("ligands", [])
        assert ligands, "No ligand found in inventory"
        ligand_name = str(ligands[0])
        active = assert_ok(post("/api/ligands/active/add", {"names": [ligand_name]}), "active add")
        assert ligand_name in set(active.get("active_ligands") or [])

        # 4) UI: per-receptor ligand selection
        assert_ok(
            post(
                "/api/ligands/select",
                {"pdb_id": "6CM4", "chain": "all", "ligand": ligand_name},
            ),
            "ligand select",
        )

        # 5) UI: build queue
        out_root_name = f"test2_ui_build_{int(time.time() * 1000)}"
        build = assert_ok(
            post(
                "/api/queue/build",
                {
                    "run_count": 1,
                    "padding": 0.0,
                    "out_root_name": out_root_name,
                    "out_root_path": "data/dock",
                    "selection_map": {
                        "6CM4": {"chain": "all", "ligand_resname": ligand_name},
                    },
                    "grid_data": {
                        "6CM4": {"cx": 0.0, "cy": 0.0, "cz": 0.0, "sx": 20.0, "sy": 20.0, "sz": 20.0},
                    },
                    "mode": "Docking",
                    "docking_config": {},
                },
            ),
            "queue build",
        )
        assert int((build.get("debug") or {}).get("new_jobs_added") or 0) >= 1
        queue_rows = [row for row in (build.get("queue") or []) if isinstance(row, dict)]
        assert any(str(row.get("pdb_id") or "") == "6CM4" for row in queue_rows)
        assert any(str(row.get("ligand_resname") or "") == ligand_name for row in queue_rows)

        # 6) UI: remove batch and verify queue_count sync
        batch_ids = {int(row["batch_id"]) for row in queue_rows if row.get("batch_id") is not None}
        for bid in sorted(batch_ids):
            assert post("/api/queue/remove_batch", {"batch_id": bid}).status_code in (200, 404)

        state = assert_ok(get("/api/state"), "state after remove batch")
        assert int(state.get("queue_count") or 0) == 0

    def test_build_queue_detects_stale_selected_ligand_after_active_pool_change(self):
        assert_ok(post("/api/mode", {"mode": "Docking"}), "set mode")
        clear_queue()
        clear_loaded_receptors()
        assert_ok(post("/api/ligands/active/clear", {}), "clear active ligands")

        assert_ok(post("/api/receptors/add", {"pdb_ids": "6CM4"}), "add receptor")

        ligands = assert_ok(get("/api/ligands/list"), "ligands list").get("ligands", [])
        assert ligands, "No ligand found in inventory"
        ligand_name = str(ligands[0])

        assert_ok(post("/api/ligands/active/add", {"names": [ligand_name]}), "active add")
        assert_ok(
            post(
                "/api/ligands/select",
                {"pdb_id": "6CM4", "chain": "all", "ligand": ligand_name},
            ),
            "ligand select",
        )

        payload = {
            "run_count": 1,
            "padding": 0.0,
            "out_root_name": f"test2_stale_{int(time.time() * 1000)}",
            "out_root_path": "data/dock",
            "selection_map": {"6CM4": {"chain": "all", "ligand_resname": ligand_name}},
            "grid_data": {"6CM4": {"cx": 1.0, "cy": 1.0, "cz": 1.0, "sx": 20.0, "sy": 20.0, "sz": 20.0}},
            "mode": "Docking",
            "docking_config": {},
        }

        first = post("/api/queue/build", payload)
        assert first.status_code == 200, first.text[:300]
        clear_queue()

        # Simulate stale frontend cache: selected ligand remains, active pool changed
        assert_ok(post("/api/ligands/active/remove", {"name": ligand_name}), "active remove")
        stale = post("/api/queue/build", payload)
        assert stale.status_code == 400, stale.text[:300]
        assert "not in dock-ready ligands" in stale.text


class TestRecentDockingsCacheInvalidation:
    def test_recent_list_reflects_delete_immediately(self):
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        backup = SESSIONS_INDEX.read_text(encoding="utf-8") if SESSIONS_INDEX.exists() else None

        session_id = f"sess_test2_{int(time.time() * 1000)}"
        session_dir = SESSIONS_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        manifest_snapshot = session_dir / "manifest.tsv"
        manifest_snapshot.write_text("", encoding="utf-8")

        sessions_payload = {
            "sessions": [
                {
                    "id": session_id,
                    "created_ts": time.time(),
                    "dock_root": "test2_root",
                    "out_root": str((DOCK_DIR / "test2_recent_root").resolve()),
                    "manifest_snapshot": str(manifest_snapshot.resolve()),
                    "runs": 1,
                    "planned_total": 1,
                }
            ]
        }

        try:
            SESSIONS_INDEX.write_text(json.dumps(sessions_payload), encoding="utf-8")

            deleted = post("/api/run/recent/delete", {"item_id": session_id})
            assert deleted.status_code == 200, deleted.text[:300]

            recent2 = assert_ok(get("/api/run/recent?limit=20"), "recent after delete")
            ids2 = {str((row or {}).get("id") or "") for row in (recent2.get("rows") or [])}
            assert session_id not in ids2, f"Deleted session still appears in recent list: {ids2}"
            assert not session_dir.exists(), "Session folder still exists after delete"
        finally:
            if backup is None:
                SESSIONS_INDEX.unlink(missing_ok=True)
            else:
                SESSIONS_INDEX.write_text(backup, encoding="utf-8")
            if session_dir.exists():
                for p in sorted(session_dir.rglob("*"), reverse=True):
                    if p.is_file():
                        p.unlink(missing_ok=True)
                    elif p.is_dir():
                        p.rmdir()
                session_dir.rmdir()

    def test_recent_endpoint_stable_on_back_to_back_refresh(self):
        data1 = assert_ok(get("/api/run/recent?limit=10"), "recent first")
        data2 = assert_ok(get("/api/run/recent?limit=10"), "recent second")

        rows1 = data1.get("rows") or []
        rows2 = data2.get("rows") or []
        ids1 = [str((row or {}).get("id") or "") for row in rows1]
        ids2 = [str((row or {}).get("id") or "") for row in rows2]

        assert len(rows1) == len(rows2), "Recent list count changed without state mutation"
        assert ids1 == ids2, "Recent list order/content changed between immediate refreshes"
