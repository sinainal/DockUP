# DockUP Test Design (Sade Yapi)

Testler 3 katmana ayrildi:

- `tests/unit` (mevcut unit dosyalari): saf Python mantigi.
- `tests/api`: FastAPI odakli API contract + workflow testleri.
- `tests/e2e`: gercek akis simule eden uzun sureli testler.

## Katmanlar

`tests/api/test_fastapi_contract.py`
- OpenAPI, content-type, FastAPI error shape (`detail`) gibi API sozlesmesi.

`tests/api/test_fastapi_workflow.py`
- FastAPI uzerinden profesyonel temel akis:
- mode switch
- receptor add/select/detail
- ligand upload + active pool + queue/build sozlesmesi

`tests/e2e/test_basic_docking_flow.py`
- Hizli basic docking E2E:
- receptor -> ligand -> grid -> queue -> run -> artifact dogrulamasi

`tests/e2e/test_full_report_flow.py`
- Tam rapor akis E2E:
- plot (`/api/reports/graphs`) + render (`/api/reports/render`) + image serving

## Legacy Suite

- `tests/test_api.py` ve `tests/test2.py` korunuyor ama `legacy` marker altinda.
- Varsayilan calistirmada skip edilir.
- Calistirmak icin: `--run-legacy`

## Ortak Altyapi

- `tests/conftest.py`: ortak fixture/config/marker yonetimi.
- `tests/_support/api_client.py`: tek tip HTTP helper.
- `tests/_support/e2e_flow.py`: paylasilan E2E util fonksiyonlari.

## Calistirma

Sadece yeni FastAPI API katmani:

```bash
pytest -q tests/api -m api
```

Sadece yeni E2E katmani:

```bash
pytest -q tests/e2e -m e2e
```

Tum yeni katmanlar (legacy haric):

```bash
pytest -q -m "not legacy"
```

Legacy dahil tumu:

```bash
pytest -q --run-legacy
```
