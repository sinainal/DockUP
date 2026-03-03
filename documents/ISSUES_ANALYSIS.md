# DockUP — Eski Sürüm vs Yeni Sürüm: Sorun Analizi

> **Oluşturulma:** 2026-03-03  
> **Karşılaştırma:** `old/docking_app/` (çalışan eski versiyon) ↔ `DockUP/docking_app/` (yeni modüler versiyon)  
> **Yöntem:** Kaynak kod satır satır karşılaştırması + canlı endpoint testleri

---

## Özet Tablo

| # | Alan | Önem | Durum |
|---|---|---|---|
| 1 | Queue Build — `grid_data` boş geliyor | 🔴 KRİTİK | Araştırılıyor |
| 2 | `_start_run` out_root BASE'e resolve eder | 🔴 KRİTİK | Aktif sorun |
| 3 | `run_batch.sh` DOCK_DIR'a yazılıyor (workspace) | 🟡 ORTA | Var ama çalışıyor |
| 4 | `scripts/` dizini eksik olabilir | 🔴 KRİTİK | Kontrol gerekli |
| 5 | `_normalize_docking_config` → `normalize_docking_config` | 🟢 DÜZELTILDI | OK |
| 6 | Queue remove_batch endpoint eksik implementasyon | 🟡 ORTA | Araştırılıyor |
| 7 | Resume queue — `_prepare_resume_queue` path sorunu | 🔴 KRİTİK | Araştırılıyor |
| 8 | Results detail endpoint path resolution | 🟡 ORTA | Araştırılıyor |
| 9 | Report: render input `complex.pdb` path sorunu | 🟡 ORTA | Araştırılıyor |
| 10 | Import `_run_job_key` / session helpers | 🟢 DÜZELTILDI | OK |

---

## 1. 🔴 KRİTİK — Queue Build: `grid_data` Boş Geliyor

### Semptom
"Build Queue" tıklanıyor, `POST /api/queue/build` çağrılıyor, `queue_count` hâlâ 0.

### Kök Neden Analizi

`_build_queue()` içindeki kritik kontrol:
```python
# services.py L577
grid_info = grid_data.get(pdb_id)
if not grid_info:
    raise HTTPException(
        status_code=400,
        detail=f"Grid parameters not set for {pdb_id}. ..."
    )
```

Frontend bu endpoint'e şunu göndermeli:
```json
{
  "run_count": 5,
  "padding": 0.0,
  "selection_map": {"6CM4": {"chain": "A", "ligand_resname": "styrene_trimer.sdf"}},
  "grid_data": {"6CM4": {"cx": 10.5, "cy": 20.1, "cz": -5.3, "sx": 25, "sy": 25, "sz": 25}},
  "out_root_path": "data/dock",
  "out_root_name": "my_docking_run",
  "docking_config": {}
}
```

**Olası Frontend Sorunları:**
- `grid_data` hiç gönderilmiyor (boş `{}`)
- `grid_data` içindeki PDB key'i küçük harf (`"6cm4"`) ama `selection_map`'te büyük harf (`"6CM4"`)
- Gridbox çizilmeden "Build Queue" tıklanıyor

### Debug Komutu
```bash
# grid_data dahil queue build test (server çalışırken):
curl -s -X POST http://localhost:8000/api/queue/build \
  -H "Content-Type: application/json" \
  -d '{
    "run_count": 3,
    "out_root_name": "test_run_1",
    "out_root_path": "data/dock",
    "selection_map": {"7X2F": {"chain": "A", "ligand_resname": "ETHYLENE_GLYCOL_monomer.sdf"}},
    "grid_data": {"7X2F": {"cx": 0.0, "cy": 0.0, "cz": 0.0, "sx": 25.0, "sy": 25.0, "sz": 25.0}},
    "docking_config": {}
  }' | python3 -m json.tool

# Ardından state kontrol:
curl -s http://localhost:8000/api/state | python3 -c "
import sys, json; d=json.load(sys.stdin)
print('queue_count:', d.get('queue_count'))
print('out_root:', d.get('out_root'))
"
```

---

## 2. 🔴 KRİTİK — `_start_run` out_root PATH SORUNU

### Fark

**`old/services.py` L733:**
```python
out_root_path = Path(out_root).expanduser()
if not out_root_path.is_absolute():
    out_root_path = (BASE / out_root_path).resolve()  # BASE = DockUP/
```

**`DockUP/services.py` L661:** (AYNI — kopyalandı)
```python
out_root_path = Path(out_root).expanduser()
if not out_root_path.is_absolute():
    out_root_path = (BASE / out_root_path).resolve()  # HATALI: BASE = DockUP/, data/dock orada yok!
```

### Problem
`STATE["out_root"]` = `"data/dock/my_run"` (relative).  
`_start_run` bunu `BASE / "data/dock/my_run"` = `/home/sina/Downloads/ngl/DockUP/data/dock/my_run` olarak çözüyor.  
Ama gerçek path: `/home/sina/Downloads/ngl/DockUP/docking_app/workspace/data/dock/my_run`

### Düzeltme

```python
# services.py L661-663
out_root_path = Path(out_root).expanduser()
if not out_root_path.is_absolute():
    # Önce WORKSPACE_DIR dene
    ws = (WORKSPACE_DIR / out_root_path).resolve()
    if ws.exists() or str(out_root).startswith("data/"):
        out_root_path = ws
    else:
        out_root_path = (BASE / out_root_path).resolve()
```

---

## 3. 🔴 KRİTİK — `scripts/` Dizini Eksik

### Fark

**`old/services.py` L729:**
```python
script_dir = BASE / "scripts"
```

**`DockUP/services.py` L659:**
```python
script_dir = BASE / "scripts"   # Aynı — DockUP/scripts/ var mı?
```

### Kontrol
```bash
ls /home/sina/Downloads/ngl/DockUP/scripts/
# Eğer run1.sh yoksa docking asla çalışmaz!
```

Eski versiyonda `old/scripts/run1.sh` ve `old/scripts/dock1.sh` vardı. Yeni DockUP'a kopyalanmış mı?

---

## 4. 🔴 KRİTİK — Resume Queue Path Sorunu

### Fonksiyon: `_prepare_resume_queue` (`routes/core.py` L395)

```python
# core.py içinde
queue_rows, meta = _prepare_resume_queue(item_id=item_id, replace_queue=replace_queue)
```

`_prepare_resume_queue` şöyle çalışır:
1. `load_run_sessions()` → `_run_sessions/index.json` okur
2. Olası sorun: Session `manifest_path` absolute path saklar → eski workspace yoluyla (`old/docking_app/workspace/`) → o yol ARTIK YOK

Eğer sessions.index.json içinde eski pathler varsa resume hiçbir zaman çalışmaz.

### Debug
```bash
cat "/home/sina/Downloads/ngl/DockUP/docking_app/workspace/data/dock/_run_sessions/index.json" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for s in data[:3]:
    print('manifest:', s.get('manifest_path'))
    print('out_root:', s.get('out_root'))
    print('---')
"
```

---

## 5. 🟡 ORTA — Results Detail / File Endpoint Path Sorunu

### `results/detail` endpoint

```python
# results.py
@router.post("/api/results/detail")
def results_detail(payload: dict[str, Any]) -> JSONResponse:
    result_dir = str(payload.get("result_dir") or "")
    ...
```

`result_dir` frontend'den absolute path geliyor (eski scan'den).  
Eski scan WORKSPACE yolunu döner ama:
- `relative_to_base()` bunu doğru işleyebiliyor mu? ✅ (düzeltildi)

### `results/file` endpoint

```python
# results.py L28-35
rp = Path(raw_path).expanduser()
if not rp.is_absolute():
    rp = (BASE / rp).resolve()  # 🔴 SORUN: WORKSPACE_DIR değil BASE!
```

**Düzeltme gerekli.**

---

## 6. 🟡 ORTA — Queue Remove Batch

### DockUP `core.py`:
```python
@router.post("/api/queue/remove_batch")
def queue_remove_batch(payload: dict[str, Any]) -> JSONResponse:
    ...
```

Eski versiyonda bu endpoint vardı ama `batch_id` matched doğru çalışıyor mu kontrol edilmeli.

---

## 7. 🟡 ORTA — Session Index Eski Pathler İçeriyor

Workspace `old/` altından taşındı ama `_run_sessions/index.json` hâlâ eski pathler içeriyor:
```json
{
  "manifest_path": "/home/sina/Downloads/ngl/old/docking_app/workspace/data/dock/manifest.tsv",
  "out_root": "/home/sina/Downloads/ngl/old/docking_app/workspace/data/dock/test_run"
}
```

Bu pathler artık yok. Resume çalışmaz. Çözüm: `scan_recent_incomplete_rows()` path-existence kontrolü yapmalı.

---

## 8. 🟡 ORTA — Report Render `_find_render_inputs` Path Sorunu

```python
# report.py L932
def _find_render_inputs(source_dir, receptor_id, ligand_name, preferred_run):
    ...
    run_dirs = _valid_run_dirs(ligand_dir)  # complex.pdb arar
```

`complex.pdb` bulunursa absolute path döner. `_render_dtype_panel()` bunu PyMOL'e passar. PyMOL kurulu mu? Kurulu değilse render sessizce fail eder.

---

## 9. 🟢 DÜZELTILDI — `_normalize_docking_config` Import

**Problem:** `services.py` içinde `_normalize_docking_config` çağrılıyordu ama bu fonksiyon `helpers.py`'ye taşındı ve `normalize_docking_config` olarak yeniden adlandırıldı.

**Durum:** DockUP `services.py` L558'de:
```python
docking_config = normalize_docking_config(...)  # ✅ Doğru import
```

---

## 10. 🟢 DÜZELTILDI — Missing Imports & Functions

| Sorun | Durum |
|---|---|
| `import json` eksikti in `report.py` | ✅ Düzeltildi |
| `WORKSPACE_RESOLVED` `helpers.py`'de yoktu | ✅ Eklendi |
| `_cleanup_ligand_dir_names()` `core.py`'de yoktu | ✅ Eklendi |
| `resolve_dock_directory` relative path sorunu | ✅ Düzeltildi |
| Report image 404 (`BASE` yerine `WORKSPACE_DIR`) | ✅ Düzeltildi |
| `_run_sessions` folder picker'da görünüyor | ✅ Filtrelendi |
| Invalid source_path → 400 kilitlenmesi | ✅ Graceful fallback eklendi |

---

## Öncelikli Aksiyon Listesi

### Hemen Yapılmalı (Kritik)

#### A. `scripts/` kontrolü
```bash
ls -la /home/sina/Downloads/ngl/DockUP/scripts/
# run1.sh, dock1.sh burada olmalı
```
Yoksa `old/scripts/` içindekilerini kopyala:
```bash
cp -r /home/sina/Downloads/ngl/old/scripts /home/sina/Downloads/ngl/DockUP/
```

#### B. `_start_run` out_root fix (`services.py` L661-663)
```python
# MEVCUT (hatalı):
out_root_path = (BASE / out_root_path).resolve()

# DÜZELTME:
ws_candidate = (WORKSPACE_DIR / out_root_path).resolve()
if str(out_root).startswith("data/") or ws_candidate.parent.exists():
    out_root_path = ws_candidate
else:
    out_root_path = (BASE / out_root_path).resolve()
```

#### C. `results/file` fix (`results.py` L30)
```python
# MEVCUT:
rp = (BASE / rp).resolve()
# DÜZELTME:
ws = (WORKSPACE_DIR / rp).resolve()
rp = ws if ws.exists() else (BASE / rp).resolve()
```

#### D. Session index path güncellemesi
Eski session'ları temizle veya path'leri yeni workspace ile düzelt.

### Sonra Yapılmalı

#### E. Queue build grid_data debug
Frontend'in tam olarak ne gönderdiğini görmek için geçici log ekle:
```python
# core.py queue_build içine geçici debug:
import logging
logging.warning(f"QUEUE BUILD: selection_map={payload.get('selection_map')}, grid_data={payload.get('grid_data')}")
```

---

## Karşılaştırma Özeti: Büyük Farklar

| Bileşen | Eski (working) | Yeni (DockUP) | Sorun |
|---|---|---|---|
| `routes.py` | Tek dosya, 3892 satır | 4 ayrı router | ✅ Mantıksal, sorun yok |
| `_normalize_docking_config` | Inline tanımlı | `helpers.normalize_docking_config` | ✅ Düzeltildi |
| PATH resolution | BASE (workspace=BASE) | WORKSPACE_DIR ayrı | ⚠️ Tüm relative path'ler kontrol edilmeli |
| Workspace | `old/docking_app/workspace/` | `DockUP/docking_app/workspace/` | ✅ Taşındı |
| `scripts/` | `old/scripts/` | `DockUP/scripts/`? | 🔴 Kontrol gerekli |
| Sessions index | Eski pathler | Eski pathler (taşınmadı) | 🔴 Stale paths |
| `_start_run` out_root | BASE çözümü (çalışıyordu çünkü workspace=simlink) | BASE çözümü (BOZUK - symlink yok) | 🔴 Düzeltilmeli |

---

*Bu analiz raporu `old/docking_app/*` ve `DockUP/docking_app/*` karşılaştırmasına dayanmaktadır. Sunucu testleri: 2026-03-03.*
