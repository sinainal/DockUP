from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
import requests

from tests._support.api_client import ApiClient
from tests._support.e2e_flow import (
    choose_native_ligand_row,
    clear_loaded_receptors,
    clear_queue,
    compute_grid_around_native_ligand,
    wait_run_finished,
)


pytestmark = [pytest.mark.api]


def _upload_temp_ligand(base_url: str, tmp_path: Path, stem: str) -> str:
    sdf_path = tmp_path / f"{stem}.sdf"
    sdf_path.write_text("\n  Ketcher\n\n  0  0  0     0  0            999 V2000\nM  END\n$$$$\n", encoding="utf-8")
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


def _prepare_receptor_grid_and_chain(api: ApiClient, receptor_id: str) -> tuple[str, dict[str, float]]:
    api.assert_ok(api.post("/api/receptors/add", {"pdb_ids": receptor_id}), where="POST /api/receptors/add")
    lig_rows_resp = api.assert_ok(
        api.get(f"/api/receptors/{receptor_id}/ligands"),
        where="GET /api/receptors/{id}/ligands",
    )
    ligand_rows = list(lig_rows_resp.get("rows") or [])
    native_ligand = choose_native_ligand_row(ligand_rows)
    native_label = str(native_ligand.get("ligand") or "").strip()
    native_parts = [p for p in native_label.split() if p]
    assert len(native_parts) >= 2, f"Unexpected native ligand label: {native_label}"
    native_resname = native_parts[0]
    native_resno = native_parts[1]
    native_chain = str(native_ligand.get("chain") or "all").strip() or "all"

    detail = api.assert_ok(api.get(f"/api/receptors/{receptor_id}"), where="GET /api/receptors/{id}")
    pdb_text = str(detail.get("pdb_text") or "").strip()
    assert pdb_text, "Receptor detail returned empty pdb_text."
    grid = compute_grid_around_native_ligand(
        pdb_text,
        native_resname,
        native_resno,
        native_chain,
        cutoff=5.0,
        fixed_size=20.0,
    )
    return native_chain, grid


def _build_single_job_queue(
    api: ApiClient,
    *,
    receptor_id: str,
    chain: str,
    ligand_name: str,
    grid: dict[str, float],
    out_root_path: str,
    out_root_name: str,
    replace_queue: bool = False,
    update_batch_id: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "run_count": 1,
        "padding": 0.0,
        "out_root_name": out_root_name,
        "out_root_path": out_root_path,
        "selection_map": {receptor_id: {"chain": chain, "ligand_resname": ligand_name}},
        "grid_data": {receptor_id: grid},
        "mode": "Docking",
        "docking_config": {},
        "replace_queue": replace_queue,
    }
    if update_batch_id is not None:
        payload["update_batch_id"] = update_batch_id
    return api.assert_ok(api.post("/api/queue/build", payload), where="POST /api/queue/build")


def test_queue_build_appends_new_batches_and_updates_selected_batch(
    server_ready: None, api: ApiClient, test_cfg, tmp_path: Path
) -> None:
    stamp = int(time.time() * 1000)
    ligand_name = ""
    receptor_id = "6CM4"
    try:
        clear_queue(api)
        clear_loaded_receptors(api)
        api.post("/api/ligands/active/clear", {})

        chain, grid = _prepare_receptor_grid_and_chain(api, receptor_id)
        ligand_name = _upload_temp_ligand(test_cfg.base_url, tmp_path, f"queue_batch_{stamp}")
        active = api.assert_ok(
            api.post("/api/ligands/active/add", {"names": [ligand_name]}),
            where="POST /api/ligands/active/add",
        )
        assert ligand_name in set(active.get("active_ligands") or []), f"Ligand not active: {active}"
        api.assert_ok(
            api.post("/api/ligands/select", {"pdb_id": receptor_id, "chain": chain, "ligand": ligand_name}),
            where="POST /api/ligands/select",
        )

        first = _build_single_job_queue(
            api,
            receptor_id=receptor_id,
            chain=chain,
            ligand_name=ligand_name,
            grid=grid,
            out_root_path="data/dock/api_queue_batches",
            out_root_name=f"batch_one_{stamp}",
        )
        first_rows = list(first.get("queue") or [])
        assert len(first_rows) == 1, f"Expected one queue row after first build: {first_rows}"
        first_batch_id = int(first_rows[0]["batch_id"])

        time.sleep(0.01)
        second = _build_single_job_queue(
            api,
            receptor_id=receptor_id,
            chain=chain,
            ligand_name=ligand_name,
            grid=grid,
            out_root_path="data/dock/api_queue_batches",
            out_root_name=f"batch_two_{stamp}",
        )
        second_rows = list(second.get("queue") or [])
        assert len(second_rows) == 2, f"Expected two queue rows after append: {second_rows}"
        batch_ids = {int(row["batch_id"]) for row in second_rows}
        assert len(batch_ids) == 2, f"Expected distinct batch ids after append: {second_rows}"

        by_batch = {int(row["batch_id"]): row for row in second_rows}
        assert by_batch[first_batch_id]["out_root_name"] == f"batch_one_{stamp}"

        second_batch_id = next(batch_id for batch_id in batch_ids if batch_id != first_batch_id)
        updated = _build_single_job_queue(
            api,
            receptor_id=receptor_id,
            chain=chain,
            ligand_name=ligand_name,
            grid=grid,
            out_root_path="data/dock/api_queue_batches",
            out_root_name=f"batch_one_edited_{stamp}",
            update_batch_id=first_batch_id,
        )
        updated_rows = list(updated.get("queue") or [])
        assert len(updated_rows) == 2, f"Updating a batch should not drop other batches: {updated_rows}"
        updated_by_batch = {int(row["batch_id"]): row for row in updated_rows}
        assert updated_by_batch[first_batch_id]["out_root_name"] == f"batch_one_edited_{stamp}"
        assert updated_by_batch[second_batch_id]["out_root_name"] == f"batch_two_{stamp}"
    finally:
        try:
            clear_queue(api)
        except Exception:
            pass
        if ligand_name:
            api.post("/api/ligands/delete", {"name": ligand_name})
        api.post("/api/ligands/active/clear", {})
        api.post("/api/receptors/remove", {"pdb_id": receptor_id})
        shutil.rmtree(test_cfg.dock_dir / "api_queue_batches", ignore_errors=True)


@pytest.mark.slow
def test_run_start_selected_batch_appends_runs_without_overwrite(
    server_ready: None, api: ApiClient, test_cfg, tmp_path: Path
) -> None:
    stamp = int(time.time() * 1000)
    ligand_name = ""
    receptor_id = "6CM4"
    shared_parent = "data/dock/api_queue_shared_runs"
    shared_name = f"shared_{stamp}"
    try:
        clear_queue(api)
        clear_loaded_receptors(api)
        api.post("/api/ligands/active/clear", {})

        chain, grid = _prepare_receptor_grid_and_chain(api, receptor_id)
        ligand_name = _upload_temp_ligand(test_cfg.base_url, tmp_path, f"queue_runs_{stamp}")
        api.assert_ok(
            api.post("/api/ligands/active/add", {"names": [ligand_name]}),
            where="POST /api/ligands/active/add",
        )
        api.assert_ok(
            api.post("/api/ligands/select", {"pdb_id": receptor_id, "chain": chain, "ligand": ligand_name}),
            where="POST /api/ligands/select",
        )

        first = _build_single_job_queue(
            api,
            receptor_id=receptor_id,
            chain=chain,
            ligand_name=ligand_name,
            grid=grid,
            out_root_path=shared_parent,
            out_root_name=shared_name,
        )
        first_batch_id = int(list(first.get("queue") or [])[0]["batch_id"])

        time.sleep(0.01)
        second = _build_single_job_queue(
            api,
            receptor_id=receptor_id,
            chain=chain,
            ligand_name=ligand_name,
            grid=grid,
            out_root_path=shared_parent,
            out_root_name=shared_name,
        )
        second_rows = list(second.get("queue") or [])
        second_batch_id = next(
            int(row["batch_id"]) for row in second_rows if int(row["batch_id"]) != first_batch_id
        )

        started_one = api.assert_ok(
            api.post("/api/run/start", {"batch_id": first_batch_id, "is_test_mode": True}, timeout=60),
            where="POST /api/run/start batch 1",
        )
        assert str(started_one.get("status") or "") in {"running", "done"}, started_one
        final_one = wait_run_finished(api, timeout_sec=60, interval_sec=0.5)
        assert str(final_one.get("status") or "") == "done", final_one
        assert int(final_one.get("returncode") or 0) == 0, final_one

        started_two = api.assert_ok(
            api.post("/api/run/start", {"batch_id": second_batch_id, "is_test_mode": True}, timeout=60),
            where="POST /api/run/start batch 2",
        )
        assert str(started_two.get("status") or "") in {"running", "done"}, started_two
        final_two = wait_run_finished(api, timeout_sec=60, interval_sec=0.5)
        assert str(final_two.get("status") or "") == "done", final_two
        assert int(final_two.get("returncode") or 0) == 0, final_two

        out_root = Path(str(final_two.get("out_root") or "")).resolve()
        assert out_root.exists(), f"Missing out_root after runs: {out_root}"
        result_paths = sorted(out_root.rglob("results.json"))
        assert len(result_paths) == 2, f"Expected two result files under shared out_root: {result_paths}"
        run_names = sorted(path.parent.name for path in result_paths)
        assert run_names == ["run1", "run2"], f"Expected run1 and run2 without overwrite: {run_names}"
    finally:
        try:
            clear_queue(api)
        except Exception:
            pass
        if ligand_name:
            api.post("/api/ligands/delete", {"name": ligand_name})
        api.post("/api/ligands/active/clear", {})
        api.post("/api/receptors/remove", {"pdb_id": receptor_id})
        shutil.rmtree(test_cfg.dock_dir / "api_queue_shared_runs", ignore_errors=True)
