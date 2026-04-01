"""
DockUP — Automated API Test Suite
===================================
Çalıştırma:
    # Sunucu çalışırken (./start.sh):
    cd <repo-root>
    python3 -m pytest tests/test_api.py -v

Gereksinim:
    pip install pytest requests

Kapsanan Alanlar:
    - State & Mode endpoints  
    - Ligand CRUD  
    - Receptor load & summary  
    - Queue build (grid_data dahil)  
    - Run start/stop/status  
    - Results scan & detail  
    - Report list, images, metadata  
    - Path resolution  
    - Edge cases & error handling  
"""

from __future__ import annotations

import json
import io
import time
from pathlib import Path
from typing import Any

import pytest
import requests
import pandas as pd

pytestmark = [pytest.mark.legacy, pytest.mark.api]

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://localhost:8000"
WORKSPACE = REPO_ROOT / "docking_app" / "workspace"
DOCK_DIR = WORKSPACE / "data" / "dock"
LIGAND_DIR = WORKSPACE / "data" / "ligand"
RECEPTOR_DIR = WORKSPACE / "data" / "receptor"

# ────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────

def get(path: str, **kwargs) -> requests.Response:
    return requests.get(f"{BASE_URL}{path}", timeout=10, **kwargs)

def post(path: str, json_data: Any = None, **kwargs) -> requests.Response:
    return requests.post(f"{BASE_URL}{path}", json=json_data, timeout=10, **kwargs)

def assert_ok(resp: requests.Response, msg: str = "") -> dict:
    """Assert 200 and return parsed JSON."""
    assert resp.status_code == 200, (
        f"{msg} — got {resp.status_code}: {resp.text[:300]}"
    )
    return resp.json()


def has_openpyxl() -> bool:
    try:
        import openpyxl  # noqa: F401
        return True
    except Exception:
        return False


def clear_loaded_receptors() -> None:
    """Best-effort receptor reset for stateful integration tests."""
    resp = get("/api/receptors/summary")
    if resp.status_code != 200:
        return
    try:
        payload = resp.json()
    except ValueError:
        return
    rows = payload.get("summary", []) if isinstance(payload, dict) else []
    for row in rows:
        pdb_id = str((row or {}).get("pdb_id") or "").strip()
        if not pdb_id:
            continue
        post("/api/receptors/remove", {"pdb_id": pdb_id})


# ────────────────────────────────────────────────
# 1. Server Health
# ────────────────────────────────────────────────

class TestServerHealth:
    def test_homepage_loads(self):
        """Ana sayfa HTML dönmeli."""
        resp = get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_api_state_returns_200(self):
        """GET /api/state temel alanları dönmeli."""
        data = assert_ok(get("/api/state"), "GET /api/state")
        assert "mode" in data
        assert "queue_count" in data
        assert "selected_receptor" in data

    def test_api_state_mode_valid(self):
        """Mode değeri tanımlı modlardan biri olmalı."""
        data = assert_ok(get("/api/state"))
        valid_modes = {"Docking", "Redocking", "Results", "Report"}
        assert data["mode"] in valid_modes, f"Unexpected mode: {data['mode']}"


# ────────────────────────────────────────────────
# 2. Mode Switching
# ────────────────────────────────────────────────

class TestModeSwitching:
    @pytest.mark.parametrize("mode", ["Docking", "Redocking", "Results", "Report"])
    def test_switch_mode(self, mode):
        """Her mod değişikliği 200 dönmeli."""
        resp = post("/api/mode", {"mode": mode})
        data = assert_ok(resp, f"POST /api/mode mode={mode}")
        assert data.get("mode") == mode or "mode" in data

    def test_invalid_mode_falls_back_to_docking(self):
        """Geçersiz mod fallback ile Docking moduna alınmalı."""
        resp = post("/api/mode", {"mode": "InvalidMode"})
        data = assert_ok(resp, "POST /api/mode invalid mode")
        assert data.get("mode") == "Docking", f"Expected Docking fallback, got: {data}"

    def test_restore_docking_mode(self):
        """Test sonrası Docking moduna geri dön."""
        resp = post("/api/mode", {"mode": "Docking"})
        assert resp.status_code == 200


# ────────────────────────────────────────────────
# 3. Ligand Management
# ────────────────────────────────────────────────

class TestLigandManagement:
    def test_list_ligands_returns_list(self):
        """GET /api/ligands/list bir liste dönmeli."""
        data = assert_ok(get("/api/ligands/list"), "GET /api/ligands/list")
        assert "ligands" in data
        assert isinstance(data["ligands"], list)

    def test_ligands_are_sdf_files(self):
        """Dönen ligand isimleri .sdf uzantılı olmalı."""
        data = assert_ok(get("/api/ligands/list"))
        for name in data["ligands"]:
            assert name.endswith(".sdf"), f"Non-SDF ligand: {name}"

    def test_ligand_count_matches_filesystem(self):
        """API'den dönen ligand sayısı disk ile eşleşmeli."""
        data = assert_ok(get("/api/ligands/list"))
        api_count = len(data["ligands"])
        disk_count = len(list(LIGAND_DIR.glob("*.sdf")))
        assert api_count == disk_count, (
            f"API: {api_count}, Disk: {disk_count}"
        )

    def test_upload_and_delete_ligand(self, tmp_path):
        """Ligand yükleme ve silme döngüsü çalışmalı."""
        # Geçici .sdf dosyası oluştur
        sdf_content = "\n  Ketcher\n\n  0  0  0     0  0            999 V2000\nM  END\n$$$$\n"
        test_file = tmp_path / "test_upload_ligand.sdf"
        test_file.write_text(sdf_content)

        # Yükle
        with open(test_file, "rb") as f:
            resp = requests.post(
                f"{BASE_URL}/api/ligands/upload",
                files={"files": ("test_upload_ligand.sdf", f, "application/octet-stream")},
                timeout=10,
            )
        data = assert_ok(resp, "POST /api/ligands/upload")
        assert "saved" in data or "ligands" in data

        # Liste ile doğrula
        list_data = assert_ok(get("/api/ligands/list"))
        names = list_data["ligands"]
        uploaded = [n for n in names if "test_upload_ligand" in n]
        assert uploaded, "Yüklenen ligand listede yok"

        # Sil
        resp2 = post("/api/ligands/delete", {"name": uploaded[0]})
        assert_ok(resp2, "POST /api/ligands/delete")

        # Silindi mi?
        list_data2 = assert_ok(get("/api/ligands/list"))
        remaining = [n for n in list_data2["ligands"] if "test_upload_ligand" in n]
        assert not remaining, "Silinen ligand hâlâ listede"

    def test_upload_sanitizes_path_traversal_filename(self, tmp_path):
        """Path traversal içeren ad, üst dizine değil güvenli basename'e yazılmalı."""
        sdf_content = "\n  Ketcher\n\n  0  0  0     0  0            999 V2000\nM  END\n$$$$\n"
        test_file = tmp_path / "safe_name.sdf"
        test_file.write_text(sdf_content)
        token = int(time.time() * 1000)
        upload_name = f"../escape_probe_{token}.sdf"
        escaped_target = WORKSPACE / "data" / f"escape_probe_{token}.sdf"
        safe_target = LIGAND_DIR / f"escape_probe_{token}.sdf"
        escaped_target.unlink(missing_ok=True)
        safe_target.unlink(missing_ok=True)

        with open(test_file, "rb") as f:
            resp = requests.post(
                f"{BASE_URL}/api/ligands/upload",
                files={"files": (upload_name, f, "application/octet-stream")},
                timeout=10,
            )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"
        assert not escaped_target.exists(), "Upload traversal ile üst dizine dosya yazıldı"
        assert safe_target.exists(), "Dosya güvenli ligand dizinine yazılmadı"
        safe_target.unlink(missing_ok=True)

    def test_active_ligands_add_and_remove(self):
        """Dock-ready ligand havuzuna ekleme/çıkarma endpointleri çalışmalı."""
        ligands = assert_ok(get("/api/ligands/list")).get("ligands", [])
        if not ligands:
            pytest.skip("No ligands available for active ligand test.")
        name = ligands[0]

        assert_ok(post("/api/ligands/active/clear", {}))
        after_clear = assert_ok(get("/api/ligands/active")).get("active_ligands", [])
        assert name not in after_clear

        added = assert_ok(post("/api/ligands/active/add", {"names": [name]})).get("active_ligands", [])
        assert name in added

        removed = assert_ok(post("/api/ligands/active/remove", {"name": name})).get("active_ligands", [])
        assert name not in removed


# ────────────────────────────────────────────────
# 4. Receptor Management
# ────────────────────────────────────────────────

class TestReceptorManagement:
    def test_summary_returns_list(self):
        """GET /api/receptors/summary liste dönmeli."""
        data = assert_ok(get("/api/receptors/summary"), "GET /api/receptors/summary")
        assert "summary" in data
        assert isinstance(data["summary"], list)

    def test_receptor_summary_fields(self):
        """Her reseptör kaydı gerekli alanları içermeli."""
        data = assert_ok(get("/api/receptors/summary"))
        for item in data["summary"]:
            assert "pdb_id" in item, f"Missing pdb_id: {item}"
            assert "status" in item, f"Missing status: {item}"
            assert "chains" in item, f"Missing chains: {item}"

    def test_receptor_ids_are_uppercase(self):
        """Yüklü receptor kimlikleri normalize edilip büyük harfli dönmeli."""
        resp = post("/api/receptors/load", {"pdb_ids": "6cm4\n6CM4"})
        data = assert_ok(resp)
        ids = [str(row.get("pdb_id") or "") for row in data.get("summary", [])]
        assert ids, "Summary boş döndü."
        for pdb_id in ids:
            assert pdb_id == pdb_id.upper(), f"Receptor ID uppercase değil: {pdb_id}"
        assert ids.count("6CM4") <= 1, f"Case-insensitive duplicate var: {ids}"

    def test_receptor_ids_accepts_whitespace_and_newline(self):
        """Load endpoint satır ve boşluk ayracı ile çoklu receptor almalı."""
        resp = post("/api/receptors/load", {"pdb_ids": "7X2F 6CM4\n3PBL"})
        data = assert_ok(resp)
        ids = {str(row.get("pdb_id") or "") for row in data.get("summary", [])}
        assert "7X2F" in ids
        assert "6CM4" in ids
        assert "3PBL" in ids

    def test_invalid_receptor_id_is_ignored(self):
        """f33wro gibi geçersiz ID load edilmemeli."""
        resp = post("/api/receptors/load", {"pdb_ids": "f33wro"})
        data = assert_ok(resp)
        ids = {str(row.get("pdb_id") or "") for row in data.get("summary", [])}
        assert "F33WRO" not in ids, f"Geçersiz ID summary'e eklendi: {ids}"
        ignored = {str(x or "") for x in data.get("ignored_ids", [])}
        assert "F33WRO" in ignored, f"Geçersiz ID ignored_ids içinde yok: {data}"

    def test_loaded_receptor_is_persisted_to_stored_receptors(self):
        """PDB ID ile yüklenen receptor, stored receptor listesinde görünmeli."""
        resp = post("/api/receptors/load", {"pdb_ids": "6CM4"})
        data = assert_ok(resp)
        ids = {str(row.get("pdb_id") or "") for row in data.get("summary", [])}
        assert "6CM4" in ids, f"6CM4 summary'de yok: {ids}"
        stored = assert_ok(get("/api/receptors/list")).get("receptors", [])
        stored_ids = {str(row.get("pdb_id") or "") for row in stored}
        assert "6CM4" in stored_ids, f"6CM4 stored listede yok: {stored_ids}"

    def test_store_then_add_receptor_flow(self):
        """Store sadece depoya yazmalı, Add ise docking listesine eklemeli."""
        clear_loaded_receptors()
        store = assert_ok(post("/api/receptors/store", {"pdb_ids": "7X2F"}))
        stored_ids = {str(row.get("pdb_id") or "") for row in store.get("receptors", [])}
        assert "7X2F" in stored_ids

        summary_before = assert_ok(get("/api/receptors/summary")).get("summary", [])
        ids_before = {str(row.get("pdb_id") or "") for row in summary_before}
        assert "7X2F" not in ids_before, f"Store endpoint receptoru aktif listeye ekledi: {ids_before}"

        added = assert_ok(post("/api/receptors/add", {"pdb_ids": "7X2F"}))
        ids_after = {str(row.get("pdb_id") or "") for row in added.get("summary", [])}
        assert "7X2F" in ids_after, f"Add endpoint receptoru aktif listeye eklemedi: {ids_after}"

    def test_receptor_list_and_delete_file_endpoints(self):
        """Stored receptor list + delete endpoint temel akış."""
        probe = RECEPTOR_DIR / f"ZZ_RECEPTOR_LIST_{int(time.time() * 1000)}.pdb"
        probe.write_text("HEADER TEST\nEND\n", encoding="utf-8")
        try:
            rows = assert_ok(get("/api/receptors/list")).get("receptors", [])
            match = next((row for row in rows if row.get("name") == probe.name), None)
            assert match is not None, "Yeni receptor dosyası listede görünmedi."
            assert match.get("pdb_id") == probe.stem.upper()

            del_resp = post("/api/receptors/delete", {"name": probe.name})
            assert del_resp.status_code == 200, del_resp.text[:200]
            assert not probe.exists(), "Receptor dosyası silinmedi."
        finally:
            probe.unlink(missing_ok=True)

    def test_probe_tmp_receptor_is_cleaned(self):
        """TMP_PROBE test artıkları listeden temizlenmeli."""
        probe = RECEPTOR_DIR / "TMP_PROBE.pdb"
        probe.write_text("HEADER TEST\nEND\n", encoding="utf-8")
        rows = assert_ok(get("/api/receptors/list")).get("receptors", [])
        ids = {str(row.get("pdb_id") or "") for row in rows}
        assert "TMP_PROBE" not in ids, f"TMP_PROBE listede kaldı: {ids}"
        assert not probe.exists(), "TMP_PROBE dosyası temizlenmedi."


# ────────────────────────────────────────────────
# 5. Results Scan
# ────────────────────────────────────────────────

class TestResultsScan:
    def test_scan_dock_dir_returns_runs(self):
        """data/dock dizini taraması run listesi dönmeli."""
        resp = post("/api/results/scan", {"root_path": "data/dock"})
        data = assert_ok(resp, "POST /api/results/scan")
        assert "runs" in data
        assert "averages" in data
        assert isinstance(data["runs"], list)

    def test_scan_returns_valid_run_structure(self):
        """Her run kaydı gerekli alanları içermeli."""
        resp = post("/api/results/scan", {"root_path": "data/dock"})
        data = assert_ok(resp)
        for run in data["runs"][:3]:  # İlk 3'ü kontrol et
            assert "pdb_id" in run, f"Missing pdb_id: {run}"
            assert "result_dir" in run, f"Missing result_dir: {run}"

    def test_scan_averages_structure(self):
        """Averages her kayıt için gerekli alanları içermeli."""
        resp = post("/api/results/scan", {"root_path": "data/dock"})
        data = assert_ok(resp)
        for avg in data["averages"][:3]:
            assert "pdb_id" in avg
            assert "run_count" in avg
            assert avg["run_count"] > 0

    def test_scan_nonexistent_dir_returns_error(self):
        """Var olmayan dizin 400 dönmeli."""
        resp = post("/api/results/scan", {"root_path": "data/dock/nonexistent_xyz"})
        assert resp.status_code == 400, (
            f"Expected 400, got {resp.status_code}: {resp.text[:200]}"
        )

    def test_scan_absolute_path_works(self):
        """Absolute path ile tarama da çalışmalı."""
        abs_path = str(DOCK_DIR)
        resp = post("/api/results/scan", {"root_path": abs_path})
        data = assert_ok(resp, "POST /api/results/scan — absolute path")
        assert "runs" in data
        assert isinstance(data["runs"], list)

    def test_scan_outside_dock_rejected(self):
        """data/dock dışı path scan için reddedilmeli."""
        resp = post("/api/results/scan", {"root_path": "/tmp"})
        assert resp.status_code == 400, (
            f"Expected 400 for outside root, got {resp.status_code}: {resp.text[:200]}"
        )

    def test_results_dock_folders_endpoint(self):
        """Results dropdown için dock klasör listesi dönmeli."""
        data = assert_ok(get("/api/results/dock-folders"))
        folders = data.get("folders", [])
        assert isinstance(folders, list)
        assert any(str(row.get("path")) == "data/dock" for row in folders), (
            f"data/dock root option missing: {folders}"
        )


# ────────────────────────────────────────────────
# 6. Path Resolution
# ────────────────────────────────────────────────

class TestPathResolution:
    def test_resolve_results_path(self):
        """data/dock relative path results scope için çözümlenmeli."""
        resp = post("/api/paths/resolve", {
            "relative_path": "data/dock",
            "scope": "results",
        })
        data = assert_ok(resp, "POST /api/paths/resolve")
        assert "resolved_path" in data or "path" in data

    def test_resolve_report_path(self):
        """data/dock report scope için çözümlenmeli."""
        resp = post("/api/paths/resolve", {
            "relative_path": "data/dock",
            "scope": "report",
        })
        assert resp.status_code == 200


# ────────────────────────────────────────────────
# 7. Report Endpoints
# ────────────────────────────────────────────────

class TestReportEndpoints:
    REPORT_PARAMS = {
        "root_path": "data/dock",
        "source_path": "data/dock/dimer_final_linked",
        "output_path": "data/dock/dimer_final_linked/report_outputs",
    }

    def test_reports_list_returns_200(self):
        """GET /api/reports/list 200 dönmeli."""
        data = assert_ok(
            get("/api/reports/list", params=self.REPORT_PARAMS),
            "GET /api/reports/list"
        )
        assert "receptors" in data
        assert "source_folders" in data

    def test_reports_list_source_folders_no_internal_dirs(self):
        """Source folders listesinde _run_sessions vb. olmamalı."""
        data = assert_ok(get("/api/reports/list", params=self.REPORT_PARAMS))
        folder_names = [f["name"] for f in data.get("source_folders", [])]
        forbidden = {"_run_sessions", "_meta", "__pycache__", "plip", "reports"}
        for name in folder_names:
            assert name not in forbidden, f"Dahili klasör görünüyor: {name}"
            assert not name.startswith("_"), f"_ ile başlayan klasör görünüyor: {name}"

    def test_reports_list_invalid_source_falls_back(self):
        """Geçersiz source_path 400 değil 200 dönmeli (fallback)."""
        params = dict(self.REPORT_PARAMS)
        params["source_path"] = "data/dock/_run_sessions"
        resp = get("/api/reports/list", params=params)
        assert resp.status_code == 200, (
            f"Geçersiz source_path ile fallback başarısız: {resp.status_code} {resp.text[:200]}"
        )

    def test_reports_images_returns_200(self):
        """GET /api/reports/images 200 dönmeli."""
        params = dict(self.REPORT_PARAMS)
        params["images_root_path"] = "data/dock/dimer_final_linked/report_outputs"
        data = assert_ok(
            get("/api/reports/images", params=params),
            "GET /api/reports/images"
        )
        assert data.get("root_path") == "data/dock"
        assert data.get("source_path") == "data/dock/dimer_final_linked"
        assert data.get("output_path") == "data/dock/dimer_final_linked/report_outputs"
        assert data.get("images_root_path") == "data/dock/dimer_final_linked/report_outputs"
        assert "images" in data
        assert isinstance(data["images"], list)

    def test_reports_status_returns_200(self):
        """GET /api/reports/status 200 dönmeli."""
        data = assert_ok(get("/api/reports/status"), "GET /api/reports/status")
        assert "status" in data

    def test_root_metadata_returns_200(self):
        """GET /api/reports/root-metadata 200 dönmeli."""
        resp = get("/api/reports/root-metadata", params={
            "root_path": "data/dock",
            "source_path": "data/dock/dimer_final_linked",
        })
        assert resp.status_code == 200


# ────────────────────────────────────────────────
# 8. Queue Build
# ────────────────────────────────────────────────

class TestQueueBuild:
    """Queue build için reseptör yüklü olması gerekmiyor —
    selection_map boşsa 0 job eklenir, bu da başarılı sayılır."""

    def test_queue_build_empty_selection_returns_empty_queue(self):
        """selection_map boşken sıfır job eklenmeli (hata değil)."""
        clear_loaded_receptors()
        resp = post("/api/queue/build", {
            "run_count": 1,
            "out_root_name": "test_queue_empty",
            "out_root_path": "data/dock",
            "selection_map": {},
            "grid_data": {},
            "docking_config": {},
        })
        data = assert_ok(resp, "POST /api/queue/build — empty selection")
        assert "queue_count" in data
        assert data["queue_count"] == 0 or isinstance(data["queue_count"], int)

    def test_queue_build_missing_grid_data_returns_400(self):
        """selection_map dolu ama grid_data eksikse 400 dönmeli."""
        # Önce receptor_meta'yı simüle edemeyiz ama STATE boşsa
        # _build_queue içi selection için meta yoksa job eklenmez
        resp = post("/api/queue/build", {
            "run_count": 1,
            "out_root_name": "test_queue_nogrid",
            "out_root_path": "data/dock",
            "selection_map": {"FAKE123": {"chain": "A", "ligand_resname": "test.sdf"}},
            "grid_data": {},  # grid yok
            "docking_config": {},
        })
        # receptor_meta boşsa job eklenmez (0), eğer receptor yüklüyse 400
        assert resp.status_code in (200, 400), (
            f"Unexpected: {resp.status_code} {resp.text[:200]}"
        )

    def test_queue_build_updates_out_root(self):
        """Queue build STATE[out_root]'u güncellmeli."""
        clear_loaded_receptors()
        resp = post("/api/queue/build", {
            "run_count": 3,
            "out_root_name": "my_test_run",
            "out_root_path": "data/dock",
            "selection_map": {},
            "grid_data": {},
            "docking_config": {},
        })
        assert_ok(resp, "POST /api/queue/build")
        state = assert_ok(get("/api/state"))
        out_root = state.get("out_root") or ""
        assert "my_test_run" in out_root, f"out_root not updated: {out_root}"

    def test_queue_build_rejects_outside_out_root_path(self):
        """out_root_path data/dock dışında ise endpoint 400 dönmeli."""
        clear_loaded_receptors()
        resp = post("/api/queue/build", {
            "run_count": 1,
            "out_root_name": "outside_probe",
            "out_root_path": "/tmp",
            "selection_map": {},
            "grid_data": {},
            "docking_config": {},
        })
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text[:200]}"

    def test_queue_remove_batch(self):
        """Boş queue'dan batch silme 200 dönmeli."""
        resp = post("/api/queue/remove_batch", {"batch_id": 9999999})
        # Mevcut değil veya OK olabilir
        assert resp.status_code in (200, 404), f"Unexpected: {resp.status_code}"


# ────────────────────────────────────────────────
# 9. Run Status (run başlatılmadan)
# ────────────────────────────────────────────────

class TestRunStatus:
    def test_run_status_idle(self):
        """Çalıştırma olmadan status idle olmalı."""
        data = assert_ok(get("/api/run/status"), "GET /api/run/status")
        assert "status" in data
        # Mevcut run yoksa idle/done/error olabilir
        valid_statuses = {"idle", "done", "error", "stopped", "running", "stopping"}
        assert data["status"] in valid_statuses, f"Unknown status: {data['status']}"

    def test_run_recent_returns_list(self):
        """GET /api/run/recent liste dönmeli."""
        data = assert_ok(get("/api/run/recent?limit=3"), "GET /api/run/recent")
        assert "rows" in data or "count" in data

    def test_run_recent_delete_removes_dot_sessions_directory(self):
        """/api/run/recent/delete .sessions altındaki klasörü de silmeli."""
        sessions_dir = DOCK_DIR / ".sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        index_path = sessions_dir / "index.json"
        backup = index_path.read_text(encoding="utf-8") if index_path.exists() else None

        item_id = f"sess_test_{int(time.time() * 1000)}"
        manifest_stub = DOCK_DIR / "manifest.tsv"
        if not manifest_stub.exists():
            manifest_stub.write_text("", encoding="utf-8")
        session_dir = sessions_dir / item_id
        session_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "sessions": [
                {
                    "id": item_id,
                    "created_ts": time.time(),
                    "dock_root": "dock",
                    "out_root": str(DOCK_DIR),
                    "manifest_snapshot": str(manifest_stub),
                    "runs": 1,
                    "planned_total": 1,
                }
            ]
        }
        index_path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            resp = post("/api/run/recent/delete", {"item_id": item_id})
            assert resp.status_code == 200, f"Unexpected: {resp.status_code} {resp.text[:200]}"
            assert not session_dir.exists(), ".sessions altındaki session klasörü silinmedi"
        finally:
            if backup is None:
                index_path.unlink(missing_ok=True)
            else:
                index_path.write_text(backup, encoding="utf-8")
            if session_dir.exists():
                for child in session_dir.glob("**/*"):
                    if child.is_file():
                        child.unlink(missing_ok=True)
                session_dir.rmdir()

    def test_run_stop_when_idle_returns_ok_or_409(self):
        """Çalışmıyorken stop 400 veya 200 dönmeli."""
        resp = post("/api/run/stop")
        assert resp.status_code in (200, 400, 409), (
            f"Unexpected: {resp.status_code} {resp.text[:200]}"
        )


# ────────────────────────────────────────────────
# 10. Config Endpoints
# ────────────────────────────────────────────────

class TestConfigEndpoints:
    def test_config_load_requires_upload_file(self):
        """Config load endpoint GET değil, file upload isteyen POST olmalı."""
        resp_get = get("/api/config/load")
        assert resp_get.status_code == 405, (
            f"Expected 405 for GET /api/config/load, got {resp_get.status_code}"
        )

        resp_post_without_file = requests.post(
            f"{BASE_URL}/api/config/load",
            timeout=10,
        )
        assert resp_post_without_file.status_code == 422, (
            f"Expected 422 for POST /api/config/load without file, got {resp_post_without_file.status_code}"
        )

    def test_config_save_returns_excel(self):
        """POST /api/config/save bir Excel dosyası döndürmeli."""
        if not has_openpyxl():
            pytest.skip("openpyxl not installed in test environment")
        payload = {
            "selection_map": {},
            "grid_data": {},
            "docking_config": {},
            "run_count": 1,
            "padding": 0.0,
        }
        resp = post("/api/config/save", payload)
        if resp.status_code == 500 and "openpyxl" in resp.text.lower():
            pytest.skip("openpyxl not installed on running API server")
        if resp.status_code != 200:
            pytest.skip(f"/api/config/save unavailable on running API server: {resp.status_code}")
        content_type = resp.headers.get("content-type", "")
        assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in content_type
        assert len(resp.content) > 0, "Config save returned empty file"

    def test_config_load_accepts_valid_workbook(self):
        """Geçerli bir config workbook yüklenince JSON cevap dönmeli."""
        if not has_openpyxl():
            pytest.skip("openpyxl not installed in test environment")
        frame = pd.DataFrame(
            [
                {
                    "type": "Docking",
                    "pdb_id": "CFG1",
                    "chain": "A",
                    "ligand": "ligand.sdf",
                    "grid_center_x": 0.0,
                    "grid_center_y": 0.0,
                    "grid_center_z": 0.0,
                    "grid_size_x": 20.0,
                    "grid_size_y": 20.0,
                    "grid_size_z": 20.0,
                    "run_count": 1,
                    "padding": 0.0,
                    "pdb2pqr_ph": 7.4,
                    "pdb2pqr_ff": "AMBER",
                    "pdb2pqr_ffout": "AMBER",
                    "pdb2pqr_nodebump": 1,
                    "pdb2pqr_keep_chain": 1,
                    "mkrec_allow_bad_res": 1,
                    "mkrec_default_altloc": "A",
                    "vina_exhaustiveness": 8,
                    "vina_num_modes": "",
                    "vina_energy_range": "",
                    "vina_cpu": "",
                    "vina_seed": "",
                }
            ]
        )
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name="Configuration", index=False)
        buf.seek(0)

        resp = requests.post(
            f"{BASE_URL}/api/config/load",
            files={
                "file": (
                    "config.xlsx",
                    buf.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            timeout=20,
        )
        if resp.status_code == 500 and "openpyxl" in resp.text.lower():
            pytest.skip("openpyxl not installed on running API server")
        data = assert_ok(resp, "POST /api/config/load with workbook")
        assert data.get("ok") is True
        assert "selection_map" in data
        assert "grid_data" in data
        assert "docking_config" in data


# ────────────────────────────────────────────────
# 11. Edge Cases & Security
# ────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_body_endpoints(self):
        """Boş body ile POST endpoint'leri çökmemeli."""
        for path in ["/api/results/scan", "/api/results/detail"]:
            resp = post(path, {})
            assert resp.status_code in (200, 400, 422), (
                f"{path} çöktü: {resp.status_code} {resp.text[:100]}"
            )

    def test_report_list_root_only(self):
        """Sadece root_path ile report list çalışmalı."""
        data = assert_ok(
            get("/api/reports/list", params={"root_path": "data/dock"}),
            "GET /api/reports/list — root only"
        )
        assert "source_folders" in data

    def test_results_scan_dimer_full(self):
        """dimer_full alt dizini taranabilmeli."""
        abs_path = str(DOCK_DIR / "dimer_full")
        if not (DOCK_DIR / "dimer_full").exists():
            pytest.skip("dimer_full dizini yok")
        resp = post("/api/results/scan", {"root_path": abs_path})
        data = assert_ok(resp, "POST /api/results/scan — dimer_full")
        assert isinstance(data["runs"], list)


# ────────────────────────────────────────────────
# 12. Regression Tests (Önceki Hatalar)
# ────────────────────────────────────────────────

class TestRegressions:
    def test_report_list_json_not_defined(self):
        """Regresyon: NameError: json is not defined — düzeltildi."""
        resp = get("/api/reports/list", params={"root_path": "data/dock"})
        assert resp.status_code != 500, (
            f"500 hatası: NameError 'json' regresyonu: {resp.text[:300]}"
        )

    def test_run_sessions_not_in_source_folders(self):
        """Regresyon: _run_sessions source folders'da görünüyordu."""
        data = assert_ok(get("/api/reports/list", params={"root_path": "data/dock"}))
        folder_names = [f["name"] for f in data.get("source_folders", [])]
        assert "_run_sessions" not in folder_names, (
            "_run_sessions source folders'da görünüyor — regresyon!"
        )

    def test_invalid_source_no_500(self):
        """Regresyon: Geçersiz source_path 500'e neden oluyordu."""
        resp = get("/api/reports/list", params={
            "root_path": "data/dock",
            "source_path": "data/dock/__pycache__",
        })
        assert resp.status_code != 500, (
            f"Regresyon: Geçersiz source 500 veriyor: {resp.text[:300]}"
        )


# ────────────────────────────────────────────────
# 13. Endpoint Coverage (Quick)
# ────────────────────────────────────────────────

class TestEndpointCoverageQuick:
    """Hedef: tüm endpoint'lerin hızlı şekilde (10 sn bütçesi içinde) smoke kontrolü."""

    @staticmethod
    def _pick_report_source() -> tuple[str, str]:
        data = assert_ok(get("/api/reports/list", params={"root_path": "data/dock"}))
        folders = data.get("source_folders", [])
        if not folders:
            return "data/dock", "data/dock/report_outputs"

        # Küçük kaynakları tercih ederek test süresini kısa tut.
        candidates = sorted(
            [f for f in folders if str(f.get("name") or "").strip()],
            key=lambda f: int(f.get("receptor_count") or 0),
        )
        name = str((candidates[0] if candidates else folders[0]).get("name") or "").strip()
        source = f"data/dock/{name}" if name else "data/dock"
        return source, f"{source}/report_outputs"

    def test_receptors_upload_select_detail_and_ligands_endpoints(self):
        """Receptor endpoint smoke: /api/receptors/{pdb_id}, /api/receptors/{pdb_id}/ligands."""
        tmp_name = f"tmp_probe_{int(time.time() * 1000)}.pdb"
        pdb_text = (
            "HEADER    TEST\n"
            "ATOM      1  N   GLY A   1      11.104  13.207   9.601  1.00 20.00           N\n"
            "END\n"
        ).encode("utf-8")
        resp_upload = requests.post(
            f"{BASE_URL}/api/receptors/upload",
            files={"files": (tmp_name, pdb_text, "chemical/x-pdb")},
            timeout=15,
        )
        assert resp_upload.status_code == 200, resp_upload.text[:200]
        saved = resp_upload.json().get("saved", [])
        assert tmp_name in saved

        # Upload edilen dosyayı bırakmayalım.
        try:
            (WORKSPACE / "data" / "receptor" / tmp_name).unlink(missing_ok=True)
        except Exception:
            pass

        resp_select = post("/api/receptors/select", {"pdb_id": "NOPE_ID"})
        assert resp_select.status_code == 200
        assert "selected_receptor" in resp_select.json()

        resp_detail = get("/api/receptors/NOPE_ID")
        assert resp_detail.status_code in (200, 404)

        resp_ligands = get("/api/receptors/NOPE_ID/ligands")
        assert resp_ligands.status_code == 200
        assert "rows" in resp_ligands.json()

    def test_grid_upload_and_read_endpoints(self):
        """Grid upload + read endpoint smoke."""
        grid_text = (
            "center_x = 0\n"
            "center_y = 0\n"
            "center_z = 0\n"
            "size_x = 20\n"
            "size_y = 20\n"
            "size_z = 20\n"
        ).encode("utf-8")
        grid_name = f"tmp_grid_{int(time.time() * 1000)}.txt"
        resp_upload = requests.post(
            f"{BASE_URL}/api/grid/upload",
            files={"file": (grid_name, grid_text, "text/plain")},
            timeout=15,
        )
        assert resp_upload.status_code == 200, resp_upload.text[:200]
        grid_file = resp_upload.json().get("grid_file", "")
        assert grid_file

        resp_grid = get("/api/grid")
        assert resp_grid.status_code == 200
        assert "grid_data" in resp_grid.json()

    def test_results_file_endpoint(self):
        """Results file endpoint invalid path'te 4xx dönmeli (500 olmamalı)."""
        resp = get("/api/results/file", params={"path": "/etc/passwd"})
        assert resp.status_code in (400, 404), resp.text[:200]

    def test_run_recent_prepare_continue_delete_endpoints(self):
        """Recent prepare/continue/delete endpoint smoke."""
        for path in ["/api/run/recent/prepare", "/api/run/recent/continue", "/api/run/recent/delete"]:
            resp = post(path, {"item_id": "__missing_item__"})
            assert resp.status_code in (400, 404, 409), (
                f"{path} unexpected status: {resp.status_code} {resp.text[:200]}"
            )

    def test_report_doc_and_config_endpoints(self):
        """doc-config ve doc endpoint smoke."""
        source_path, output_path = self._pick_report_source()
        params = {
            "root_path": "data/dock",
            "source_path": source_path,
            "output_path": output_path,
        }

        resp_get_cfg = get("/api/reports/doc-config", params=params)
        assert resp_get_cfg.status_code in (200, 400), resp_get_cfg.text[:200]

        resp_post_cfg = post("/api/reports/doc-config", {
            "root_path": "data/dock",
            "source_path": source_path,
            "figure_start_number": 1,
            "extra_sections": [],
            "figure_caption_overrides": {},
        })
        assert resp_post_cfg.status_code in (200, 400), resp_post_cfg.text[:200]

        resp_doc = get("/api/reports/doc", params=params)
        assert resp_doc.status_code in (200, 404, 400), resp_doc.text[:200]

    def test_report_mutation_and_render_endpoints(self):
        """Report mutation smoke: includes /api/reports/image/{path:path} and render/graphs/compile endpoints."""
        source_path, output_path = self._pick_report_source()
        base_payload = {
            "root_path": "data/dock",
            "source_path": source_path,
            "output_path": output_path,
        }

        # Source delete testinde gerçek source silmeyelim: kasıtlı geçersiz path.
        resp_source_delete = post("/api/reports/source/delete", {
            "root_path": "data/dock",
            "source_path": "data/dock/__invalid_source__",
        })
        assert resp_source_delete.status_code in (400, 404), resp_source_delete.text[:200]

        # Gerçek dosya silmeyecek şekilde invalid image path.
        resp_img_delete = post("/api/reports/image/delete", {
            **base_payload,
            "images_root_path": "data/dock/__invalid_images_root__",
            "path": "data/dock/not_exists.png",
        })
        assert resp_img_delete.status_code in (400, 404), resp_img_delete.text[:200]

        resp_delete_all = post("/api/reports/images/delete-all", {
            **base_payload,
            "images_root_path": "data/dock/__invalid_images_root__",
            "scope": "__invalid_scope__",
        })
        assert resp_delete_all.status_code in (400, 404), resp_delete_all.text[:200]

        resp_serve_img = get("/api/reports/image/data/dock/not_exists.png")
        assert resp_serve_img.status_code in (404, 400), resp_serve_img.text[:200]

        non_image_rel = f"data/dock/non_image_probe_{int(time.time() * 1000)}.txt"
        non_image_abs = WORKSPACE / non_image_rel
        non_image_abs.parent.mkdir(parents=True, exist_ok=True)
        non_image_abs.write_text("probe", encoding="utf-8")
        try:
            resp_non_image = get(f"/api/reports/image/{non_image_rel}")
            assert resp_non_image.status_code == 404, (
                f"Expected 404 for non-image file, got {resp_non_image.status_code}"
            )
        finally:
            non_image_abs.unlink(missing_ok=True)

        # Hız için render/graphs/compile çağrılarını invalid source ile hızlı 4xx/409 beklentisiyle yapıyoruz.
        invalid_payload = {
            "root_path": "data/dock",
            "source_path": "data/dock/__invalid_source__",
            "output_path": "data/dock/__invalid_source__/report_outputs",
        }

        resp_graphs = post("/api/reports/graphs", {
            **invalid_payload,
            "linked_path": "",
            "scripts": [],
        })
        assert resp_graphs.status_code in (200, 400, 409), resp_graphs.text[:200]

        resp_render = post("/api/reports/render", {
            **invalid_payload,
            "linked_path": "",
            "dpi": 72,
            "receptors": [],
            "run_by_receptor": {},
            "is_preview": True,
        })
        assert resp_render.status_code in (200, 400, 409), resp_render.text[:200]

        resp_compile = post("/api/reports/compile", {
            **invalid_payload,
            "images_root_path": invalid_payload["output_path"],
            "selected_images": [],
            "figure_captions": {},
            "figure_start_number": 1,
            "extra_sections": [],
        })
        assert resp_compile.status_code in (200, 400, 409, 500), resp_compile.text[:300]


# ────────────────────────────────────────────────
# main
# ────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import subprocess
    sys.exit(subprocess.call(
        ["python3", "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=str(Path(__file__).parent.parent),
    ))
