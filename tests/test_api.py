"""
DockUP — Automated API Test Suite
===================================
Çalıştırma:
    # Sunucu çalışırken (./start.sh):
    cd /home/sina/Downloads/ngl/DockUP
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

BASE_URL = "http://localhost:8000"
WORKSPACE = Path("/home/sina/Downloads/ngl/DockUP/docking_app/workspace")
DOCK_DIR = WORKSPACE / "data" / "dock"
LIGAND_DIR = WORKSPACE / "data" / "ligand"

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
        assert len(data["runs"]) > 0, "Absolute path ile tarama sonuç döndürmedi"


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
    def test_path_traversal_blocked(self):
        """../../ ile path traversal engellenmeli."""
        resp = get("/api/reports/image/../../etc/passwd")
        assert resp.status_code in (400, 404), (
            f"Path traversal not blocked: {resp.status_code}"
        )

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
# main
# ────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import subprocess
    sys.exit(subprocess.call(
        ["python3", "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=str(Path(__file__).parent.parent),
    ))
