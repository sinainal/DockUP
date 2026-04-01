from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
import requests

pytest.importorskip("playwright.sync_api", reason="Playwright is optional for browser regression tests.")

from playwright.sync_api import Browser, Page, Playwright, sync_playwright

from tests._support.api_client import ApiClient


pytestmark = [pytest.mark.browser]


def _artifacts_root(test_cfg) -> Path:
    root = test_cfg.repo_root / "output" / "playwright" / "browser_state_regressions"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(scope="session")
def playwright_instance() -> Playwright:
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright_instance: Playwright) -> Browser:
    browser = playwright_instance.chromium.launch(headless=True)
    yield browser
    browser.close()


@pytest.fixture()
def page(browser: Browser) -> Page:
    page = browser.new_page(viewport={"width": 1600, "height": 1200})
    yield page
    page.close()


def _snap(page: Page, test_cfg, name: str) -> Path:
    path = _artifacts_root(test_cfg) / name
    page.screenshot(path=str(path), full_page=True)
    return path


def _queue_rows(api: ApiClient) -> list[dict[str, object]]:
    state = api.assert_ok(api.get("/api/state"), where="GET /api/state")
    return list(state.get("queue") or [])


def _clear_queue(api: ApiClient) -> None:
    rows = _queue_rows(api)
    batch_ids = sorted(
        {
            int(row["batch_id"])
            for row in rows
            if isinstance(row, dict) and row.get("batch_id") is not None
        }
    )
    for batch_id in batch_ids:
        api.post("/api/queue/remove_batch", {"batch_id": batch_id})


def _clear_loaded_receptors(api: ApiClient) -> None:
    summary = api.assert_ok(api.get("/api/receptors/summary"), where="GET /api/receptors/summary")
    for row in list(summary.get("summary") or []):
        pdb_id = str(row.get("pdb_id") or "").strip()
        if pdb_id:
            api.post("/api/receptors/remove", {"pdb_id": pdb_id})


def _upload_temp_ligand(base_url: str, stem: str) -> str:
    sdf_bytes = f"{stem}\n  Ketcher\n\n  0  0  0     0  0            999 V2000\nM  END\n$$$$\n".encode("utf-8")
    resp = requests.post(
        f"{base_url.rstrip('/')}/api/ligands/upload",
        files={"files": (f"{stem}.sdf", sdf_bytes, "application/octet-stream")},
        timeout=30,
    )
    assert resp.status_code == 200, f"Ligand upload failed: {resp.status_code} {resp.text[:300]}"
    payload = resp.json()
    saved = payload.get("saved") or []
    assert saved, f"Upload response does not include saved ligand name: {payload}"
    return str(saved[0])


def _remove_dock_out_roots(test_cfg, *names: str) -> None:
    for raw_name in names:
        name = str(raw_name or "").strip()
        if not name:
            continue
        shutil.rmtree(test_cfg.dock_dir / name, ignore_errors=True)


def _prepare_two_queue_batches(api: ApiClient, test_cfg, *, stamp: int) -> tuple[str, str]:
    receptor_id = "6CM4"
    ligand_name = _upload_temp_ligand(test_cfg.base_url, f"browser_queue_{stamp}")
    api.assert_ok(api.post("/api/receptors/add", {"pdb_ids": receptor_id}), where="POST /api/receptors/add")
    ligands = api.assert_ok(api.get(f"/api/receptors/{receptor_id}/ligands"), where="GET /api/receptors/{id}/ligands")
    rows = list(ligands.get("rows") or [])
    assert rows, "Expected at least one native ligand row for 6CM4."
    chain = str(rows[0].get("chain") or "all").strip() or "all"
    api.assert_ok(api.post("/api/ligands/active/add", {"names": [ligand_name]}), where="POST /api/ligands/active/add")
    api.assert_ok(
        api.post("/api/ligands/select", {"pdb_id": receptor_id, "chain": chain, "ligand": ligand_name}),
        where="POST /api/ligands/select",
    )

    grid = {"cx": 0.0, "cy": 0.0, "cz": 0.0, "sx": 20.0, "sy": 20.0, "sz": 20.0}
    common = {
        "run_count": 1,
        "padding": 0.0,
        "out_root_path": "data/dock",
        "selection_map": {receptor_id: {"chain": chain, "ligand_resname": ligand_name}},
        "grid_data": {receptor_id: grid},
        "mode": "Docking",
        "docking_config": {},
    }
    api.assert_ok(api.post("/api/queue/build", {**common, "out_root_name": "qa_refresh_bug"}), where="build queue a")
    api.assert_ok(api.post("/api/queue/build", {**common, "out_root_name": "qb_refresh_bug"}), where="build queue b")
    return receptor_id, ligand_name


def _cleanup_queue_seed(api: ApiClient, test_cfg, receptor_id: str, ligand_name: str) -> None:
    try:
        _clear_queue(api)
    except Exception:
        pass
    try:
        api.post("/api/ligands/delete", {"name": ligand_name})
    except Exception:
        pass
    try:
        api.post("/api/ligands/active/clear", {})
    except Exception:
        pass
    try:
        api.post("/api/receptors/remove", {"pdb_id": receptor_id})
    except Exception:
        pass
    _remove_dock_out_roots(test_cfg, "qa_refresh_bug", "qb_refresh_bug", "q_clear_selection_new")


def _prepare_distinct_receptor_batches(api: ApiClient, test_cfg, *, stamp: int) -> tuple[str, str]:
    api.assert_ok(api.post("/api/receptors/add", {"pdb_ids": "6CM4,3PBL"}), where="POST /api/receptors/add")
    lig_6 = api.assert_ok(api.get("/api/receptors/6CM4/ligands"), where="GET /api/receptors/6CM4/ligands")
    lig_3 = api.assert_ok(api.get("/api/receptors/3PBL/ligands"), where="GET /api/receptors/3PBL/ligands")
    row_6 = list(lig_6.get("rows") or [])[0]
    row_3 = list(lig_3.get("rows") or [])[0]
    assert row_6, "Expected a native ligand for 6CM4."
    assert row_3, "Expected a native ligand for 3PBL."
    lig_name_6 = _upload_temp_ligand(test_cfg.base_url, f"browser_distinct_6_{stamp}")
    lig_name_3 = _upload_temp_ligand(test_cfg.base_url, f"browser_distinct_3_{stamp}")
    api.assert_ok(
        api.post("/api/ligands/active/add", {"names": [lig_name_6, lig_name_3]}),
        where="POST /api/ligands/active/add",
    )
    api.assert_ok(
        api.post("/api/ligands/select", {"pdb_id": "6CM4", "chain": row_6["chain"], "ligand": lig_name_6}),
        where="select ligand for 6CM4",
    )
    api.assert_ok(
        api.post("/api/ligands/select", {"pdb_id": "3PBL", "chain": row_3["chain"], "ligand": lig_name_3}),
        where="select ligand for 3PBL",
    )

    common = {
        "run_count": 1,
        "padding": 0.0,
        "out_root_path": "data/dock",
        "mode": "Docking",
        "docking_config": {},
    }
    api.assert_ok(
        api.post(
            "/api/queue/build",
            {
                **common,
                "out_root_name": "q6_only",
                "selection_map": {
                    "6CM4": {"chain": row_6["chain"], "ligand_resname": lig_name_6},
                },
                "grid_data": {"6CM4": {"cx": 1.0, "cy": 2.0, "cz": 3.0, "sx": 20.0, "sy": 20.0, "sz": 20.0}},
            },
        ),
        where="build q6_only",
    )
    api.assert_ok(
        api.post(
            "/api/queue/build",
            {
                **common,
                "out_root_name": "q3_only",
                "selection_map": {
                    "3PBL": {"chain": row_3["chain"], "ligand_resname": lig_name_3},
                },
                "grid_data": {"3PBL": {"cx": 4.0, "cy": 5.0, "cz": 6.0, "sx": 22.0, "sy": 22.0, "sz": 22.0}},
            },
        ),
        where="build q3_only",
    )
    return "6CM4", "3PBL"


def _cleanup_receptors(api: ApiClient, test_cfg, receptor_ids: list[str]) -> None:
    try:
        _clear_queue(api)
    except Exception:
        pass
    try:
        api.post("/api/ligands/active/clear", {})
    except Exception:
        pass
    for receptor_id in receptor_ids:
        try:
            api.post("/api/receptors/remove", {"pdb_id": receptor_id})
        except Exception:
            pass
    _remove_dock_out_roots(test_cfg, "q3_only", "q6_only", "q_hidden_leak")


def _queue_batch_card(page: Page, batch_label: str):
    return page.locator("#queueTable > div").filter(has=page.get_by_text(batch_label, exact=False)).first


def _select_queue_batch(page: Page, batch_label: str) -> None:
    card = _queue_batch_card(page, batch_label)
    card.locator(":scope > div").first.click(force=True)


def test_results_dock_folder_filter_persists_after_reload(
    server_ready: None, api: ApiClient, test_cfg, page: Page
) -> None:
    page.goto(test_cfg.base_url, wait_until="networkidle")
    page.get_by_role("button", name="Results").click()
    select = page.locator("#resultsDockFolderSelect")
    options = [option.text_content().strip() for option in select.locator("option").all()]
    target = next(option for option in options if option not in {"All dock folders"})
    select.select_option(label=target)
    page.wait_for_timeout(400)
    before = select.input_value()
    _snap(page, test_cfg, "bug_results_filter_before_reload.png")

    page.reload(wait_until="networkidle")
    page.get_by_role("button", name="Results").click()
    after = page.locator("#resultsDockFolderSelect").input_value()
    _snap(page, test_cfg, "bug_results_filter_after_reload.png")

    assert after == before, (
        f"Results dock-folder filter should persist after reload. "
        f"Expected {before!r}, got {after!r}. Screenshot: output/playwright/browser_state_regressions/bug_results_filter_after_reload.png"
    )


def test_results_root_path_persists_after_reload(
    server_ready: None, api: ApiClient, test_cfg, page: Page
) -> None:
    page.goto(test_cfg.base_url, wait_until="networkidle")
    page.get_by_role("button", name="Results").click()

    page.evaluate(
        """() => {
          const el = document.getElementById('resultsRootPath');
          el.value = 'data/dock/dimer_full';
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
        }"""
    )
    page.wait_for_timeout(700)
    before = page.locator("#resultsRootPath").input_value()
    _snap(page, test_cfg, "bug_results_root_before_reload.png")

    page.reload(wait_until="networkidle")
    page.get_by_role("button", name="Results").click()
    after = page.locator("#resultsRootPath").input_value()
    _snap(page, test_cfg, "bug_results_root_after_reload.png")

    assert after == before, (
        f"Results root path should persist after reload. "
        f"Expected {before!r}, got {after!r}. Screenshot: output/playwright/browser_state_regressions/bug_results_root_after_reload.png"
    )


def test_queue_selection_persists_after_reload(
    server_ready: None, api: ApiClient, test_cfg, page: Page
) -> None:
    stamp = int(time.time() * 1000)
    _clear_queue(api)
    _clear_loaded_receptors(api)
    api.post("/api/ligands/active/clear", {})
    receptor_id = ""
    ligand_name = ""
    try:
        receptor_id, ligand_name = _prepare_two_queue_batches(api, test_cfg, stamp=stamp)

        page.goto(test_cfg.base_url, wait_until="networkidle")
        page.get_by_role("button", name="Docking", exact=True).click()
        _select_queue_batch(page, "qb_refresh_bug")
        page.wait_for_timeout(500)
        before_status = page.locator("#queueEditorStatus").text_content() or ""
        _snap(page, test_cfg, "bug_queue_context_before_reload.png")

        page.reload(wait_until="networkidle")
        page.get_by_role("button", name="Docking", exact=True).click()
        page.wait_for_timeout(800)
        after_status = page.locator("#queueEditorStatus").text_content() or ""
        _snap(page, test_cfg, "bug_queue_context_after_reload_and_build.png")

        assert "Selected batch #" in before_status, "Sanity check failed: selected batch state did not activate."
        assert "Selected batch #" in after_status and "qb_refresh_bug" not in after_status, (
            f"Refreshing should preserve the selected queue batch context for Run queue. "
            f"Got status {after_status!r}. "
            f"Screenshot: output/playwright/browser_state_regressions/bug_queue_context_after_reload_and_build.png"
        )
        assert page.locator("#clearQueueSelection").is_visible(), "Clear Selection should remain visible after reload."
    finally:
        if receptor_id or ligand_name:
            _cleanup_queue_seed(api, test_cfg, receptor_id, ligand_name)


def test_deleting_selected_batch_clears_selected_queue_context(
    server_ready: None, api: ApiClient, test_cfg, page: Page
) -> None:
    stamp = int(time.time() * 1000)
    _clear_queue(api)
    _clear_loaded_receptors(api)
    api.post("/api/ligands/active/clear", {})
    receptor_id = ""
    ligand_name = ""
    try:
        receptor_id, ligand_name = _prepare_two_queue_batches(api, test_cfg, stamp=stamp)
        page.goto(test_cfg.base_url, wait_until="networkidle")
        page.get_by_role("button", name="Docking", exact=True).click()
        target_card = _queue_batch_card(page, "qb_refresh_bug")
        _select_queue_batch(page, "qb_refresh_bug")
        page.wait_for_timeout(500)
        before_status = page.locator("#queueEditorStatus").text_content() or ""
        _snap(page, test_cfg, "bug_delete_selected_batch_before.png")

        page.once("dialog", lambda dialog: dialog.accept())
        target_card.get_by_role("button", name="Delete Batch").click()
        page.wait_for_timeout(1000)
        after_status = page.locator("#queueEditorStatus").text_content() or ""
        _snap(page, test_cfg, "bug_delete_selected_batch_after.png")

        assert "Selected batch #" in before_status, "Sanity check failed: batch selection status was not active."
        assert page.locator("#clearQueueSelection").is_hidden(), "Clear Selection should be hidden after deleting the selected batch."
        assert "New queue builds append as separate batches." in after_status, (
            f"Deleting the selected batch should clear the active queue selection. "
            f"Got status {after_status!r}. "
            f"Screenshot: output/playwright/browser_state_regressions/bug_delete_selected_batch_after.png"
        )
        assert page.locator("#queueTable").get_by_text("qb_refresh_bug", exact=False).count() == 0, (
            "Deleted batch should disappear from queue table."
        )
    finally:
        if receptor_id or ligand_name:
            _cleanup_queue_seed(api, test_cfg, receptor_id, ligand_name)


def test_clear_selection_returns_queue_builder_to_append_mode(
    server_ready: None, api: ApiClient, test_cfg, page: Page
) -> None:
    stamp = int(time.time() * 1000)
    _clear_queue(api)
    _clear_loaded_receptors(api)
    api.post("/api/ligands/active/clear", {})
    receptor_id = ""
    ligand_name = ""
    try:
        receptor_id, ligand_name = _prepare_two_queue_batches(api, test_cfg, stamp=stamp)
        page.goto(test_cfg.base_url, wait_until="networkidle")
        page.get_by_role("button", name="Docking", exact=True).click()
        initial_batches = page.locator("#queueTable").get_by_text("Batch #").count()
        page.locator("#outRootName").fill("q_clear_selection_new")
        _select_queue_batch(page, "qb_refresh_bug")
        page.wait_for_timeout(500)
        before_status = page.locator("#queueEditorStatus").text_content() or ""
        _snap(page, test_cfg, "bug_new_queue_before.png")

        page.get_by_role("button", name="Clear Selection").click()
        page.wait_for_timeout(500)
        after_status = page.locator("#queueEditorStatus").text_content() or ""
        page.get_by_role("button", name="Build queue").click()
        page.wait_for_timeout(1000)
        after_batches = page.locator("#queueTable").get_by_text("Batch #").count()
        _snap(page, test_cfg, "bug_new_queue_after.png")

        assert "Selected batch #" in before_status, "Sanity check failed: selected batch state did not activate."
        assert "New queue builds append as separate batches." in after_status, (
            f"Clearing queue selection should return the builder to append mode. "
            f"Got status {after_status!r}. "
            f"Screenshot: output/playwright/browser_state_regressions/bug_new_queue_after.png"
        )
        assert after_batches == initial_batches + 1, (
            f"Build queue after clearing selection should append a new batch. "
            f"Expected {initial_batches + 1} batches, got {after_batches}. "
            f"Screenshot: output/playwright/browser_state_regressions/bug_new_queue_after.png"
        )
        leak_rows = [row for row in _queue_rows(api) if str(row.get("out_root_name") or "") == "q_clear_selection_new"]
        assert leak_rows, "Expected a new queue batch with out_root_name=q_clear_selection_new."
    finally:
        if receptor_id or ligand_name:
            _cleanup_queue_seed(api, test_cfg, receptor_id, ligand_name)


def test_queue_popup_edits_do_not_leak_hidden_receptors_into_new_batch(
    server_ready: None, api: ApiClient, test_cfg, page: Page
) -> None:
    _clear_queue(api)
    _clear_loaded_receptors(api)
    api.post("/api/ligands/active/clear", {})
    try:
        _prepare_distinct_receptor_batches(api, test_cfg, stamp=int(time.time() * 1000))
        api.post("/api/receptors/remove", {"pdb_id": "3PBL"})
        ligands_6 = api.assert_ok(api.get("/api/receptors/6CM4/ligands"), where="GET /api/receptors/6CM4/ligands")
        row_6 = list(ligands_6.get("rows") or [])[0]
        api.assert_ok(api.post("/api/ligands/active/add", {"names": ["Ethylene_monomer_3.sdf"]}), where="add safe active ligand")
        api.assert_ok(
            api.post(
                "/api/ligands/select",
                {"pdb_id": "6CM4", "chain": row_6["chain"], "ligand": "Ethylene_monomer_3.sdf"},
            ),
            where="select safe ligand for 6CM4",
        )
        page.goto(test_cfg.base_url, wait_until="networkidle")
        page.get_by_role("button", name="Docking", exact=True).click()
        page.wait_for_timeout(1200)

        _queue_batch_card(page, "q3_only").get_by_role("button", name="Edit Queue").click()
        page.wait_for_timeout(800)
        page.get_by_role("button", name="Close").click()
        page.wait_for_timeout(300)
        _queue_batch_card(page, "q6_only").get_by_role("button", name="Edit Queue").click()
        page.wait_for_timeout(800)
        assert page.locator("#queueBatchOutputName").input_value() == "q6_only", "Sanity check failed: q6_only was not loaded."
        page.get_by_role("button", name="Close").click()
        page.wait_for_timeout(300)
        _snap(page, test_cfg, "bug_hidden_receptor_leak_before.png")

        page.locator("#outRootName").fill("q_hidden_leak")
        page.get_by_role("button", name="Build queue").click()
        page.wait_for_timeout(1000)
        _snap(page, test_cfg, "bug_hidden_receptor_leak_after.png")

        leak_rows = [row for row in _queue_rows(api) if str(row.get("out_root_name") or "") == "q_hidden_leak"]
        leak_ids = sorted(str(row.get("pdb_id") or "").strip().upper() for row in leak_rows if row.get("pdb_id"))
        assert leak_ids == ["6CM4"], (
            f"Queue popup edits should not silently carry hidden receptors into a new main-form batch. "
            f"Expected only ['6CM4'], got {leak_ids}. "
            f"Screenshot: output/playwright/browser_state_regressions/bug_hidden_receptor_leak_after.png"
        )
    finally:
        _cleanup_receptors(api, test_cfg, ["6CM4", "3PBL"])
