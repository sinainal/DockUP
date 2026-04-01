from __future__ import annotations

import json
import importlib.metadata
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from ..helpers import find_identical_file_by_bytes as _shared_find_identical_file_by_bytes
from ..helpers import next_available_ligand_path as _shared_next_available_ligand_path
from ..helpers import normalize_ligand_db_filename as _shared_normalize_ligand_db_filename
from ..state import STATE, save_state_cache

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR
while PROJECT_ROOT != PROJECT_ROOT.parent and not (PROJECT_ROOT / "convert_3D.py").exists():
    PROJECT_ROOT = PROJECT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

GENERATED_DIR = APP_DIR / "generated"
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
try:
    from ..config import LIGAND_DIR as LIGAND_DB_DIR
except Exception:
    LIGAND_DB_DIR = PROJECT_ROOT / "docking_app" / "workspace" / "data" / "ligand"
LIGAND_DB_DIR.mkdir(parents=True, exist_ok=True)

CHEMBL_BASE_URL = "https://www.ebi.ac.uk/chembl/api/data"
PUBCHEM_AUTOCOMPLETE_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/autocomplete/compound"
PUBCHEM_PUG_BASE_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
CACHE_TTL_SECONDS = 180
CACHE_MAX_KEYS = 512
API_CACHE: dict[str, tuple[float, Any]] = {}


@lru_cache(maxsize=1)
def _load_converter_functions() -> tuple[Any, Any]:
    try:
        numpy_ver = importlib.metadata.version("numpy")
    except importlib.metadata.PackageNotFoundError:
        numpy_ver = ""
    try:
        rdkit_ver = importlib.metadata.version("rdkit")
    except importlib.metadata.PackageNotFoundError:
        rdkit_ver = ""
    try:
        numpy_major = int(str(numpy_ver).split(".", 1)[0])
    except (TypeError, ValueError):
        numpy_major = 0
    rdkit_legacy = str(rdkit_ver).startswith(("2020.", "2021.", "2022.", "2023."))
    if numpy_major >= 2 and rdkit_legacy:
        raise RuntimeError(
            "Incompatible environment detected for 3D conversion: "
            f"numpy={numpy_ver}, rdkit={rdkit_ver}. "
            "Use numpy<2 (e.g. 1.26.x) or upgrade RDKit to a NumPy 2 compatible build."
        )

    try:
        from convert_3D import build_oligomer_smiles, smiles_to_3d_sdf  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "3D conversion backend unavailable (likely RDKit/Numpy mismatch). "
            f"Original error: {exc}"
        ) from exc
    return build_oligomer_smiles, smiles_to_3d_sdf

app = FastAPI(title="Ligand 3D Prototype")
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


class ConvertPayload(BaseModel):
    smiles: str
    name: str = ""
    file_stem: str = ""


class OligomerBuildItem(BaseModel):
    primary_id: str = ""
    name: str = ""
    smiles: str
    count: int = 1


class OligomerBuildPayload(BaseModel):
    items: list[OligomerBuildItem] = []


class AddLigandsPayload(BaseModel):
    file_names: list[str] = []


class DeleteLigandPayload(BaseModel):
    name: str = ""


class DeleteFilesPayload(BaseModel):
    file_names: list[str] = []


def _resolve_generated_file(raw_name: str) -> Path:
    safe_rel = Path(str(raw_name or "").replace("\\", "/"))
    if safe_rel.is_absolute():
        raise HTTPException(status_code=400, detail=f"Invalid file path: {raw_name}")
    file_path = (GENERATED_DIR / safe_rel).resolve()
    if GENERATED_DIR.resolve() not in file_path.parents:
        raise HTTPException(status_code=400, detail=f"Invalid file path: {raw_name}")
    return file_path


def _chembl_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Ligand3DPrototype/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def _http_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Ligand3DPrototype/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def _normalize_limit(raw: int, default: int = 20, min_v: int = 1, max_v: int = 100) -> int:
    try:
        val = int(raw)
    except (TypeError, ValueError):
        val = default
    return max(min_v, min(max_v, val))


def _safe_stem(raw: str, fallback: str = "ligand") -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", str(raw or "").strip()).strip("._")
    return base or fallback


def _next_available_path(directory: Path, filename: str) -> Path:
    return _shared_next_available_ligand_path(directory, filename)


def _normalize_ligand_db_filename(filename: str) -> str:
    return _shared_normalize_ligand_db_filename(filename)


def _cleanup_ligand_db_names() -> int:
    renamed = 0
    for path in sorted(LIGAND_DB_DIR.glob("*.sdf"), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        normalized_name = _normalize_ligand_db_filename(path.name)
        if normalized_name == path.name:
            continue
        target_path = _next_available_path(LIGAND_DB_DIR, normalized_name)
        path.rename(target_path)
        renamed += 1
    return renamed


def _list_docking_db_ligands() -> list[str]:
    return sorted([p.name for p in LIGAND_DB_DIR.glob("*.sdf") if p.is_file()], key=lambda x: x.lower())


def _oligomer_label(count: int) -> str:
    names = {
        1: "monomer",
        2: "dimer",
        3: "trimer",
        4: "tetramer",
        5: "pentamer",
        6: "hexamer",
        7: "heptamer",
        8: "octamer",
        9: "nonamer",
        10: "decamer",
    }
    return names.get(count, f"{count}-mer")


def _normalize_molecule_row(row: dict[str, Any]) -> dict[str, Any]:
    structures = row.get("molecule_structures") or {}
    smiles = str(structures.get("canonical_smiles") or "").strip()
    return {
        "chembl_id": str(row.get("molecule_chembl_id") or ""),
        "name": str(row.get("pref_name") or "").strip(),
        "smiles": smiles,
        "max_phase": row.get("max_phase"),
        "molecule_type": str(row.get("molecule_type") or ""),
    }


def _clone_data(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _cache_get(key: str) -> Any | None:
    row = API_CACHE.get(key)
    if not row:
        return None
    stamp, data = row
    if time.time() - stamp > CACHE_TTL_SECONDS:
        API_CACHE.pop(key, None)
        return None
    return _clone_data(data)


def _cache_set(key: str, value: Any) -> None:
    if len(API_CACHE) >= CACHE_MAX_KEYS:
        oldest_key = min(API_CACHE.keys(), key=lambda item: API_CACHE[item][0])
        API_CACHE.pop(oldest_key, None)
    API_CACHE[key] = (time.time(), _clone_data(value))


def _normalize_sources(raw_sources: str) -> list[str]:
    allowed = {"chembl", "pubchem"}
    requested = [item.strip().lower() for item in str(raw_sources or "").split(",") if item.strip()]
    if not requested:
        requested = ["chembl", "pubchem"]
    out: list[str] = []
    for item in requested:
        if item in allowed and item not in out:
            out.append(item)
    return out or ["chembl", "pubchem"]


def _normalize_chembl_result_row(row: dict[str, Any], *, similarity: Any = None) -> dict[str, Any]:
    normalized = _normalize_molecule_row(row)
    chembl_id = str(normalized.get("chembl_id") or "").strip()
    name = str(normalized.get("name") or "").strip()
    smiles = str(normalized.get("smiles") or "").strip()
    return {
        "source": "chembl",
        "primary_id": chembl_id or "-",
        "chembl_id": chembl_id,
        "pubchem_cid": "",
        "name": name,
        "smiles": smiles,
        "molecule_type": str(normalized.get("molecule_type") or ""),
        "similarity": similarity if similarity not in (None, "") else "",
        "detail_url": f"https://www.ebi.ac.uk/chembl/compound_report_card/{chembl_id}/" if chembl_id else "",
    }


def _pubchem_autocomplete_terms(query: str, limit: int) -> list[str]:
    encoded = urllib.parse.quote(str(query or "").strip())
    url = f"{PUBCHEM_AUTOCOMPLETE_URL}/{encoded}/JSON?limit={_normalize_limit(limit, default=10, min_v=1, max_v=50)}"
    data = _http_json(url)
    terms = data.get("dictionary_terms", {}).get("compound", [])
    return [str(item).strip() for item in terms if str(item).strip()]


def _pubchem_first_cid_by_name(name: str) -> int | None:
    encoded = urllib.parse.quote(str(name or "").strip())
    if not encoded:
        return None
    url = f"{PUBCHEM_PUG_BASE_URL}/compound/name/{encoded}/cids/JSON"
    try:
        data = _http_json(url)
    except urllib.error.HTTPError:
        return None
    cids = data.get("IdentifierList", {}).get("CID", [])
    if not cids:
        return None
    try:
        return int(cids[0])
    except (TypeError, ValueError):
        return None


def _pubchem_properties_by_name(name: str) -> list[dict[str, Any]]:
    encoded = urllib.parse.quote(str(name or "").strip())
    if not encoded:
        return []
    url = (
        f"{PUBCHEM_PUG_BASE_URL}/compound/name/{encoded}/property/"
        "CanonicalSMILES,IsomericSMILES,IUPACName,Title/JSON"
    )
    try:
        data = _http_json(url)
    except urllib.error.HTTPError:
        return []
    return data.get("PropertyTable", {}).get("Properties", []) or []


def _pubchem_properties_by_cids(cids: list[int]) -> list[dict[str, Any]]:
    clean_cids = []
    for cid in cids:
        try:
            num = int(cid)
        except (TypeError, ValueError):
            continue
        if num > 0 and num not in clean_cids:
            clean_cids.append(num)
    if not clean_cids:
        return []
    cid_part = ",".join(str(cid) for cid in clean_cids)
    url = (
        f"{PUBCHEM_PUG_BASE_URL}/compound/cid/{cid_part}/property/"
        "CanonicalSMILES,IsomericSMILES,IUPACName,Title/JSON"
    )
    try:
        data = _http_json(url)
    except urllib.error.HTTPError:
        return []
    return data.get("PropertyTable", {}).get("Properties", []) or []


def _normalize_pubchem_property_row(row: dict[str, Any], *, similarity: Any = None) -> dict[str, Any]:
    cid = row.get("CID")
    try:
        cid_num = int(cid)
    except (TypeError, ValueError):
        cid_num = 0
    smiles = str(
        row.get("CanonicalSMILES")
        or row.get("IsomericSMILES")
        or row.get("SMILES")
        or row.get("ConnectivitySMILES")
        or ""
    ).strip()
    name = str(row.get("Title") or row.get("IUPACName") or f"CID {cid_num}" if cid_num else "").strip()
    return {
        "source": "pubchem",
        "primary_id": f"CID {cid_num}" if cid_num else "-",
        "chembl_id": "",
        "pubchem_cid": str(cid_num) if cid_num else "",
        "name": name,
        "smiles": smiles,
        "molecule_type": "small_molecule",
        "similarity": similarity if similarity not in (None, "") else "",
        "detail_url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid_num}" if cid_num else "",
    }


def _dedupe_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        source = str(row.get("source") or "").strip().lower()
        rid = str(row.get("primary_id") or "").strip().lower()
        key = (source, rid)
        if not source or not rid or key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _round_robin_source_merge(
    source_to_rows: dict[str, list[dict[str, Any]]],
    source_order: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    queues: dict[str, list[dict[str, Any]]] = {
        source: list(source_to_rows.get(source) or [])
        for source in source_order
    }
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    while len(out) < limit and any(queues.get(src) for src in source_order):
        progressed = False
        for source in source_order:
            rows = queues.get(source) or []
            while rows:
                row = rows.pop(0)
                rid = str(row.get("primary_id") or "").strip().lower()
                if not rid:
                    continue
                key = (source, rid)
                if key in seen:
                    continue
                seen.add(key)
                out.append(row)
                progressed = True
                break
            queues[source] = rows
            if len(out) >= limit:
                break
        if not progressed:
            break
    return out


def _chembl_search_rows(query: str, limit: int) -> list[dict[str, Any]]:
    cache_key = f"chembl_search:{query.lower()}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    params = urllib.parse.urlencode({"q": query, "limit": limit})
    url = f"{CHEMBL_BASE_URL}/molecule/search.json?{params}"
    data = _chembl_json(url)
    molecules = data.get("molecules") or []
    rows = [_normalize_chembl_result_row(item) for item in molecules]
    rows = [row for row in rows if row.get("primary_id") or row.get("smiles")]
    _cache_set(cache_key, rows)
    return rows


def _chembl_similar_rows(smiles: str, threshold: int, limit: int) -> list[dict[str, Any]]:
    cache_key = f"chembl_sim:{smiles}:{threshold}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    encoded_smiles = urllib.parse.quote(smiles, safe="")
    params = urllib.parse.urlencode({"limit": limit})
    url = f"{CHEMBL_BASE_URL}/similarity/{encoded_smiles}/{threshold}.json?{params}"
    data = _chembl_json(url)
    molecules = data.get("molecules") or []
    rows = [_normalize_chembl_result_row(item, similarity=item.get("similarity")) for item in molecules]
    rows = [row for row in rows if row.get("primary_id") or row.get("smiles")]
    _cache_set(cache_key, rows)
    return rows


def _pubchem_search_rows(query: str, limit: int) -> list[dict[str, Any]]:
    cache_key = f"pubchem_search:{query.lower()}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    collected: list[dict[str, Any]] = []
    seen_cids: set[int] = set()

    # Exact name lookup first; catches cases like "propylene".
    exact_rows = _pubchem_properties_by_name(query)
    for row in exact_rows:
        normalized = _normalize_pubchem_property_row(row)
        cid = int(normalized["pubchem_cid"] or 0)
        if cid <= 0 or cid in seen_cids:
            continue
        seen_cids.add(cid)
        collected.append(normalized)
        if len(collected) >= limit:
            _cache_set(cache_key, collected[:limit])
            return collected[:limit]

    # Then autocomplete terms + CID resolution.
    # Keep this small for responsiveness in manual-search mode.
    autocomplete_limit = max(3, min(5, limit))
    terms = _pubchem_autocomplete_terms(query, limit=autocomplete_limit)
    cids: list[int] = []
    cid_target = max(1, min(limit, 3))
    for term in terms:
        cid = _pubchem_first_cid_by_name(term)
        if cid is None or cid in seen_cids:
            continue
        seen_cids.add(cid)
        cids.append(cid)
        if len(cids) >= cid_target:
            break

    if cids:
        prop_rows = _pubchem_properties_by_cids(cids)
        for row in prop_rows:
            normalized = _normalize_pubchem_property_row(row)
            cid = int(normalized["pubchem_cid"] or 0)
            if cid <= 0:
                continue
            if all(int(item.get("pubchem_cid") or 0) != cid for item in collected):
                collected.append(normalized)
            if len(collected) >= limit:
                break

    collected = collected[:limit]
    _cache_set(cache_key, collected)
    return collected


def _pubchem_similar_rows(smiles: str, threshold: int, limit: int) -> list[dict[str, Any]]:
    cache_key = f"pubchem_sim:{smiles}:{threshold}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    encoded = urllib.parse.quote(smiles, safe="")
    url = (
        f"{PUBCHEM_PUG_BASE_URL}/compound/fastsimilarity_2d/smiles/{encoded}/cids/JSON?"
        f"Threshold={threshold}&MaxRecords={limit}"
    )
    try:
        data = _http_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            _cache_set(cache_key, [])
            return []
        raise
    cids_raw = data.get("IdentifierList", {}).get("CID", [])
    cids: list[int] = []
    for item in cids_raw:
        try:
            cid = int(item)
        except (TypeError, ValueError):
            continue
        if cid > 0 and cid not in cids:
            cids.append(cid)
        if len(cids) >= limit:
            break
    if not cids:
        _cache_set(cache_key, [])
        return []
    prop_rows = _pubchem_properties_by_cids(cids)
    rows = [_normalize_pubchem_property_row(item, similarity=f">={threshold}") for item in prop_rows]
    rows = [row for row in rows if row.get("primary_id") or row.get("smiles")]
    _cache_set(cache_key, rows[:limit])
    return rows[:limit]


def _list_generated_files() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in GENERATED_DIR.rglob("*.sdf"):
        if not path.is_file():
            continue
        stat = path.stat()
        rel_name = str(path.relative_to(GENERATED_DIR)).replace("\\", "/")
        rows.append(
            {
                "name": rel_name,
                "size_bytes": int(stat.st_size),
                "mtime": int(stat.st_mtime),
                "download_url": f"/api/files/{urllib.parse.quote(rel_name, safe='')}",
            }
        )
    rows.sort(key=lambda item: (-item["mtime"], item["name"].lower()))
    return rows


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "title": "Ligand Download + 3D Prototype",
        },
    )


@app.get("/api/chembl/search")
def chembl_search(
    q: str = Query("", min_length=1),
    limit: int = Query(5),
) -> dict[str, Any]:
    query = str(q or "").strip()
    if len(query) < 2:
        raise HTTPException(status_code=400, detail="Query must be at least 2 characters.")
    normalized_limit = _normalize_limit(limit, default=5, min_v=1, max_v=25)
    try:
        rows = _chembl_search_rows(query, normalized_limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ChEMBL search failed: {exc}") from exc
    results = [
        {
            "chembl_id": row.get("chembl_id", ""),
            "name": row.get("name", ""),
            "smiles": row.get("smiles", ""),
            "max_phase": "",
            "molecule_type": row.get("molecule_type", ""),
            "source": row.get("source", "chembl"),
            "primary_id": row.get("primary_id", ""),
            "detail_url": row.get("detail_url", ""),
        }
        for row in rows
    ]
    return {
        "query": query,
        "count": len(results),
        "results": results,
    }


@app.get("/api/chembl/similar")
def chembl_similar(
    smiles: str = Query("", min_length=1),
    threshold: int = Query(70),
    limit: int = Query(10),
) -> dict[str, Any]:
    smiles_text = str(smiles or "").strip()
    if not smiles_text:
        raise HTTPException(status_code=400, detail="SMILES is required.")
    normalized_threshold = _normalize_limit(threshold, default=70, min_v=40, max_v=100)
    normalized_limit = _normalize_limit(limit, default=20, min_v=1, max_v=100)
    try:
        rows = _chembl_similar_rows(smiles_text, normalized_threshold, normalized_limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ChEMBL similarity failed: {exc}") from exc
    results = [
        {
            "chembl_id": row.get("chembl_id", ""),
            "name": row.get("name", ""),
            "smiles": row.get("smiles", ""),
            "molecule_type": row.get("molecule_type", ""),
            "similarity": row.get("similarity", ""),
            "source": row.get("source", "chembl"),
            "primary_id": row.get("primary_id", ""),
            "detail_url": row.get("detail_url", ""),
        }
        for row in rows
    ]

    return {
        "input_smiles": smiles_text,
        "threshold": normalized_threshold,
        "count": len(results),
        "results": results,
    }


@app.get("/api/pubchem/search")
def pubchem_search(
    q: str = Query("", min_length=1),
    limit: int = Query(5),
) -> dict[str, Any]:
    query = str(q or "").strip()
    if len(query) < 2:
        raise HTTPException(status_code=400, detail="Query must be at least 2 characters.")
    normalized_limit = _normalize_limit(limit, default=5, min_v=1, max_v=25)
    try:
        results = _pubchem_search_rows(query, normalized_limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"PubChem search failed: {exc}") from exc
    return {
        "query": query,
        "count": len(results),
        "results": results,
    }


@app.get("/api/pubchem/similar")
def pubchem_similar(
    smiles: str = Query("", min_length=1),
    threshold: int = Query(80),
    limit: int = Query(10),
) -> dict[str, Any]:
    smiles_text = str(smiles or "").strip()
    if not smiles_text:
        raise HTTPException(status_code=400, detail="SMILES is required.")
    normalized_threshold = _normalize_limit(threshold, default=80, min_v=40, max_v=100)
    normalized_limit = _normalize_limit(limit, default=20, min_v=1, max_v=100)
    try:
        results = _pubchem_similar_rows(smiles_text, normalized_threshold, normalized_limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"PubChem similarity failed: {exc}") from exc
    return {
        "input_smiles": smiles_text,
        "threshold": normalized_threshold,
        "count": len(results),
        "results": results,
    }


@app.get("/api/search")
def search_all(
    q: str = Query("", min_length=1),
    limit: int = Query(5),
    sources: str = Query("chembl,pubchem"),
) -> dict[str, Any]:
    query = str(q or "").strip()
    if len(query) < 2:
        raise HTTPException(status_code=400, detail="Query must be at least 2 characters.")
    normalized_limit = _normalize_limit(limit, default=5, min_v=1, max_v=25)
    selected_sources = _normalize_sources(sources)

    source_to_rows: dict[str, list[dict[str, Any]]] = {source: [] for source in selected_sources}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(4, len(selected_sources)))) as pool:
        future_to_source = {}
        for source in selected_sources:
            if source == "chembl":
                future_to_source[pool.submit(_chembl_search_rows, query, normalized_limit)] = source
            elif source == "pubchem":
                future_to_source[pool.submit(_pubchem_search_rows, query, normalized_limit)] = source
        for future in as_completed(future_to_source):
            source = future_to_source[future]
            try:
                source_to_rows[source] = future.result()
            except Exception as exc:
                errors[source] = str(exc)
                source_to_rows[source] = []

    results = _round_robin_source_merge(source_to_rows, selected_sources, normalized_limit)
    if not results:
        fallback_rows: list[dict[str, Any]] = []
        for source in selected_sources:
            fallback_rows.extend(source_to_rows.get(source, []))
        results = _dedupe_rows(fallback_rows, normalized_limit)
    count_by_source = {"chembl": 0, "pubchem": 0}
    for row in results:
        src = str(row.get("source") or "").lower()
        if src in count_by_source:
            count_by_source[src] += 1

    if not results and errors:
        raise HTTPException(status_code=502, detail=f"Search failed: {errors}")
    return {
        "query": query,
        "sources": selected_sources,
        "count": len(results),
        "count_by_source": count_by_source,
        "errors": errors,
        "results": results,
    }


@app.get("/api/similar")
def similar_all(
    smiles: str = Query("", min_length=1),
    threshold: int = Query(80),
    limit: int = Query(10),
    sources: str = Query("chembl,pubchem"),
) -> dict[str, Any]:
    smiles_text = str(smiles or "").strip()
    if not smiles_text:
        raise HTTPException(status_code=400, detail="SMILES is required.")
    normalized_threshold = _normalize_limit(threshold, default=80, min_v=40, max_v=100)
    normalized_limit = _normalize_limit(limit, default=10, min_v=1, max_v=25)
    selected_sources = _normalize_sources(sources)

    source_to_rows: dict[str, list[dict[str, Any]]] = {source: [] for source in selected_sources}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(4, len(selected_sources)))) as pool:
        future_to_source = {}
        for source in selected_sources:
            if source == "chembl":
                future_to_source[
                    pool.submit(_chembl_similar_rows, smiles_text, normalized_threshold, normalized_limit)
                ] = source
            elif source == "pubchem":
                future_to_source[
                    pool.submit(_pubchem_similar_rows, smiles_text, normalized_threshold, normalized_limit)
                ] = source
        for future in as_completed(future_to_source):
            source = future_to_source[future]
            try:
                source_to_rows[source] = future.result()
            except Exception as exc:
                errors[source] = str(exc)
                source_to_rows[source] = []

    results = _round_robin_source_merge(source_to_rows, selected_sources, normalized_limit)
    if not results:
        fallback_rows: list[dict[str, Any]] = []
        for source in selected_sources:
            fallback_rows.extend(source_to_rows.get(source, []))
        results = _dedupe_rows(fallback_rows, normalized_limit)
    count_by_source = {"chembl": 0, "pubchem": 0}
    for row in results:
        src = str(row.get("source") or "").lower()
        if src in count_by_source:
            count_by_source[src] += 1

    if not results and errors:
        raise HTTPException(status_code=502, detail=f"Similarity search failed: {errors}")
    return {
        "input_smiles": smiles_text,
        "threshold": normalized_threshold,
        "sources": selected_sources,
        "count": len(results),
        "count_by_source": count_by_source,
        "errors": errors,
        "results": results,
    }


@app.post("/api/oligomers/build")
def build_oligomers(payload: OligomerBuildPayload) -> dict[str, Any]:
    items = payload.items or []
    if not items:
        raise HTTPException(status_code=400, detail="No items provided.")
    try:
        build_oligomer_smiles, smiles_to_3d_sdf = _load_converter_functions()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    generated_rows: list[dict[str, Any]] = []
    errors: list[str] = []

    for item in items:
        smiles_text = str(item.smiles or "").strip()
        if not smiles_text:
            errors.append(f"{item.name or item.primary_id or 'item'}: missing SMILES")
            continue
        count = _normalize_limit(item.count, default=1, min_v=1, max_v=10)
        base_name = item.name or item.primary_id or "ligand"
        safe_stem = _safe_stem(base_name)
        oligomer_label = _oligomer_label(count)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        out_name = f"{safe_stem}_{oligomer_label}_{timestamp}_{time.time_ns() % 100000}.sdf"
        out_path = GENERATED_DIR / out_name

        try:
            oligomer_smiles = build_oligomer_smiles(smiles_text, count)
            saved = Path(smiles_to_3d_sdf(oligomer_smiles, out_path)).resolve()
            if GENERATED_DIR.resolve() not in saved.parents:
                raise RuntimeError("Generated file outside workspace.")
            generated_rows.append(
                {
                    "name": saved.name,
                    "download_url": f"/api/files/{urllib.parse.quote(saved.name, safe='')}",
                    "source_name": base_name,
                    "primary_id": str(item.primary_id or ""),
                    "smiles": smiles_text,
                    "oligomer_count": count,
                    "oligomer_label": oligomer_label,
                }
            )
        except Exception as exc:
            errors.append(f"{base_name}: {exc}")

    if not generated_rows and errors:
        raise HTTPException(status_code=400, detail=f"Oligomer generation failed: {' | '.join(errors)}")

    return {
        "ok": True,
        "generated_count": len(generated_rows),
        "generated": generated_rows,
        "errors": errors,
    }


@app.post("/api/ligands/add")
def add_ligands(payload: AddLigandsPayload) -> dict[str, Any]:
    names = [str(item or "").strip() for item in (payload.file_names or []) if str(item or "").strip()]
    if not names:
        raise HTTPException(status_code=400, detail="No file names provided.")
    _cleanup_ligand_db_names()

    copied: list[str] = []
    duplicates: list[str] = []
    missing: list[str] = []
    for raw_name in names:
        safe_name = Path(raw_name).name
        source_path = (GENERATED_DIR / safe_name).resolve()
        if GENERATED_DIR.resolve() not in source_path.parents:
            missing.append(raw_name)
            continue
        if not source_path.exists() or not source_path.is_file():
            missing.append(raw_name)
            continue
        source_bytes = source_path.read_bytes()
        existing_path = _shared_find_identical_file_by_bytes(
            LIGAND_DB_DIR,
            source_bytes,
            suffixes=(".sdf",),
            preferred_name=safe_name,
        )
        if existing_path is not None:
            duplicates.append(existing_path.name)
            continue
        normalized_name = _normalize_ligand_db_filename(safe_name)
        target_path = _next_available_path(LIGAND_DB_DIR, normalized_name)
        shutil.copy2(source_path, target_path)
        copied.append(target_path.name)

    ligands = _list_docking_db_ligands()
    return {
        "ok": True,
        "copied_count": len(copied),
        "copied": copied,
        "duplicates": duplicates,
        "missing": missing,
        "ligands_path": str(LIGAND_DB_DIR.resolve()),
        "ligands": ligands,
    }


@app.post("/api/ligands/delete")
def delete_ligand(payload: DeleteLigandPayload) -> dict[str, Any]:
    name = str(payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Ligand name is required.")
    _cleanup_ligand_db_names()

    target = (LIGAND_DB_DIR / Path(name).name).resolve()
    if LIGAND_DB_DIR.resolve() not in target.parents or target.suffix.lower() != ".sdf":
        raise HTTPException(status_code=400, detail="Invalid ligand name.")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Ligand not found.")

    target.unlink(missing_ok=False)
    deleted_name = target.name
    STATE["active_ligands"] = [item for item in STATE.get("active_ligands", []) if str(item or "").strip() != deleted_name]
    if str(STATE.get("selected_ligand") or "").strip() == deleted_name:
        STATE["selected_ligand"] = ""
    selection_map = STATE.get("selection_map")
    if isinstance(selection_map, dict):
        for row in selection_map.values():
            if not isinstance(row, dict):
                continue
            if str(row.get("ligand_resname") or "").strip() == deleted_name:
                row["ligand_resname"] = ""
    save_state_cache()
    ligands = _list_docking_db_ligands()
    return {
        "ok": True,
        "deleted": deleted_name,
        "count": len(ligands),
        "ligands": ligands,
        "ligands_path": str(LIGAND_DB_DIR.resolve()),
    }


@app.get("/api/ligands/database")
def ligand_database() -> dict[str, Any]:
    _cleanup_ligand_db_names()
    ligands = _list_docking_db_ligands()
    return {
        "count": len(ligands),
        "ligands": ligands,
        "ligands_path": str(LIGAND_DB_DIR.resolve()),
    }


@app.post("/api/convert3d")
def convert_3d(payload: ConvertPayload) -> dict[str, Any]:
    smiles_text = str(payload.smiles or "").strip()
    if not smiles_text:
        raise HTTPException(status_code=400, detail="SMILES is required for conversion.")
    try:
        _, smiles_to_3d_sdf = _load_converter_functions()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    base_name = payload.file_stem or payload.name or "ligand"
    stem = _safe_stem(base_name)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    output_file = GENERATED_DIR / f"{stem}_{timestamp}.sdf"

    try:
        saved_path = Path(smiles_to_3d_sdf(smiles_text, output_file)).resolve()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"3D conversion failed: {exc}") from exc

    if saved_path.parent != GENERATED_DIR.resolve() and GENERATED_DIR.resolve() not in saved_path.parents:
        raise HTTPException(status_code=500, detail="Converted file is outside generated folder.")

    return {
        "ok": True,
        "name": saved_path.name,
        "download_url": f"/api/files/{urllib.parse.quote(saved_path.name)}",
    }


@app.get("/api/files")
def list_files() -> dict[str, Any]:
    files = _list_generated_files()
    return {
        "count": len(files),
        "files": files,
    }


@app.post("/api/files/delete")
def delete_files(payload: DeleteFilesPayload) -> dict[str, Any]:
    names = [str(item or "").strip() for item in (payload.file_names or []) if str(item or "").strip()]
    if not names:
        raise HTTPException(status_code=400, detail="No file names provided.")

    deleted: list[str] = []
    missing: list[str] = []
    for raw_name in names:
        try:
            path = _resolve_generated_file(raw_name)
        except HTTPException:
            missing.append(raw_name)
            continue
        if not path.exists() or not path.is_file():
            missing.append(raw_name)
            continue
        path.unlink(missing_ok=False)
        rel_name = str(path.relative_to(GENERATED_DIR)).replace("\\", "/")
        deleted.append(rel_name)

    return {
        "ok": True,
        "deleted_count": len(deleted),
        "deleted": deleted,
        "missing": missing,
    }


@app.get("/api/files/{name:path}")
def download_file(name: str):
    file_path = _resolve_generated_file(name)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(
        str(file_path),
        media_type="chemical/x-mdl-sdfile",
        filename=file_path.name,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8090)
