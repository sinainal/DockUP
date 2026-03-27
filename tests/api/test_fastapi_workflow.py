from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
import requests

from tests._support.api_client import ApiClient
from tests._support.e2e_flow import clear_loaded_receptors, clear_queue


pytestmark = [pytest.mark.api]


def _upload_temp_ligand(base_url: str, tmp_path: Path, stem: str) -> str:
    sdf_path = tmp_path / f"{stem}.sdf"
    sdf_path.write_text(f"{stem}\n  Ketcher\n\n  0  0  0     0  0            999 V2000\nM  END\n$$$$\n", encoding="utf-8")
    with sdf_path.open("rb") as handle:
        resp = requests.post(
            f"{base_url.rstrip('/')}/api/ligands/upload",
            files={"files": (sdf_path.name, handle, "application/octet-stream")},
            timeout=30,
        )
    assert resp.status_code == 200, f"Ligand upload failed: {resp.status_code} {resp.text[:300]}"
    payload = resp.json()
    saved = payload.get("saved") or []
    if saved:
        return str(saved[0])
    listed = payload.get("ligands") or []
    assert listed, f"Upload response does not include ligand name: {payload}"
    return str(listed[0])


def test_mode_switch_roundtrip(server_ready: None, api: ApiClient) -> None:
    original = api.assert_ok(api.get("/api/state"), where="GET /api/state").get("mode", "Docking")
    try:
        for mode in ("Docking", "Redocking", "Results", "Report"):
            payload = api.assert_ok(api.post("/api/mode", {"mode": mode}), where=f"POST /api/mode {mode}")
            assert str(payload.get("mode") or mode) == mode, f"Mode switch failed for {mode}: {payload}"
    finally:
        api.post("/api/mode", {"mode": original})


def test_receptor_add_select_detail_flow(server_ready: None, api: ApiClient) -> None:
    clear_loaded_receptors(api)
    api.assert_ok(api.post("/api/receptors/add", {"pdb_ids": "6CM4"}), where="POST /api/receptors/add")

    summary = api.assert_ok(api.get("/api/receptors/summary"), where="GET /api/receptors/summary")
    rows = list(summary.get("summary") or [])
    receptor = next((r for r in rows if str(r.get("pdb_id") or "").upper() == "6CM4"), None)
    assert receptor is not None, f"6CM4 not present in receptor summary: {rows}"

    api.assert_ok(api.post("/api/receptors/select", {"pdb_id": "6CM4"}), where="POST /api/receptors/select")
    detail = api.assert_ok(api.get("/api/receptors/6CM4"), where="GET /api/receptors/6CM4")
    assert str(detail.get("pdb_text") or "").strip(), "receptor detail returned empty pdb_text."
    assert isinstance(detail.get("chains"), list), "receptor detail chains must be a list."

    api.post("/api/receptors/remove", {"pdb_id": "6CM4"})


def test_queue_build_contract_with_uploaded_ligand(
    server_ready: None, api: ApiClient, test_cfg, tmp_path: Path
) -> None:
    stamp = int(time.time() * 1000)
    ligand_name = ""
    out_root_name = f"api_contract_{stamp}"
    try:
        clear_queue(api)
        clear_loaded_receptors(api)
        api.post("/api/ligands/active/clear", {})
        api.assert_ok(api.post("/api/receptors/add", {"pdb_ids": "6CM4"}), where="POST /api/receptors/add")

        ligand_name = _upload_temp_ligand(test_cfg.base_url, tmp_path, f"api_flow_{stamp}")
        active = api.assert_ok(
            api.post("/api/ligands/active/add", {"names": [ligand_name]}),
            where="POST /api/ligands/active/add",
        )
        assert ligand_name in set(active.get("active_ligands") or []), (
            f"Ligand not active after add: {active}"
        )

        api.assert_ok(
            api.post("/api/ligands/select", {"pdb_id": "6CM4", "chain": "all", "ligand": ligand_name}),
            where="POST /api/ligands/select",
        )

        build = api.assert_ok(
            api.post(
                "/api/queue/build",
                {
                    "run_count": 1,
                    "padding": 0.0,
                    "out_root_name": out_root_name,
                    "out_root_path": "data/dock",
                    "selection_map": {"6CM4": {"chain": "all", "ligand_resname": ligand_name}},
                    "grid_data": {"6CM4": {"cx": 0.0, "cy": 0.0, "cz": 0.0, "sx": 20.0, "sy": 20.0, "sz": 20.0}},
                    "mode": "Docking",
                    "docking_config": {},
                    "replace_queue": True,
                },
            ),
            where="POST /api/queue/build",
        )
        assert int((build.get("debug") or {}).get("new_jobs_added") or 0) >= 1, (
            f"queue/build did not add jobs: {build}"
        )
        queue_rows = list(build.get("queue") or [])
        assert queue_rows, "queue/build returned empty queue."
        assert any(str(row.get("pdb_id") or "").upper() == "6CM4" for row in queue_rows), (
            f"Queue does not include target receptor: {queue_rows}"
        )
    finally:
        try:
            clear_queue(api)
        except Exception:
            pass
        if ligand_name:
            api.post("/api/ligands/delete", {"name": ligand_name})
        api.post("/api/ligands/active/clear", {})
        api.post("/api/receptors/remove", {"pdb_id": "6CM4"})
        shutil.rmtree(test_cfg.dock_dir / out_root_name, ignore_errors=True)


def test_popup_delete_ligand_removes_db_entry_and_state(
    server_ready: None, api: ApiClient, test_cfg, tmp_path: Path
) -> None:
    stamp = int(time.time() * 1000)
    ligand_name = ""
    try:
        api.post("/api/ligands/active/clear", {})
        ligand_name = _upload_temp_ligand(test_cfg.base_url, tmp_path, f"api_popup_delete_{stamp}")
        active = api.assert_ok(
            api.post("/api/ligands/active/add", {"names": [ligand_name]}),
            where="POST /api/ligands/active/add",
        )
        assert ligand_name in set(active.get("active_ligands") or []), active

        deleted = api.assert_ok(
            api.post("/ligand-3d/api/ligands/delete", {"name": ligand_name}),
            where="POST /ligand-3d/api/ligands/delete",
        )
        assert str(deleted.get("deleted") or "") == ligand_name, deleted
        assert ligand_name not in set(deleted.get("ligands") or []), deleted

        active_after = api.assert_ok(api.get("/api/ligands/active"), where="GET /api/ligands/active")
        assert ligand_name not in set(active_after.get("active_ligands") or []), active_after
    finally:
        if ligand_name:
            api.post("/api/ligands/delete", {"name": ligand_name})
        api.post("/api/ligands/active/clear", {})
