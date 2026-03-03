# DockUP — Tam İşlev Referansı (Hata Ayıklama Kılavuzu)

> **Oluşturulma:** 2026-03-03  
> **Sürüm:** DockUP refactored build  
> **Amaç:** Uygulama işlevlerinin tamamını belgeleyen referans dökümanı. Hata ayıklama, regresyon testi ve geliştirme için kullanılır.

---

## İçindekiler

1. [Dizin Yapısı](#1-dizin-yapısı)
2. [Konfigürasyon (config.py)](#2-konfigürasyon)
3. [State (Durum Yönetimi)](#3-state)
4. [Yardımcı Fonksiyonlar (helpers.py)](#4-yardımcı-fonksiyonlar)
5. [Manifest (manifest.py)](#5-manifest)
6. [Servisler (services.py)](#6-servisler)
7. [Oturumlar (sessions.py)](#7-oturumlar)
8. [API — Core Routes](#8-api--core-routes)
9. [API — Results Routes](#9-api--results-routes)
10. [API — Config Routes](#10-api--config-routes)
11. [API — Report Routes](#11-api--report-routes)
12. [Bilinen Sorunlar ve Hata Ayıklama Notları](#12-bilinen-sorunlar-ve-hata-ayıklama-notları)
13. [Frontend ↔ Backend Etkileşim Şeması](#13-frontend--backend-etkileşim-şeması)

---

## 1. Dizin Yapısı

```
DockUP/
├── start.sh                        # Uygulamayı başlatan shell scripti
├── documents/                      # Bu referans dökümanı
├── figure_scripts/                 # Grafik üretimi için Python modülleri
│   └── final_plots/
│       ├── affinity_variants.py    # Affinite boxplot
│       ├── interacted_residue_plots.py  # Interaksiyon frekans ısı haritası
│       ├── common_residue_interactions.py  # Ortak rezidü ısı haritası
│       └── interaction_plots.py    # Yığılmış çubuk grafik
└── docking_app/
    ├── __init__.py                 # app nesnesini export eder
    ├── app.py                      # FastAPI uygulaması oluşturulur
    ├── cli.py                      # Komut satırı arayüzü
    ├── config.py                   # Path sabitleri
    ├── state.py                    # Global uygulama durumu
    ├── helpers.py                  # Ortak yardımcı fonksiyonlar
    ├── manifest.py                 # Manifest okuma/yazma
    ├── sessions.py                 # Run session yönetimi
    ├── services.py                 # İş mantığı servisleri
    ├── models.py                   # Pydantic veri modelleri
    ├── templates/                  # Jinja2 HTML şablonları
    ├── static/                     # CSS, JS dosyaları
    └── routes/
        ├── __init__.py             # Router toparlama
        ├── core.py                 # Temel API endpoint'leri
        ├── results.py              # Sonuç tarama endpoint'leri
        ├── config_routes.py        # Konfigürasyon endpoint'leri
        └── report.py               # Rapor üretim endpoint'leri

    workspace/                      # Tüm veri dosyaları (665 MB)
        data/
        ├── ligand/                 # Yüklenen .sdf ligand dosyaları
        ├── receptor/               # Yüklenen .pdb reseptör dosyaları
        └── dock/                   # Docking çıktı dizini
            ├── manifest.tsv        # Aktif docking kuyruğu
            ├── _run_sessions/      # Run oturum index'i (JSON)
            ├── dimer_full/         # Tam dimer sonuçları
            ├── dimer_final_linked/ # Bağlantılı/organize dimer sonuçları
            └── dopamine_trimer/    # Örnek veri seti
        plip-2.4.0/                 # PLIP protein-ligand etkileşim analiz aracı
```

---

## 2. Konfigürasyon

**Dosya:** `docking_app/config.py`

| Sabit | Değer | Açıklama |
|---|---|---|
| `BASE` | `DockUP/` | Uygulama kök dizini |
| `PACKAGE_DIR` | `DockUP/docking_app/` | Paket dizini |
| `WORKSPACE_DIR` | `docking_app/workspace/` | Tüm runtime verisi |
| `DATA_DIR` | `workspace/data/` | Veri ana dizini |
| `LIGAND_DIR` | `data/ligand/` | Ligand deposu |
| `RECEPTOR_DIR` | `data/receptor/` | Reseptör deposu |
| `DOCK_DIR` | `data/dock/` | Docking çıktı dizini |
| `PLIP_DIR` | `workspace/plip-2.4.0/` | PLIP kurulum dizini |
| `TEMPLATES_DIR` | `docking_app/templates/` | HTML şablonları |
| `STATIC_DIR` | `docking_app/static/` | Statik dosyalar |

> **Önemli:** Tüm relative path'ler (`data/dock` gibi) önce `WORKSPACE_DIR` altında aranır, bulunamazsa `BASE` altında aranır.

---

## 3. State

**Dosya:** `docking_app/state.py`

Uygulama hafızada bir `STATE` dictionary tutar. Sunucu yeniden başlatıldığında sıfırlanır.

### STATE Anahtarları

| Anahtar | Tip | Açıklama |
|---|---|---|
| `mode` | `str` | Aktif mod: `"Docking"`, `"Redocking"`, `"Results"`, `"Report"` |
| `selected_receptor` | `str` | Seçili PDB ID |
| `selected_ligand` | `str` | Seçili ligand adı |
| `selected_chain` | `str` | Seçili zincir (ör: `"A"`, `"all"`) |
| `grid_file_path` | `str` | Yüklü grid dosyasının tam yolu |
| `queue` | `list` | Aktif docking görevi kuyruğu |
| `runs` | `int` | Her iş çifti için run sayısı |
| `out_root` | `str` | Docking çıktı kök dizini |
| `out_root_path` | `str` | Çıktı dizini parent path'i |
| `out_root_name` | `str` | Çıktı dizini ismi |
| `receptor_meta` | `list` | Yüklü reseptörlerin metadata listesi |
| `selection_map` | `dict` | `{pdb_id: {chain, ligand_resname}}` |
| `results_root_path` | `str` | Sonuç tarama için kök path |
| `docking_config` | `dict` | Aktif docking parametreleri |
| `grid_pad` | `float` | Grid padding değeri |

### RUN_STATE Anahtarları

| Anahtar | Tip | Açıklama |
|---|---|---|
| `status` | `str` | `"idle"`, `"running"`, `"stopping"`, `"stopped"`, `"error"`, `"done"` |
| `returncode` | `int\|None` | İşlem çıkış kodu |
| `log_lines` | `list[str]` | Son 400 log satırı |
| `command` | `str` | Çalıştırılan komut string'i |
| `out_root` | `str` | Aktif çalışmanın çıktı dizini |
| `start_time` | `float` | Unix timestamp |
| `total_runs` | `int` | Toplam planlanan run sayısı |
| `completed_runs` | `int` | Tamamlanan run sayısı |
| `batch_log_path` | `str` | Batch log dosyası yolu |

### DOCKING_CONFIG_DEFAULTS

```python
{
    "pdb2pqr_ph": 7.4,
    "pdb2pqr_ff": "AMBER",
    "pdb2pqr_ffout": "",
    "pdb2pqr_nodebump": True,
    "pdb2pqr_keep_chain": True,
    "mkrec_allow_bad_res": False,
    "mkrec_default_altloc": "A",
    "vina_exhaustiveness": 8,
    "vina_num_modes": None,
    "vina_energy_range": None,
    "vina_cpu": None,
    "vina_seed": None,
}
```

---

## 4. Yardımcı Fonksiyonlar

**Dosya:** `docking_app/helpers.py`

### Tip Dönüşüm Yardımcıları

| Fonksiyon | İmza | Açıklama |
|---|---|---|
| `boolish` | `(value, default) → bool` | "true"/"false"/"1"/"0"/"yes"/"no" gibi string'leri bool'a çevirir |
| `to_optional_int` | `(value, min, max) → int\|None` | Boş veya hatalı değerde None döner |
| `to_optional_float` | `(value, min, max) → float\|None` | Boş veya hatalı değerde None döner |

### Docking Konfigürasyon

| Fonksiyon | İmza | Açıklama |
|---|---|---|
| `normalize_docking_config` | `(raw) → dict` | Ham dict'i güvenli tiplerle temizler |
| `restore_manifest_value` | `(raw) → str` | `__EMPTY__` sentinel'ini `""` yapar |

### Path Yardımcıları

| Fonksiyon | İmza | Açıklama |
|---|---|---|
| `to_display_path` | `(path) → str` | Absolute path'i `data/dock/...` formatına çevirir |
| `relative_to_base` | `(path) → str\|None` | BASE veya WORKSPACE dışındaysa `None` döner |
| `resolve_dock_directory` | `(path_text, default, allow_create) → Path` | Relative path'i WORKSPACE/BASE altında çözer; DOCK_DIR dışındaysa 400 fırlatır |
| `safe_mtime` | `(path) → float` | Dosya değişim zamanı, hata = 0.0 |
| `read_json` | `(path, default) → Any` | JSON okur, hata varsa default döner |
| `write_json` | `(path, payload) → None` | JSON'u atomik yazar (.tmp → rename) |
| `timestamp_token` | `() → str` | `"20260303_161513"` formatında timestamp |

### Path Sabitleri (modül seviyesinde)

```python
BASE_RESOLVED = BASE.resolve()
DATA_DIR_RESOLVED = DATA_DIR.resolve()
DOCK_DIR_RESOLVED = DOCK_DIR.resolve()
WORKSPACE_RESOLVED = WORKSPACE_DIR.resolve()
```

---

## 5. Manifest

**Dosya:** `docking_app/manifest.py`

Manifest (`manifest.tsv`), docking kuyruğunu TSV formatında tutar. Her satır bir docking işini tanımlar.

### Manifest Sütun Düzeni

```
pdb_id | chain | ligand | lig_spec | pdb_file | grid_pad | grid_file | force_run_id |
pdb2pqr_ph | pdb2pqr_ff | pdb2pqr_ffout | pdb2pqr_nodebump | pdb2pqr_keep_chain |
mkrec_allow_bad_res | mkrec_default_altloc | vina_exhaustiveness |
vina_num_modes | vina_energy_range | vina_cpu | vina_seed
```

### Fonksiyonlar

| Fonksiyon | Açıklama |
|---|---|
| `config_to_manifest_values(cfg)` | Docking config dict'ini string listesine dönüştürür (manifest satırı için) |
| `manifest_values_to_config(cols)` | TSV sütunlarını config dict'ine geri çevirir |
| `append_docking_config_args(args, cfg_raw)` | CLI argümanlarına docking parametrelerini ekler |
| `parse_manifest_rows(manifest_path)` | Manifest dosyasını okur, her satırı dict listesi olarak döner |
| `write_manifest(path, queue, global_cfg)` | Queue listesinden manifest.tsv oluşturur |
| `build_preview_command(queue, out_root, runs)` | İlk queue elemanı için run1.sh preview komutu üretir |
| `normalize_ligand_folder_name(name)` | Ligand klasör adını normalize eder |
| `persist_root_run_meta(...)` | Docking kök meta verisini JSON'a yazar |

---

## 6. Servisler

**Dosya:** `docking_app/services.py`

Backend iş mantığını içerir. Route handler'ları tarafından çağrılır.

### Reseptör Yönetimi

| Fonksiyon | Açıklama |
|---|---|
| `_load_receptor_meta(pdb_ids, pdb_files)` | PDB ID listesi için online PDB fetch veya yerel dosya okuma |
| `_summarize_receptors(meta_list)` | Reseptör metadata listesini frontend'e uygun formata çevirir |
| `_get_meta(pdb_id)` | STATE'ten belirli bir PDB ID'nin metadata'sını bulur |
| `_init_selection_map(meta_list)` | Yeni reseptörler için selection_map başlangıç değerleri |

### Ligand & Dosya İşlemleri

| Fonksiyon | Açıklama |
|---|---|
| `_save_uploads(files, target_dir)` | UploadFile listesini hedef dizine kaydeder |
| `_existing_files(directory, extensions)` | Dizindeki belirli uzantılı dosyaları listeler |
| `_ligand_table(meta)` | Bir reseptör için ligand listesini tablo formatında döner |

### Grid & Queue

| Fonksiyon | Açıklama |
|---|---|
| `_parse_grid_file(path)` | `.gpf` veya `gridbox.txt` dosyasını okur |
| `_build_queue(payload)` | Frontend payload'ından docking queue job'ları oluşturur |

### Run Yönetimi

| Fonksiyon | Açıklama |
|---|---|
| `_start_run(manifest_path, runs, out_root, total, preview_cmd, is_test)` | `run1.sh` scriptini arka planda subprocess olarak başlatır; `RUN_STATE`'i günceller |

### Sonuç Analizi

| Fonksiyon | Açıklama |
|---|---|
| `_scan_results(root_path)` | Dizindeki tüm docking sonuçlarını tarar; `{runs, averages, root_path}` döner |
| `_parse_results_folder(target)` | Tek bir run dizinini parse eder (affinity, RMSD, PDB ID) |
| `_parse_plip_report(xml_path)` | PLIP `report.xml`'ini okur; interactions, residues, ligand_info döner |

### Bash Script Üretimi (run1.sh için)

| Fonksiyon | Açıklama |
|---|---|
| `_generate_run_script(...)` | Bash run script'ini oluşturur |
| `_is_empty(value)` | Boş değer kontrolü |
| `_now_str()` | Geçerli tarih/saati string döner |

---

## 7. Oturumlar

**Dosya:** `docking_app/sessions.py`

Run oturumları, `DOCK_DIR/_run_sessions/index.json` dosyasında saklanır.

### Fonksiyonlar

| Fonksiyon | Açıklama |
|---|---|
| `load_run_sessions()` | `_run_sessions/index.json`'ı okur; hata varsa `[]` döner |
| `save_run_sessions(sessions)` | Session listesini `index.json`'a yazar |
| `register_run_session(out_root, runs, manifest_path, planned_total)` | Yeni session oluşturur ve index'e ekler |
| `scan_recent_incomplete_rows(limit, include_jobs)` | Son N tamamlanmamış run'ı tarar; resume edilebilirlik bilgisi içerir |
| `collect_resume_sessions()` | Devam ettirilebilir tüm session'ları listeler |
| `build_legacy_session_entry(dock_root)` | Eski format docking dizinlerini session formatına dönüştürür |

### Session Yapısı

```json
{
  "id": "uuid4",
  "out_root": "/abs/path/to/dock/output",
  "dock_root": "dimer_full",
  "created_at": "2026-03-03T16:00:00",
  "planned_total_runs": 25,
  "queue_count": 5,
  "runs": 5,
  "mode": "fresh",
  "manifest_path": "/abs/path/manifest.tsv",
  "resumable": true,
  "resume_reason": "",
  "pending_queue_rows": [...]
}
```

---

## 8. API — Core Routes

**Dosya:** `docking_app/routes/core.py`  
**Router prefix:** `/`

### Ana Sayfalar

| Method | Endpoint | Açıklama | Dönen |
|---|---|---|---|
| GET | `/` | Frontend HTML sayfası | HTML |
| GET | `/api/state` | Tüm uygulama state'ini döner | `{mode, selected_receptor, selected_ligand, selected_chain, grid_file_path, queue_count, runs, grid_pad, docking_config, out_root, run_status, ...}` |

### Mod Kontrolü

| Method | Endpoint | Body | Açıklama |
|---|---|---|---|
| POST | `/api/mode` | `{mode: "Docking"\|"Redocking"\|"Results"\|"Report"}` | Aktif modu değiştirir |

### Ligand Yönetimi

| Method | Endpoint | Açıklama | Dönen |
|---|---|---|---|
| GET | `/api/ligands/list` | Yüklü `.sdf` ligandları listeler; normalize eder | `{ligands: [name, ...]}` |
| POST | `/api/ligands/upload` | Dosya yükle (`multipart/form-data`) | `{saved: [name, ...]}` |
| POST | `/api/ligands/delete` | `{name: "file.sdf"}` ile ligand siler | `{ligands: [...]}` |
| POST | `/api/ligands/select` | `{pdb_id, chain, ligand}` seçimini kaydeder | `{ok: true}` |

> **Sorun (bilinen):** `list_ligands` çağrısında `_cleanup_ligand_dir_names()` timestamp suffix'lerini kaldırır. Dosyalar rename edilir.

### Reseptör Yönetimi

| Method | Endpoint | Body/Query | Açıklama | Dönen |
|---|---|---|---|---|
| POST | `/api/receptors/upload` | `multipart/form-data` | PDB dosyalarını yükler | `{saved: [...]}` |
| POST | `/api/receptors/load` | `{pdb_ids: "7X2F\n6CM4\n..."}` | PDB ID'leri ile online'dan PDB fetch'ler | `{summary: [...]}` |
| POST | `/api/receptors/remove` | `{pdb_id}` | STATE'ten reseptör kaldırır | `{summary: [...]}` |
| GET | `/api/receptors/summary` | — | Yüklü reseptör özetini döner | `{summary: [...]}` |
| POST | `/api/receptors/select` | `{pdb_id}` | Aktif reseptörü değiştirir | `{selected_receptor}` |
| GET | `/api/receptors/{pdb_id}` | — | Reseptör detayları + grid verisi | `{pdb_id, pdb_text, chains, ligands_by_chain, grid_data, ...}` |
| GET | `/api/receptors/{pdb_id}/ligands` | — | Reseptörün ligand tablosunu döner | `{rows: [...]}` |

### Grid Yönetimi

| Method | Endpoint | Açıklama |
|---|---|---|
| POST | `/api/grid/upload` | Grid dosyası yükler, `STATE["grid_file_path"]` günceller |
| GET | `/api/grid` | Aktif grid verisini döner |

### Queue Yönetimi

| Method | Endpoint | Body | Açıklama |
|---|---|---|---|
| POST | `/api/queue/build` | `{run_count, padding, selection_map, grid_data, docking_config, out_root_path, out_root_name}` | Queue'ya yeni job'lar ekler |
| POST | `/api/queue/remove_batch` | `{batch_id}` | Belirli batch'i queue'dan kaldırır |

> **Sorun (aktif):** Queue build endpoint'i çalışmıyor — `_build_queue()` fonksiyonu payload'ı doğru işlemiyor olabilir. `STATE["queue"]` boş kalıyor ya da `grid_data` eksik aktarılıyor olabilir. Debug için: `POST /api/queue/build` sonrası `GET /api/state` → `queue_count` ve `queue` alanlarını kontrol et.

### Run Kontrolü

| Method | Endpoint | Body | Açıklama | Dönen |
|---|---|---|---|---|
| POST | `/api/run/start` | `{is_test_mode: bool}` | manifest.tsv yazar, run1.sh başlatır | `{status, command, out_root}` |
| POST | `/api/run/stop` | — | SIGTERM → SIGKILL ile aktif süreci durdurur | `{status, returncode, message}` |
| GET | `/api/run/status` | — | Anlık run durumu + log | `{status, log, command, total_runs, completed_runs, elapsed_seconds}` |
| GET | `/api/run/recent` | `?limit=1-3` | Son tamamlanmamış run kayıtlarını listeler | `{count, rows}` |
| POST | `/api/run/recent/prepare` | `{item_id, replace_queue}` | Seçili run'ı resume için queue'ya hazırlar | `{ok, prepared_count, queue, out_root}` |
| POST | `/api/run/recent/continue` | `{item_id, replace_queue, is_test_mode}` | Hazırlanmış queue'yu başlatır | `{status, command, prepared_count}` |
| POST | `/api/run/recent/delete` | `{item_id}` | Session index'ten bir kaydı siler | `{ok, deleted_id, count}` |

---

## 9. API — Results Routes

**Dosya:** `docking_app/routes/results.py`

### Sonuç Tarama

| Method | Endpoint | Body/Query | Açıklama | Dönen |
|---|---|---|---|---|
| POST | `/api/results/scan` | `{root_path: "data/dock"}` | Dizindeki tüm docking sonuçlarını tarar | `{runs: [...], averages: [...], root_path}` |
| POST | `/api/results/detail` | `{result_dir: "/abs/path"}` | Tek run'ın detaylarını + PLIP etkileşimlerini döner | `{result, residues, interactions}` |
| GET | `/api/results/file` | `?path=...` | PDB/SDF dosyası sunar | `FileResponse` |

#### Scan Çıktısı (her `run` elemanı)

```json
{
  "pdb_id": "6CM4",
  "ligand_display_name": "styrene_dimer",
  "chain": "A",
  "run_id": 1,
  "affinity": -7.7,
  "rmsd": null,
  "pose_path": "/abs/path/.../6CM4_pose.pdb",
  "receptor_path": "/abs/path/.../6CM4_rec_raw.pdb",
  "complex_path": "/abs/path/.../6CM4_complex.pdb",
  "report_path": "/abs/path/.../plip/report.xml"
}
```

#### Averages Çıktısı (her eleman)

```json
{
  "pdb_id": "6CM4",
  "ligand_display_name": "styrene_dimer",
  "run_count": 5,
  "avg_affinity": -7.68,
  "min_affinity": -7.7,
  "max_affinity": -7.6,
  "avg_rmsd": null
}
```

### Path Çözümleme

| Method | Endpoint | Body | Açıklama |
|---|---|---|---|
| POST | `/api/paths/resolve` | `{relative_path, scope: "results"\|"report"\|"generic"}` | Relative path'i gerçek dizin path'ine çözer |

---

## 10. API — Config Routes

**Dosya:** `docking_app/routes/config_routes.py`

| Method | Endpoint | Body | Açıklama | Dönen |
|---|---|---|---|---|
| POST | `/api/config/save` | `multipart/form-data: file (.xlsx)` | Docking config Excel dosyasını yükler ve STATE'i günceller | `{ok, config}` |
| GET | `/api/config/load` | — | Aktif docking config'ini döner | `{config: {...}}` |
| POST | `/api/config/update` | `{pdb2pqr_ph, pdb2pqr_ff, vina_exhaustiveness, ...}` | Config parametrelerini günceller | `{config: {...}}` |

### Excel Konfigürasyon Formatı

Config `.xlsx` dosyası şu sütunları içerir:
- `pdb_id`, `chain`, `ligand`, `lig_spec`, `grid_pad`
- `pdb2pqr_ph`, `pdb2pqr_ff`, `vina_exhaustiveness`, `vina_num_modes`

---

## 11. API — Report Routes

**Dosya:** `docking_app/routes/report.py`

### Kaynak Klasör Yapısı Beklentisi

Rapor sistemi şu dizin yapılarını tanır:

```
# Pattern A: receptor/ligand/runN
source_dir/
  3PBL/
    styrene_dimer/
      run1/  (complex.pdb + interaction_map.json + plip/report.xml)
      run2/
      run3/
    ethylene_dimer/
      run1/

# Pattern B: dimer linked (D1-D5 klasörleri)
source_dir/
  D1/
    ligand_name_1/
      run1/
  D2/ ...

# Pattern C: flat PDB_ligand_runN
source_dir/
  6CM4_styrene_dimer_run1/  (valid run dir)
  6CM4_styrene_dimer_run2/
```

### Geçerli Run Dizini Kriterleri

Bir dizinin "geçerli run" sayılması için şunların hepsi mevcut olmalı:
- `*_complex.pdb` (en az bir tane)
- `interaction_map.json`
- `plip/report.xml`

### Liste & Keşif

| Method | Endpoint | Query Params | Açıklama | Dönen |
|---|---|---|---|---|
| GET | `/api/reports/list` | `root_path`, `source_path`, `output_path` | Kaynak analiz eder; reseptör/ligand/run listesi döner | `{receptors, source_folders, source_metadata, render_images, plot_images, ...}` |
| GET | `/api/reports/images` | `root_path`, `source_path`, `output_path`, `images_root_path` | Üretilmiş imajları listeler | `{render_images, plot_images, images}` |
| GET | `/api/reports/root-metadata` | `root_path`, `source_path` | Kaynak metadata JSON'unu döner | `{path, receptor_order, ligand_order, receptor_labels, ligand_labels, ...}` |
| GET | `/api/reports/doc-config` | `root_path`, `source_path` | Rapor doküman konfigürasyonunu döner | `{figure_start_number, extra_sections, figure_caption_overrides}` |

> **Kaynak Klasör Filtreleme:** `_` ile başlayan klasörler (örn. `_run_sessions`) ve `report_outputs`, `plip`, `plots` gibi dahili klasörler listede görünmez.

> **Hata Toleransı:** Geçersiz `source_path` verilirse 400 yerine otomatik olarak default kaynağa (`dimer_final_linked` → `dimer_full` → `dock` root) düşer.

### Metadata Yönetimi

| Method | Endpoint | Açıklama |
|---|---|---|
| POST | `/api/reports/root-metadata` | `{source_path, main_type, receptor_labels, ligand_labels, receptor_order, ligand_order}` → JSON'a kaydeder |
| POST | `/api/reports/doc-config` | `{source_path, figure_start_number, extra_sections, figure_caption_overrides}` → JSON'a kaydeder |

Metadata dosyası: `source_dir/.docking_app_meta.json`

### Grafik Üretimi

| Method | Endpoint | Body | Açıklama |
|---|---|---|---|
| POST | `/api/reports/graphs` | `{root_path, source_path, output_path, scripts: ["affinity_table_plus_boxplot", ...]}` | Seçili grafikleri arka planda üretir |

Desteklenen grafik türleri:

| ID | Label | Modül | Çıktı Dosyası |
|---|---|---|---|
| `affinity_table_plus_boxplot` | Affinity Table + Boxplot | `figure_scripts.final_plots.affinity_variants` | `affinity_boxplot.png` |
| `interaction_frequency_heatmap` | Interaction Frequency Heatmap | `figure_scripts.final_plots.interacted_residue_plots` | `run_frequency_heatmap.png` |
| `common_residue_heatmap` | Common Residue Heatmap | `figure_scripts.final_plots.common_residue_interactions` | `common_residue_heatmap.png` |
| `interaction_stacked_bar` | Interaction Stacked Bar | `figure_scripts.final_plots.interaction_plots` | `interaction_stacked_bar.png` |

### 3D Render Üretimi

| Method | Endpoint | Body | Açıklama |
|---|---|---|---|
| POST | `/api/reports/render` | `{root_path, source_path, output_path, receptor_ids, ligand_names, preferred_run, dpi, preview_mode}` | Seçili reseptör/ligand kombinasyonları için 3D PNG render üretir |

Render; PyMOL veya benzeri araç ile `*_complex.pdb` dosyasından receptor + pose görüntüsü oluşturur.

### Doküman Derleme

| Method | Endpoint | Body | Açıklama |
|---|---|---|---|
| POST | `/api/reports/compile` | `{root_path, source_path, output_path, receptor_order, ligand_order, selected_images, figure_start_number, extra_sections, main_type_label, ...}` | Seçili imajları kullanarak `.docx` rapor derler |

Çıktı: `output_root/reports/docking_report_mvp.docx`

Rapor yapısı:
1. Materials and Methods başlığı (docking parametreleri)
2. Receptor/Ligand bilgileri
3. Her reseptör için: 3D render imajı + affinity tablosu
4. Grafik imajları (boxplot, heatmap, vb.)
5. Results, Discussion, Conclusion bölümleri (boş şablon)

### Silme İşlemleri

| Method | Endpoint | Açıklama |
|---|---|---|
| POST | `/api/reports/images/delete-all` | Output klasöründeki tüm imajları siler |
| POST | `/api/reports/image/delete` | `{image_path}` ile tek imaj siler |
| POST | `/api/reports/source/delete` | Source klasörünü ve içeriğini siler |

### Dosya Servisi

| Method | Endpoint | Açıklama |
|---|---|---|
| GET | `/api/reports/image/{path:path}` | İmaj dosyasını sunar (WORKSPACE → BASE sırasıyla arar) |
| GET | `/api/reports/doc` | `.docx` raporu indirilir |

### Durum Takibi

| Method | Endpoint | Açıklama |
|---|---|---|
| GET | `/api/reports/status` | Arka plan görev durumunu döner: `{status, progress, message, error}` |

---

## 12. Bilinen Sorunlar ve Hata Ayıklama Notları

### 🔴 Aktif Sorun: Queue Build Çalışmıyor

**Semptom:** "Build Queue" butonu tıklanıyor ama queue dolmuyor.

**Olası Nedenler:**
1. `POST /api/queue/build` payload'ında `selection_map` boş geliyor → reseptör seçilmemiş olabilir
2. `_build_queue()` içinde grid_data eksik → `grid_file_path` STATE'te `""` olabilir
3. Frontend `out_root_name` göndermiyordur → queue'ya eklenmiyor

**Debug Adımları:**
```bash
# 1. State kontrol et
curl http://localhost:8000/api/state | python3 -m json.tool | grep -A5 "queue\|selected"

# 2. Manuel queue build dene
curl -X POST http://localhost:8000/api/queue/build \
  -H "Content-Type: application/json" \
  -d '{"run_count": 1, "out_root_name": "test_run", "out_root_path": "data/dock",
       "selection_map": {"7X2F": {"chain": "A", "ligand_resname": "LDP"}},
       "docking_config": {}}'

# 3. Queue count kontrol
curl http://localhost:8000/api/state | python3 -c "import sys,json;d=json.load(sys.stdin);print('queue:', d['queue_count'], d['queue'])"
```

### 🟡 Path Resolution — Genel Kural

Uygulama iki kökenden path çözer:
1. **WORKSPACE_DIR** (`docking_app/workspace/`) — önce aranır
2. **BASE** (`DockUP/`) — fallback

`data/dock` → `workspace/data/dock` olarak çözümlenir. Bu kuralı bozan bir yerde `(BASE / path)` pattern'i varsa `(WORKSPACE_DIR / path)` ile değiştir.

### 🟡 Sunucu Hot-Reload

`uvicorn` değişiklikleri genellikle 1-3 saniye içinde yakalar. Eğer değişiklik etkili olmuyorsa:
```bash
Ctrl+C → ./start.sh
```

### 🟢 Çalışan Endpoint'ler (Doğrulanmış)

```
GET  /api/state                           ✅
GET  /api/ligands/list                    ✅
POST /api/results/scan (data/dock)        ✅ (200 runs)
GET  /api/reports/list (data/dock)        ✅
GET  /api/reports/images (...)            ✅
GET  /api/reports/image/{path}            ✅
GET  /api/run/recent                      ✅
POST /api/mode                            ✅
```

---

## 13. Frontend ↔ Backend Etkileşim Şeması

```
Tarayıcı (app.js)
│
├── Mod Değişikliği
│   └── POST /api/mode → STATE.mode güncellenir
│
├── Docking Modu
│   ├── GET  /api/ligands/list         → Ligand dropdown
│   ├── POST /api/receptors/load       → PDB fetch (RCSB'den)
│   ├── GET  /api/receptors/{id}       → 3D viewer için PDB text
│   ├── POST /api/ligands/select       → selection_map güncellenir
│   ├── POST /api/queue/build      ⚠️  → Queue oluşturulur
│   ├── POST /api/run/start            → manifest.tsv + run1.sh
│   ├── GET  /api/run/status (polling) → log + progress
│   └── POST /api/run/stop             → SIGTERM/SIGKILL
│
├── Results Modu
│   ├── POST /api/results/scan         → run listesi + averages tablosu
│   ├── POST /api/results/detail       → PLIP interactions + poses
│   └── GET  /api/results/file?path=   → PDB/SDF dosyası
│
├── Report Modu
│   ├── GET  /api/reports/list         → receptor/ligand/run ağacı
│   ├── GET  /api/reports/images       → üretilmiş imajlar
│   ├── POST /api/reports/graphs       → grafik üretimi (arka plan)
│   ├── POST /api/reports/render       → 3D render (arka plan)
│   ├── POST /api/reports/compile      → .docx derle
│   ├── GET  /api/reports/doc          → .docx indir
│   └── GET  /api/reports/status       → arka plan görev durumu
│
└── Config Modu
    ├── POST /api/config/save          → Excel'den config yükle
    ├── GET  /api/config/load          → Aktif config'i al
    └── POST /api/config/update        → Config güncelle
```

---

## 14. Bağımlılıklar

| Paket | Kullanım |
|---|---|
| `fastapi` | Web framework, routing |
| `uvicorn` | ASGI sunucu |
| `pydantic` | Veri doğrulama modelleri |
| `pandas` | Rapor veri işleme, Excel okuma |
| `requests` | RCSB PDB API'dan PDB dosyası indirme |
| `python-docx` | `.docx` rapor oluşturma |
| `jinja2` | HTML template rendering |
| `python-multipart` | Dosya upload desteği |

---

*Bu döküman DockUP refactoring sonrası oluşturulmuştur. Yeni endpoint eklendiğinde veya mevcut bir endpoint değiştirildiğinde bu dosyayı güncelleyin.*
