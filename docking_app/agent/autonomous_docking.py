from __future__ import annotations

import json
import re
import shutil
import time
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi import HTTPException

from ..config import DOCK_DIR, LIGAND_DIR, RECEPTOR_DIR
from ..helpers import normalize_docking_config, normalize_ligand_db_filename, resolve_dock_directory, to_display_path
from ..manifest import materialize_queue_runs
from ..pocket_finder import compute_gridbox_for_pocket, get_runtime_state, latest_output_dir, run_p2rank_async
from ..services import _build_queue, _existing_files, _init_selection_map, _load_receptor_meta, _normalize_receptor_id
from ..state import RUN_STATE, STATE, _normalize_selection_map, save_state_cache


SYSTEM_PROMPT = """You are DockUP Docking Agent.
Be concise, direct, and outcome-focused. Do not narrate internal reasoning.
Use returned tool state as evidence. Preserve user-provided receptor, ligand, file, and setting names; if a required value is missing or a tool cannot resolve it, ask briefly instead of inventing data.
If the request is about state, queue, run status, or gridbox summary, inspect state first instead of guessing.
For docking work, keep the workflow short and purposeful: use the minimal missing prerequisites, avoid repeating a satisfied stage, and move from assets to workspace to gridbox to configuration to queue/run only as needed.
For multi-ligand work, keep dock_ligands as "all" unless the user restricts the ligand set; DockUP expands active ligand files during validation.
For gridboxes, prefer the main native ligand first and ignore helper ions or solvent-like residues such as CL, NA, HOH, WAT, SO4, PO4, GOL, PEG, or EDO when a better native ligand exists.
If no usable native ligand exists, switch to P2Rank/gridfinder mode and keep the user informed with a short live status message.
If fetch_assets fails, retry once with the cleanest obvious alternative from retry_attempts or a corrected spelling; keep the successful assets and do not repeat the same failing name.
Treat run_count as repeated runs per job, not receptor-ligand combinations or total dockings.
For a full docking task, complete the missing prerequisites in a sensible order and do not repeat a satisfied step. Do not call build_or_run_queue before gridbox and docking config exist.
Never return an empty answer. If you cannot act yet, ask a short clarifying question or state the missing input in one sentence.
"""

AGENT_STATE: dict[str, Any] = {
    "inventory": {},
    "setup_rows": [],
    "grid_data": {},
    "batch_config": {},
    "batch_id": "",
}

COMMON_NATIVE_LIGANDS = {
    "ACT",
    "CA",
    "CL",
    "CO",
    "CU",
    "DOD",
    "EDO",
    "FE",
    "FMT",
    "GOL",
    "HOH",
    "IOD",
    "K",
    "MG",
    "MN",
    "NA",
    "NI",
    "PEG",
    "PO4",
    "SO4",
    "WAT",
    "ZN",
}
PDB_ID_RE = r"\b[0-9][A-Za-z0-9]{3}\b"
LIGAND_WORDS_TO_DROP = {
    "a",
    "an",
    "and",
    "against",
    "bana",
    "baslat",
    "başlat",
    "calistir",
    "çalıştır",
    "compound",
    "compounds",
    "dock",
    "docking",
    "eder",
    "et",
    "for",
    "icin",
    "için",
    "ile",
    "in",
    "into",
    "ligand",
    "ligands",
    "misin",
    "molecule",
    "molecules",
    "on",
    "pdb",
    "protein",
    "proteins",
    "receptor",
    "receptors",
    "the",
    "to",
    "ve",
    "with",
    "yap",
}


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "plan_assets",
            "description": "Extract requested receptor PDB ids and ligand names in compact form.",
            "parameters": {
                "type": "object",
                "required": ["receptors", "ligands"],
                "properties": {
                    "receptors": {"type": "string", "description": "Comma-separated PDB ids."},
                    "ligands": {"type": "string", "description": "Semicolon-separated ligand names or ids."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_assets",
            "description": "Download/store receptors and ligands, then return compact inventory.",
            "parameters": {
                "type": "object",
                "required": ["receptors", "ligands"],
                "properties": {
                    "receptors": {"type": "string"},
                    "ligands": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_setup_rows",
            "description": "Submit docking setup rows. You must call this function with the rows string. No docking settings.",
            "parameters": {
                "type": "object",
                "required": ["rows"],
                "properties": {
                    "rows": {
                        "type": "string",
                        "description": "Semicolon-separated rows: receptor,chain,native_ligand,box_size,dock_ligands",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "make_gridboxes",
            "description": "Compute grid centers from main native ligands or P2Rank/gridfinder fallback using setup rows.",
            "parameters": {
                "type": "object",
                "required": ["rows"],
                "properties": {
                    "rows": {
                        "type": "string",
                        "description": "Semicolon-separated rows: receptor,chain,native_ligand,box_size,dock_ligands",
                    },
                    "method": {"type": "string", "description": "native_ligand, current_selection, p2rank, gridfinder, or auto."},
                    "pocket_rank": {"type": "integer", "description": "Pocket rank to use when method falls back to P2Rank."},
                    "p2rank_mode": {"type": "string", "description": "P2Rank box mode: fit or fixed."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_batch_config",
            "description": "Submit final batch config and docking settings. You must call this function after gridboxes exist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "run_count": {
                        "type": "integer",
                        "description": "Repeated runs per receptor-ligand job. Default 1. Do not use total job count here.",
                    },
                    "padding": {"type": "number", "description": "Extra grid padding. Default 0."},
                    "out_root_name": {"type": "string", "description": "Output folder name under data/dock."},
                    "docking_engine": {"type": "string", "description": "Docking engine, e.g. vina_gpu_21 or vina."},
                    "docking_mode": {"type": "string", "description": "standard or flexible."},
                    "ligand_binding_mode": {"type": "string", "description": "single unless multi-ligand mode is explicitly requested."},
                    "pdb2pqr_ph": {"type": "number"},
                    "vina_exhaustiveness": {"type": "integer"},
                    "vina_num_modes": {"type": "integer"},
                    "vina_energy_range": {"type": "number"},
                    "vina_cpu": {"type": "integer"},
                    "vina_seed": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_batch",
            "description": "Validate final batch config and estimate total docking runs.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_queue",
            "description": "Build queue from validated batch config.",
            "parameters": {
                "type": "object",
                "properties": {"replace_queue": {"type": "boolean"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_queue",
            "description": "Start queued batch. Use test_mode for log/test runs.",
            "parameters": {
                "type": "object",
                "properties": {"test_mode": {"type": "boolean"}},
            },
        },
    },
]


def _split_tokens(text: Any, *, separators: str = r"[\s,;]+") -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for token in re.split(separators, str(text or "").strip()):
        value = token.strip()
        if value and value not in seen:
            out.append(value)
            seen.add(value)
    return out


def _split_ligand_specs_text(text: Any) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in raw:
        if char == "[":
            depth += 1
        elif char == "]" and depth > 0:
            depth -= 1
        if char in {";", ","} and depth == 0:
            value = "".join(current).strip()
            if value:
                parts.append(value)
            current = []
            continue
        current.append(char)
    value = "".join(current).strip()
    if value:
        parts.append(value)
    return parts


def _fetch_ligand(identifier: str) -> tuple[str, str]:
    raw = str(identifier or "").strip()
    if not raw:
        return "", "empty ligand"
    local = _resolve_local_ligand(raw)
    if local:
        return local.name, ""
    upper = raw.upper()
    if upper.startswith("CID:"):
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{quote(raw[4:])}/SDF?record_type=3d"
    elif upper.startswith("ID:"):
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{quote(raw[3:])}/SDF?record_type=3d"
    elif raw.isdigit():
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{quote(raw)}/SDF?record_type=3d"
    else:
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{quote(raw)}/SDF?record_type=3d"
    try:
        with urlopen(Request(url), timeout=20) as response:
            content = response.read()
    except Exception as exc:
        return "", f"{raw}: {type(exc).__name__}: {exc}"
    filename = normalize_ligand_db_filename(f"{raw}.sdf".replace(":", "_").replace("/", "_"))
    target = LIGAND_DIR / filename
    idx = 1
    while target.exists() and target.read_bytes() != content:
        target = LIGAND_DIR / f"{Path(filename).stem}_{idx}{Path(filename).suffix}"
        idx += 1
    target.write_bytes(content)
    return target.name, ""


def _pubchem_smiles(identifier: str) -> tuple[str, str]:
    raw = str(identifier or "").strip()
    if not raw:
        return "", "empty ligand"
    upper = raw.upper()
    if upper.startswith("CID:"):
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{quote(raw[4:])}/property/CanonicalSMILES/JSON"
    elif upper.startswith("ID:"):
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{quote(raw[3:])}/property/CanonicalSMILES/JSON"
    elif raw.isdigit():
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{quote(raw)}/property/CanonicalSMILES/JSON"
    else:
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{quote(raw)}/property/CanonicalSMILES/JSON"
    try:
        with urlopen(Request(url), timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return "", f"{raw}: {type(exc).__name__}: {exc}"
    rows = (((data or {}).get("PropertyTable") or {}).get("Properties") or [])
    if not rows:
        return "", f"{raw}: no PubChem SMILES found"
    smiles = str(rows[0].get("CanonicalSMILES") or "").strip()
    return smiles, "" if smiles else f"{raw}: empty PubChem SMILES"


def _ligand_name_candidates(identifier: str) -> list[str]:
    raw = str(identifier or "").strip()
    candidates: list[str] = []
    for value in [
        raw,
        raw.replace("_", " "),
        raw.replace("-", " "),
        raw.replace(" ", "-"),
        raw.replace(" ", "_"),
    ]:
        value = re.sub(r"\s+", " ", value).strip()
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def _fetch_ligand_with_retries(identifier: str) -> tuple[str, str, list[str]]:
    errors: list[str] = []
    tried: list[str] = []
    for candidate in _ligand_name_candidates(identifier):
        tried.append(candidate)
        saved, error = _fetch_ligand(candidate)
        if saved:
            return saved, "", tried
        if error:
            errors.append(error)
    return "", " | ".join(errors) if errors else f"{identifier}: not found", tried


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


def _next_ligand_path(filename: str) -> Path:
    normalized = normalize_ligand_db_filename(filename)
    target = LIGAND_DIR / normalized
    idx = 1
    while target.exists():
        target = LIGAND_DIR / f"{Path(normalized).stem}_{idx}{Path(normalized).suffix}"
        idx += 1
    return target


def _generate_oligomer_ligand(identifier: str, count: int) -> tuple[str, str]:
    smiles, error = _pubchem_smiles(identifier)
    if error or not smiles:
        return "", error or f"{identifier}: missing SMILES"
    try:
        from ..ligand_3d.app import _load_converter_functions

        build_oligomer_smiles, smiles_to_3d_sdf = _load_converter_functions()
        oligomer_smiles = build_oligomer_smiles(smiles, count)
        safe_base = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(identifier or "ligand")).strip("._-") or "ligand"
        target = _next_ligand_path(f"{safe_base}_{_oligomer_label(count)}.sdf")
        saved = Path(smiles_to_3d_sdf(oligomer_smiles, target)).resolve()
        if saved != target.resolve():
            if saved.is_file():
                shutil.copy2(saved, target)
                saved = target.resolve()
        if LIGAND_DIR.resolve() not in saved.parents and saved.parent != LIGAND_DIR.resolve():
            return "", f"{identifier}[{count}]: generated file outside ligand DB"
        return saved.name, ""
    except Exception as exc:
        return "", f"{identifier}[{count}]: {type(exc).__name__}: {exc}"


def _parse_ligand_specs(ligands: str) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for raw in _split_ligand_specs_text(ligands):
        match = re.fullmatch(r"(.+?)\s*\[(\d+)\s*:\s*(\d+)\]", raw)
        if match:
            start = max(1, int(match.group(2)))
            end = max(1, int(match.group(3)))
            if end < start:
                start, end = end, start
            specs.append({"name": match.group(1).strip(), "counts": list(range(start, min(end, 10) + 1)), "raw": raw})
            continue
        match = re.fullmatch(r"(.+?)\s*\[((?:\d+\s*,\s*)*\d+)\]", raw)
        if match:
            counts = []
            for item in match.group(2).split(","):
                value = max(1, min(10, int(item.strip())))
                if value not in counts:
                    counts.append(value)
            specs.append({"name": match.group(1).strip(), "counts": counts or [1], "raw": raw})
            continue
        specs.append({"name": raw, "counts": [1], "raw": raw})
    return specs


def _resolve_local_ligand(identifier: str) -> Path | None:
    raw = str(identifier or "").strip()
    if not raw:
        return None
    candidates = [raw]
    if not Path(raw).suffix:
        candidates.append(f"{raw}.sdf")
    local_files = _existing_files(LIGAND_DIR, (".sdf",))
    by_name = {path.name.lower(): path for path in local_files}
    by_stem = {path.stem.lower(): path for path in local_files}
    for item in candidates:
        direct = Path(item).expanduser()
        if direct.is_file() and direct.suffix.lower() == ".sdf":
            return direct
        found = by_name.get(Path(item).name.lower())
        if found:
            return found
        found = by_stem.get(Path(item).stem.lower())
        if found:
            return found
    normalized = re.sub(r"[^a-z0-9]+", "", raw.lower())
    fuzzy_index: dict[str, Path] = {}
    for path in local_files:
        fuzzy_index[re.sub(r"[^a-z0-9]+", "", path.stem.lower())] = path
        fuzzy_index[re.sub(r"[^a-z0-9]+", "", path.name.lower())] = path
    if normalized:
        matches = get_close_matches(normalized, list(fuzzy_index), n=1, cutoff=0.84)
        if matches:
            return fuzzy_index[matches[0]]
    return None


def _refresh_receptor_state(pdb_ids: list[str]) -> tuple[list[str], list[str]]:
    requested = [_normalize_receptor_id(pid) for pid in pdb_ids if _normalize_receptor_id(pid)]
    existing = {_normalize_receptor_id(row.get("pdb_id")) for row in STATE.get("receptor_meta", [])}
    missing = [pid for pid in requested if pid not in existing]
    failed: list[str] = []
    if missing:
        meta = _load_receptor_meta(missing, _existing_files(RECEPTOR_DIR, (".pdb",)))
        loaded = {_normalize_receptor_id(row.get("pdb_id")) for row in meta}
        failed = [pid for pid in missing if pid not in loaded]
        if meta:
            STATE["receptor_meta"].extend(meta)
            STATE["selection_map"].update(_init_selection_map(meta))
    save_state_cache()
    loaded_now = [_normalize_receptor_id(row.get("pdb_id")) for row in STATE.get("receptor_meta", [])]
    return [pid for pid in requested if pid in loaded_now], failed


def _inventory_for(pdb_ids: list[str], ligand_names: list[str]) -> dict[str, Any]:
    receptors: dict[str, Any] = {}
    wanted = {_normalize_receptor_id(pid) for pid in pdb_ids}
    for meta in STATE.get("receptor_meta", []):
        pdb_id = _normalize_receptor_id(meta.get("pdb_id"))
        if wanted and pdb_id not in wanted:
            continue
        ligands_by_chain = meta.get("ligands_by_chain") if isinstance(meta.get("ligands_by_chain"), dict) else {}
        receptors[pdb_id] = {
            "chains": [str(c) for c in (meta.get("chains") or ["all"])],
            "native_ligands": {
                str(chain): [str(lig) for lig in ligs]
                for chain, ligs in ligands_by_chain.items()
            },
        }
    return {"receptors": receptors, "ligands": ligand_names}


def _compact_inventory(inventory: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {"receptors": {}, "ligands": list(inventory.get("ligands") or [])}
    for pdb_id, receptor in (inventory.get("receptors") or {}).items():
        chains = [c for c in (receptor.get("chains") or []) if c != "all"]
        by_chain = receptor.get("native_ligands") or {}
        compact_ligs: dict[str, list[str]] = {}
        for chain in chains[:4]:
            ligands = list(by_chain.get(chain) or [])
            ranked = sorted(ligands, key=lambda item: _native_ligand_sort_key(pdb_id, chain, item))
            compact_ligs[chain] = ranked[:4]
        compact["receptors"][pdb_id] = {"chains": chains[:4] or ["all"], "native_ligands": compact_ligs}
    return compact


def _suggest_setup_rows(inventory: dict[str, Any], box_size: float = 20.0) -> str:
    rows: list[str] = []
    for pdb_id, receptor in (inventory.get("receptors") or {}).items():
        chains = [c for c in (receptor.get("chains") or []) if c != "all"] or ["all"]
        by_chain = receptor.get("native_ligands") or {}
        best_chain = chains[0]
        best_ligand = ""
        best_score = (True, True, 0, True, "ZZZ", 0, "ZZZ")
        for chain in chains:
            ligands = list(by_chain.get(chain) or by_chain.get("all") or [])
            for ligand in ligands:
                score = _native_ligand_sort_key(pdb_id, chain, ligand)
                if score < best_score:
                    best_score = score
                    best_chain = chain
                    best_ligand = str(ligand)
        if best_ligand:
            rows.append(f"{pdb_id},{best_chain},{best_ligand},{box_size:g},all")
    return ";".join(rows)


def _batch_defaults_from_prompt(prompt: str) -> dict[str, Any]:
    text = str(prompt or "")
    run_count = 1
    match = re.search(r"\brun_count\s*[:=]?\s*(\d+)\b", text, re.IGNORECASE) or re.search(r"\b(\d+)\s+runs?\b", text, re.IGNORECASE)
    if match:
        run_count = max(1, int(match.group(1)))
    padding = 0.0
    match = re.search(r"\bpadding\s*[:=]?\s*(-?\d+(?:\.\d+)?)\b", text, re.IGNORECASE)
    if match:
        padding = max(0.0, float(match.group(1)))
    out_root_name = f"agent_{time.strftime('%Y%m%d_%H%M%S')}"
    match = re.search(r"\boutput folder\s+([A-Za-z0-9_.-]+)", text, re.IGNORECASE)
    if match:
        out_root_name = match.group(1)
    return {"run_count": run_count, "padding": padding, "out_root_name": out_root_name}


def _strip_run_count_phrases(text: str) -> str:
    cleaned = str(text or "")
    patterns = [
        r"\btotal(?:ing|ed)?\s+(?:of\s+)?\d+\s+(?:dock(?:ing|ings?|s)?|combination(?:s)?|combo(?:s)?|job(?:s)?|run(?:s)?)\b",
        r"\b(?:a\s+total\s+of|total(?:ing|ed)?)\s+\d+\b",
        r"\b\d+\s+dock(?:ing|ings?|s)?\b",
        r"\b\d+\s+combinations?\b",
        r"\b\d+\s+runs?\b",
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip()


def _emit_progress(progress_callback: Callable[[dict[str, Any]], None] | None, **payload: Any) -> None:
    if not progress_callback:
        return
    try:
        progress_callback(payload)
    except Exception:
        pass


def _assets_from_direct_prompt(prompt: str) -> dict[str, str]:
    text = _strip_run_count_phrases(str(prompt or "").strip())
    receptors: list[str] = []
    for match in re.findall(PDB_ID_RE, text):
        pdb_id = match.upper()
        if pdb_id not in receptors:
            receptors.append(pdb_id)

    ligand_chunks: list[str] = []
    patterns = [
        rf"\bligands?\s*(?:are|is|:|=)?\s*(.+?)(?:\s+(?:against|with|for|on|into|in|to)\s+(?:the\s+)?(?:receptors?|proteins?|pdb|{PDB_ID_RE})|[.;\n]|$)",
        rf"\bdock\s+(.+?)\s+(?:against|with|for|on|into|in|to)\s+(?:the\s+)?(?:receptors?|proteins?|pdb|{PDB_ID_RE})",
        rf"\bdocking\s+(.+?)\s+(?:against|with|for|on|into|in|to)\s+(?:the\s+)?(?:receptors?|proteins?|pdb|{PDB_ID_RE})",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            chunk = str(match.group(1) or "").strip()
            if chunk:
                ligand_chunks.append(chunk)

    if not ligand_chunks:
        scrubbed = re.sub(PDB_ID_RE, " ", text)
        scrubbed = re.sub(r"\b(?:dock|docking|against|with|for|on|into|in|to|receptor|receptors|pdb|protein|proteins|combination|combinations|combo|combos|run|runs|total|totaling|totaled)\b", " ", scrubbed, flags=re.IGNORECASE)
        scrubbed = re.sub(r"\b(?:test|mode|log|full|run|runs|standard|vina|gpu|padding|output|folder)\b", " ", scrubbed, flags=re.IGNORECASE)
        scrubbed = re.sub(r"\b(?:bana|et|yap|eder|misin|ile|icin|için|calistir|çalıştır|baslat|başlat)\b", " ", scrubbed, flags=re.IGNORECASE)
        ligand_chunks.append(scrubbed)

    ligands: list[str] = []
    seen: set[str] = set()
    for chunk in ligand_chunks:
        cleaned = re.sub(r"\([^)]*\)", " ", chunk)
        cleaned = re.sub(r"\b(?:and|ve)\b", ",", cleaned, flags=re.IGNORECASE)
        for token in re.split(r"[,;/\n]+", cleaned):
            token = re.sub(PDB_ID_RE, " ", token)
            token = re.sub(r"[^A-Za-z0-9:_+ -]+", " ", token)
            words = [
                word
                for word in re.split(r"\s+", token.strip())
                if word and word.lower() not in LIGAND_WORDS_TO_DROP
            ]
            value = re.sub(r"\s+", " ", " ".join(words)).strip(" .:-")
            if not value:
                continue
            if re.fullmatch(r"\d+\s+runs?", value, re.IGNORECASE):
                continue
            if re.fullmatch(r"\d+", value, re.IGNORECASE):
                continue
            if re.search(r"\b(?:receptors?|pdb|test|mode|padding|output|folder)\b", value, re.IGNORECASE):
                continue
            if value.upper() in receptors:
                continue
            key = value.lower()
            if key not in seen:
                ligands.append(value)
                seen.add(key)
    return {"receptors": ",".join(receptors), "ligands": ";".join(ligands)}


def suggest_setup_rows(inventory: dict[str, Any], box_size: float = 20.0) -> str:
    return _suggest_setup_rows(inventory, box_size)


def _receptor_meta(pdb_id: str) -> dict[str, Any] | None:
    target_id = _normalize_receptor_id(pdb_id)
    return next((_m for _m in STATE.get("receptor_meta", []) if _normalize_receptor_id(_m.get("pdb_id")) == target_id), None)


def _receptor_pdb_text(pdb_id: str) -> str:
    target_id = _normalize_receptor_id(pdb_id)
    meta = _receptor_meta(target_id)
    if not meta:
        return ""
    pdb_text = str(meta.get("pdb_text") or "")
    if pdb_text:
        return pdb_text
    pdb_file = str(meta.get("pdb_file") or (RECEPTOR_DIR / f"{target_id}.pdb"))
    try:
        return Path(pdb_file).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _ligand_centroid(pdb_id: str, chain: str, native_ligand: str) -> tuple[dict[str, float] | None, str]:
    target_id = _normalize_receptor_id(pdb_id)
    target_chain = str(chain or "all").strip()
    target_lig = str(native_ligand or "").strip().upper()
    target_resname = target_lig.split()[0] if target_lig else ""
    target_resid = target_lig.split()[1] if len(target_lig.split()) > 1 else ""
    if not _receptor_meta(target_id):
        return None, f"{target_id}: receptor not loaded"
    pdb_text = _receptor_pdb_text(target_id)
    points: list[tuple[float, float, float]] = []
    for line in pdb_text.splitlines():
        if not line.startswith("HETATM"):
            continue
        resname = line[17:20].strip().upper()
        line_chain = line[21].strip() or "_"
        resid = line[22:26].strip()
        if target_chain not in {"", "all"} and line_chain != target_chain:
            continue
        if target_resname and resname != target_resname:
            continue
        if target_resid and resid != target_resid:
            continue
        try:
            points.append((float(line[30:38]), float(line[38:46]), float(line[46:54])))
        except ValueError:
            continue
    if not points:
        return None, f"{target_id}: native ligand {native_ligand!r} not found for chain {chain!r}"
    count = len(points)
    return {
        "cx": round(sum(p[0] for p in points) / count, 3),
        "cy": round(sum(p[1] for p in points) / count, 3),
        "cz": round(sum(p[2] for p in points) / count, 3),
    }, ""


def _native_ligand_resname(native_ligand: str) -> str:
    return str(native_ligand or "").strip().split()[0].upper()


def _is_helper_native_ligand(native_ligand: str) -> bool:
    return _native_ligand_resname(native_ligand) in COMMON_NATIVE_LIGANDS


def _ligand_atom_count(pdb_id: str, chain: str, native_ligand: str) -> int:
    target_id = _normalize_receptor_id(pdb_id)
    target_chain = str(chain or "all").strip()
    if target_chain.lower() in {"", "all", "auto"}:
        target_chain = "all"
    target_lig = str(native_ligand or "").strip().upper()
    target_resname = target_lig.split()[0] if target_lig else ""
    target_resid = target_lig.split()[1] if len(target_lig.split()) > 1 else ""
    pdb_text = _receptor_pdb_text(target_id)
    count = 0
    for line in pdb_text.splitlines():
        if not line.startswith("HETATM"):
            continue
        resname = line[17:20].strip().upper()
        line_chain = line[21].strip() or "_"
        resid = line[22:26].strip()
        if target_chain not in {"", "all"} and line_chain != target_chain:
            continue
        if target_resname and resname != target_resname:
            continue
        if target_resid and resid != target_resid:
            continue
        count += 1
    return count


def _native_ligand_sort_key(pdb_id: str, chain: str, native_ligand: str) -> tuple[Any, ...]:
    normalized_chain = str(chain or "all").strip()
    if normalized_chain.lower() in {"", "all", "auto"}:
        normalized_chain = "all"
    lig = str(native_ligand or "").strip()
    resname = _native_ligand_resname(lig)
    resid = lig.split()[1] if len(lig.split()) > 1 else ""
    try:
        resid_num: Any = int(resid)
    except (TypeError, ValueError):
        resid_num = resid or 0
    atom_count = _ligand_atom_count(pdb_id, normalized_chain, lig)
    return (
        resname in COMMON_NATIVE_LIGANDS,
        atom_count <= 0,
        -atom_count,
        len(resname) <= 2,
        str(resname),
        resid_num,
        lig,
    )


def _receptor_file_for_meta(pdb_id: str) -> Path | None:
    meta = _receptor_meta(pdb_id)
    if not meta:
        return None
    pdb_file = str(meta.get("pdb_file") or "").strip()
    if pdb_file:
        candidate = Path(pdb_file).expanduser()
        if candidate.exists():
            return candidate.resolve()
    fallback = RECEPTOR_DIR / f"{_normalize_receptor_id(pdb_id)}.pdb"
    return fallback if fallback.exists() else None


def _wait_for_p2rank_gridbox(
    pdb_id: str,
    chain: str,
    *,
    pocket_rank: int,
    mode: str,
    fixed_size: float,
    padding: float,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    timeout_seconds: float = 120.0,
) -> tuple[dict[str, float], list[str]]:
    target_id = _normalize_receptor_id(pdb_id)
    target_chain = str(chain or "all").strip() or "all"
    receptor_file = _receptor_file_for_meta(target_id)
    if not receptor_file:
        raise RuntimeError(f"{target_id}: receptor file missing for P2Rank gridbox.")

    cached_output = latest_output_dir(target_id, target_chain)
    pocket_index = max(1, int(pocket_rank or 1))
    if cached_output and cached_output.exists():
        grid = compute_gridbox_for_pocket(
            cached_output,
            pocket_rank=pocket_index,
            mode=mode,
            fixed_size=fixed_size,
            padding=padding,
        )
        _emit_progress(
            progress_callback,
            type="status",
            stage="p2rank",
            delta=f"P2Rank pocket grid ready for {target_id} ({target_chain}).",
        )
        return grid, [f"{target_id}: reused cached P2Rank pocket"]

    _emit_progress(
        progress_callback,
        type="status",
        stage="p2rank",
        delta=f"Running P2Rank for {target_id} ({target_chain})...",
    )
    try:
        run_p2rank_async(target_id, receptor_file, chain=target_chain)
    except RuntimeError:
        # Another prediction for the same receptor/chain can already be running.
        pass
    except FileNotFoundError:
        raise

    deadline = time.time() + max(10.0, float(timeout_seconds or 120.0))
    last_message = ""
    while time.time() < deadline:
        runtime = get_runtime_state()
        status = str(runtime.get("status") or "").strip().lower()
        message = str(runtime.get("message") or "").strip()
        error = str(runtime.get("error") or "").strip()
        if message and message != last_message:
            _emit_progress(progress_callback, type="status", stage="p2rank", delta=message)
            last_message = message
        if status == "done":
            output_dir = latest_output_dir(target_id, target_chain)
            if output_dir and output_dir.exists():
                grid = compute_gridbox_for_pocket(
                    output_dir,
                    pocket_rank=pocket_index,
                    mode=mode,
                    fixed_size=fixed_size,
                    padding=padding,
                )
                _emit_progress(
                    progress_callback,
                    type="status",
                    stage="p2rank",
                    delta=f"P2Rank pocket {pocket_index} ready for {target_id}.",
                )
                return grid, []
        if status == "error":
            raise RuntimeError(error or message or f"{target_id}: P2Rank failed")
        time.sleep(0.4)
    raise TimeoutError(f"{target_id}: P2Rank timed out after {int(timeout_seconds)}s")


def _resolve_chain_native(pdb_id: str, chain: str, native: str) -> tuple[str, str]:
    inv = AGENT_STATE.get("inventory") or {}
    receptor = (inv.get("receptors") or {}).get(_normalize_receptor_id(pdb_id)) or {}
    by_chain = receptor.get("native_ligands") or {}
    requested_chain = str(chain or "all").strip() or "all"
    if requested_chain.lower() not in {"", "all", "auto"}:
        requested_chain = requested_chain.upper()
    candidates = list(by_chain.get(requested_chain) or []) or list(by_chain.get("all") or [])
    if not candidates:
        fallback_chain = requested_chain
        if fallback_chain in {"", "all", "auto"}:
            meta = _receptor_meta(_normalize_receptor_id(pdb_id)) or {}
            meta_chains = [str(item).strip() for item in (meta.get("chains") or []) if str(item).strip().lower() not in {"", "all"}]
            if not meta_chains:
                meta_chains = [str(item).strip() for item in by_chain.keys() if str(item).strip().lower() not in {"", "all"}]
            fallback_chain = meta_chains[0] if meta_chains else "all"
        return (fallback_chain or "all").upper() if str(fallback_chain or "").strip().lower() not in {"all", ""} else (fallback_chain or "all"), str(native or "").strip()
    wanted = str(native or "").strip().upper()
    if not wanted or wanted in {"?", "UNKNOWN", "AUTO", "NATIVE"}:
        chosen = sorted(candidates, key=lambda item: _native_ligand_sort_key(pdb_id, requested_chain, item))[0]
    else:
        chosen = sorted(candidates, key=lambda item: _native_ligand_sort_key(pdb_id, requested_chain, item))[0]
        for item in candidates:
            if str(item).upper() == wanted or str(item).upper().startswith(wanted + " "):
                chosen = str(item)
                break
    for chain_name, ligands in by_chain.items():
        if chain_name == "all":
            continue
        if chosen in ligands:
            return str(chain_name), str(chosen)
    return requested_chain or "all", str(chosen)


def plan_assets(receptors: str, ligands: str) -> dict[str, Any]:
    return {
        "receptors": ",".join(_split_tokens(receptors)),
        "ligands": ";".join(_split_ligand_specs_text(ligands)),
    }


def download_assets(receptors: str, ligands: str) -> dict[str, Any]:
    pdb_ids = [_normalize_receptor_id(pid) for pid in _split_tokens(receptors)]
    loaded_receptors, failed_receptors = _refresh_receptor_state(pdb_ids)
    ligand_inputs = _split_ligand_specs_text(ligands)
    saved_ligands: list[str] = []
    failed_ligands: list[str] = []
    for ligand in ligand_inputs:
        saved, error = _fetch_ligand(ligand)
        if saved:
            saved_ligands.append(saved)
        if error:
            failed_ligands.append(error)
    current = [name for name in STATE.get("active_ligands", []) if isinstance(name, str)]
    for name in saved_ligands:
        if name not in current:
            current.append(name)
    STATE["active_ligands"] = current
    save_state_cache()
    inventory = _inventory_for(loaded_receptors, saved_ligands)
    AGENT_STATE["inventory"] = inventory
    return {
        "loaded_receptors": loaded_receptors,
        "saved_ligands": saved_ligands,
        "failed_receptors": failed_receptors,
        "failed_ligands": failed_ligands,
        "inventory": inventory,
    }


def _fetch_ligands_from_specs(ligands: str) -> tuple[list[str], list[str], list[str]]:
    saved_ligands: list[str] = []
    failed_ligands: list[str] = []
    attempts: list[str] = []
    for spec in _parse_ligand_specs(ligands):
        name = str(spec.get("name") or "").strip()
        counts = [int(c) for c in (spec.get("counts") or [1]) if int(c) > 0]
        for count in counts:
            if count == 1:
                saved, error, tried = _fetch_ligand_with_retries(name)
                attempts.extend([f"{name}: {item}" for item in tried])
            else:
                saved, error = _generate_oligomer_ligand(name, count)
                attempts.append(f"{name}[{count}]")
            if saved and saved not in saved_ligands:
                saved_ligands.append(saved)
            if error:
                failed_ligands.append(error)
    return saved_ligands, failed_ligands, attempts


def _parse_setup_rows(rows: Any) -> list[list[Any]]:
    if isinstance(rows, str):
        parsed: list[list[Any]] = []
        for raw_row in rows.split(";"):
            parts = [part.strip() for part in raw_row.split(",", 4)]
            if len(parts) == 5:
                parsed.append(parts)
        return parsed
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, list)]
    return []


def setup_docking(rows: Any) -> dict[str, Any]:
    cleaned: list[list[Any]] = []
    ligands = [str(name) for name in ((AGENT_STATE.get("inventory") or {}).get("ligands") or STATE.get("active_ligands") or [])]
    for row in _parse_setup_rows(rows):
        if len(row) < 5:
            continue
        pdb_id = _normalize_receptor_id(row[0])
        chain = str(row[1] or "all").strip() or "all"
        chain, native = _resolve_chain_native(pdb_id, chain, str(row[2] or ""))
        try:
            box_size = float(row[3])
        except (TypeError, ValueError):
            box_size = 20.0
        dock_ligands = str(row[4] or "all").strip() or "all"
        if dock_ligands.lower() == "all":
            dock_ligands = "all"
        else:
            requested = [item.strip() for item in dock_ligands.split(",") if item.strip()]
            dock_ligands = ",".join([item for item in requested if item in ligands]) or "all"
        cleaned.append([pdb_id, chain, native, box_size, dock_ligands])
    AGENT_STATE["setup_rows"] = cleaned
    return {"rows": cleaned}


def make_gridboxes(
    rows: Any,
    method: str = "native_ligand",
    pocket_rank: int = 1,
    p2rank_mode: str = "fit",
    fixed_size: float = 20.0,
    padding: float = 0.0,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    setup_docking(rows)
    grid_data: dict[str, dict[str, float]] = {}
    warnings: list[str] = []
    p2rank_used = False
    method_norm = str(method or "native_ligand").strip().lower()
    for pdb_id, chain, native, box_size, _dock_ligands in AGENT_STATE.get("setup_rows", []):
        use_p2rank = method_norm in {"auto", "p2rank", "gridfinder"}
        center = None
        error = ""
        native_label = str(native or "").strip()
        usable_native = bool(native_label) and not _is_helper_native_ligand(native_label)
        if method_norm not in {"p2rank", "gridfinder"} and usable_native:
            center, error = _ligand_centroid(pdb_id, chain, native)
            if center is None or error:
                warnings.append(error or f"{pdb_id}: native ligand not found")
                use_p2rank = method_norm in {"auto", "native_ligand", "current_selection"}
        elif method_norm in {"native_ligand", "current_selection", "auto"} and not usable_native:
            warnings.append(f"{pdb_id}: no usable native ligand; using P2Rank/gridfinder")
            use_p2rank = True
        if center is not None and not use_p2rank:
            size = max(1.0, float(box_size))
            grid_data[pdb_id] = {**center, "sx": size, "sy": size, "sz": size}
            continue
        try:
            grid = _wait_for_p2rank_gridbox(
                pdb_id,
                chain,
                pocket_rank=max(1, int(pocket_rank or 1)),
                mode=p2rank_mode,
                fixed_size=max(1.0, float(fixed_size or box_size or 20.0)),
                padding=0.0,
                progress_callback=progress_callback,
            )[0]
            grid_data[pdb_id] = grid
            p2rank_used = True
        except Exception as exc:
            warnings.append(f"{pdb_id}: {exc}")
    _persist_agent_grid_data(grid_data)
    return {"grid_data": grid_data, "warnings": warnings, "p2rank_used": p2rank_used}


def _selection_for_rows() -> dict[str, dict[str, Any]]:
    selection: dict[str, dict[str, Any]] = {}
    session_ligands = [str(name) for name in ((AGENT_STATE.get("inventory") or {}).get("ligands") or [])]
    active_ligands = session_ligands or [str(name) for name in STATE.get("active_ligands", [])]
    for pdb_id, chain, _native, _box_size, dock_ligands in AGENT_STATE.get("setup_rows", []):
        raw_dock = str(dock_ligands or "").strip().lower()
        use_all_ligands = raw_dock in {"all", "*", "all_set", "dock_all", "all ligands", "all_ligands"}
        if use_all_ligands:
            ligand_names = list(active_ligands)
        else:
            ligand_names = [item.strip() for item in str(dock_ligands).split(",") if item.strip()]
        selection[pdb_id] = {
            "chain": chain,
            "ligand_resname": "all_set" if use_all_ligands or len(ligand_names) != 1 else ligand_names[0],
            "ligand_resnames": ligand_names,
            "flex_residues": [],
        }
    return selection


def prepare_batch(
    run_count: int = 1,
    padding: float = 0.0,
    out_root_name: str = "",
    docking_engine: str = "vina_gpu_21",
    docking_mode: str = "standard",
    ligand_binding_mode: str = "single",
    pdb2pqr_ph: float | None = None,
    vina_exhaustiveness: int | None = None,
    vina_num_modes: int | None = None,
    vina_energy_range: float | None = None,
    vina_cpu: int | None = None,
    vina_seed: int | None = None,
) -> dict[str, Any]:
    clean_out_root_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(out_root_name or "")).strip("._-")
    docking_config: dict[str, Any] = {
        "docking_engine": str(docking_engine or "vina_gpu_21"),
        "docking_mode": str(docking_mode or "standard"),
        "ligand_binding_mode": str(ligand_binding_mode or "single"),
    }
    optional_values = {
        "pdb2pqr_ph": pdb2pqr_ph,
        "vina_exhaustiveness": vina_exhaustiveness,
        "vina_num_modes": vina_num_modes,
        "vina_energy_range": vina_energy_range,
        "vina_cpu": vina_cpu,
        "vina_seed": vina_seed,
    }
    for key, value in optional_values.items():
        if value is not None:
            docking_config[key] = value
    cfg = {
        "schema": "dockup.config.v1",
        "mode": "Docking",
        "run_count": max(1, int(run_count or 1)),
        "padding": float(padding or 0.0),
        "out_root_path": "data/dock",
        "out_root_name": clean_out_root_name or f"agent_{time.strftime('%Y%m%d_%H%M%S')}",
        "docking_config": normalize_docking_config(docking_config),
        "selection_map": _selection_for_rows(),
        "grid_data": dict(AGENT_STATE.get("grid_data") or STATE.get("agent_grid_data") or {}),
    }
    AGENT_STATE["batch_config"] = cfg
    STATE["runs"] = cfg["run_count"]
    STATE["grid_pad"] = cfg["padding"]
    STATE["out_root_name"] = cfg["out_root_name"]
    STATE["docking_config"] = cfg["docking_config"]
    if cfg.get("selection_map"):
        selection_map = STATE.setdefault("selection_map", {})
        for pdb_id, row in cfg["selection_map"].items():
            selection_map[pdb_id] = dict(row)
    if cfg.get("grid_data"):
        STATE["agent_grid_data"] = dict(cfg["grid_data"])
    save_state_cache()
    return cfg


def validate_batch() -> dict[str, Any]:
    cfg = dict(AGENT_STATE.get("batch_config") or {})
    errors: list[str] = []
    warnings: list[str] = []
    selection = cfg.get("selection_map") if isinstance(cfg.get("selection_map"), dict) else {}
    grid_data = cfg.get("grid_data") if isinstance(cfg.get("grid_data"), dict) else {}
    active = set(str(name) for name in STATE.get("active_ligands", []))
    for pdb_id, sel in selection.items():
        if pdb_id not in grid_data:
            errors.append(f"{pdb_id}: missing gridbox")
        ligands = sel.get("ligand_resnames") if isinstance(sel, dict) else []
        if not ligands:
            errors.append(f"{pdb_id}: missing dock ligands")
        missing = [name for name in ligands if name not in active]
        if missing:
            errors.append(f"{pdb_id}: missing active ligands {', '.join(missing)}")
    job_count = sum(len((sel or {}).get("ligand_resnames") or []) for sel in selection.values())
    total_runs = job_count * max(1, int(cfg.get("run_count") or 1))
    if not selection:
        errors.append("no receptors selected")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "job_count": job_count, "total_runs": total_runs}


def build_queue(replace_queue: bool = False) -> dict[str, Any]:
    validation = validate_batch()
    if not validation["ok"]:
        return {"ok": False, **validation}
    cfg = dict(AGENT_STATE.get("batch_config") or {})
    out_root_base = resolve_dock_directory(str(cfg.get("out_root_path") or "data/dock"), default=DOCK_DIR.resolve(), allow_create=True)
    out_root_name = str(cfg.get("out_root_name") or f"agent_{time.strftime('%Y%m%d_%H%M%S')}")
    STATE["out_root_path"] = to_display_path(out_root_base)
    STATE["out_root_name"] = out_root_name
    STATE["out_root"] = str((out_root_base / out_root_name).resolve())
    batch_entries: list[dict[str, Any]] = []
    selection = cfg.get("selection_map") or {}
    for pdb_id, sel in selection.items():
        for ligand_name in sel.get("ligand_resnames") or []:
            payload = {
                "selection_map": {pdb_id: {**sel, "ligand_resname": ligand_name, "ligand_resnames": [ligand_name]}},
                "grid_data": {pdb_id: (cfg.get("grid_data") or {}).get(pdb_id)},
                "run_count": cfg.get("run_count", 1),
                "padding": cfg.get("padding", 0),
                "mode": "Docking",
                "out_root_path": to_display_path(out_root_base),
                "out_root_name": out_root_name,
                "docking_config": cfg.get("docking_config") or {},
            }
            batch_entries.extend(_build_queue(payload))
    batch_id = str(int(time.time() * 1000))
    for entry in batch_entries:
        entry["batch_id"] = batch_id
    if replace_queue:
        STATE["queue"] = batch_entries
    else:
        STATE["queue"].extend(batch_entries)
    save_state_cache()
    AGENT_STATE["batch_id"] = batch_id
    return {"ok": True, "batch_id": batch_id, "queue_count": len(STATE["queue"]), "new_jobs": len(batch_entries), **validation}


def run_queue(
    test_mode: bool = True,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    batch_id = str(AGENT_STATE.get("batch_id") or "")
    rows = [row for row in STATE.get("queue", []) if str(row.get("batch_id")) == batch_id] if batch_id else list(STATE.get("queue", []))
    if not rows:
        return {"ok": False, "error": "queue is empty"}
    cfg = dict(AGENT_STATE.get("batch_config") or {})
    try:
        run_count = max(1, int(cfg.get("run_count") or 1))
    except (TypeError, ValueError):
        run_count = 1
    job_count = len(rows)
    out_root = str(rows[0].get("out_root") or STATE.get("out_root") or DOCK_DIR)
    planned = materialize_queue_runs(rows, out_root)
    _emit_progress(
        progress_callback,
        type="status",
        stage="run_queue",
        delta=f"Queue ready for {'test/log' if test_mode else 'real'} run; batch {batch_id or '-'}.",
    )
    if test_mode:
        _emit_progress(
            progress_callback,
            type="status",
            stage="run_queue",
            delta=f"Planned {len(planned)} run(s); no heavy docking process was started.",
        )
        return {
            "ok": True,
            "test_mode": True,
            "batch_id": batch_id,
            "queue_jobs": job_count,
            "job_count": job_count,
            "run_count": run_count,
            "planned_total_runs": len(planned),
            "total_runs": len(planned),
            "out_root": out_root,
        }
    from ..routes.core import RunStartPayload, run_start

    try:
        selected_batch_id: int | None = None
        if batch_id:
            try:
                selected_batch_id = int(batch_id)
            except (TypeError, ValueError):
                selected_batch_id = None
        _emit_progress(
            progress_callback,
            type="status",
            stage="run_queue",
            delta=f"Submitting real run for batch {selected_batch_id if selected_batch_id is not None else (batch_id or '-')}...",
        )
        response = run_start(RunStartPayload(is_test_mode=False, batch_id=selected_batch_id))
        response_payload = json.loads(response.body.decode("utf-8"))
        if int(getattr(response, "status_code", 200) or 200) >= 400:
            error_message = str(response_payload.get("error") or response_payload.get("detail") or "run_start failed")
            _emit_progress(
                progress_callback,
                type="status",
                stage="run_queue",
                delta=f"Run start failed: {error_message}",
            )
            return {
                "ok": False,
                "error": error_message,
                "response": response_payload,
                "batch_id": batch_id,
                "queue_jobs": job_count,
                "job_count": job_count,
                "run_count": run_count,
                "planned_total_runs": len(planned),
                "total_runs": len(planned),
                "out_root": out_root,
            }
        _emit_progress(
            progress_callback,
            type="status",
            stage="run_queue",
            delta=f"Run started for batch {selected_batch_id if selected_batch_id is not None else (batch_id or '-')}.",
        )
        return {
            "ok": True,
            "test_mode": False,
            "started": True,
            "response": response_payload,
            "batch_id": batch_id,
            "queue_jobs": job_count,
            "job_count": job_count,
            "run_count": run_count,
            "planned_total_runs": len(planned),
            "total_runs": len(planned),
            "out_root": out_root,
        }
    except HTTPException as exc:
        _emit_progress(
            progress_callback,
            type="status",
            stage="run_queue",
            delta=f"Run start failed: {exc.detail}",
        )
        return {"ok": False, "error": str(exc.detail)}
    except Exception as exc:
        _emit_progress(
            progress_callback,
            type="status",
            stage="run_queue",
            delta=f"Run start failed: {type(exc).__name__}: {exc}",
        )
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _state_receptor_ids() -> list[str]:
    return [_normalize_receptor_id(row.get("pdb_id")) for row in STATE.get("receptor_meta", []) if _normalize_receptor_id(row.get("pdb_id"))]


def _compact_agent_state(*, allowed_next_tools: list[str] | None = None) -> dict[str, Any]:
    queue = list(STATE.get("queue") or [])
    return {
        "ok": True,
        "loaded_receptors": _state_receptor_ids()[:12],
        "loaded_ligands": [str(name) for name in STATE.get("active_ligands", []) if str(name).strip()][:24],
        "selected_receptor": str(STATE.get("selected_receptor") or ""),
        "selected_chain": str(STATE.get("selected_chain") or "all"),
        "selected_native_ligand": str(STATE.get("selected_ligand") or ""),
        "workspace_rows": len(AGENT_STATE.get("setup_rows") or []),
        "gridbox_ready": bool(AGENT_STATE.get("grid_data") or STATE.get("agent_grid_data")),
        "gridbox_count": len((AGENT_STATE.get("grid_data") or STATE.get("agent_grid_data") or {})),
        "queue_jobs": len(queue),
        "run_status": str(RUN_STATE.get("status") or "idle"),
        "allowed_next_tools": allowed_next_tools or [],
    }


def _compact_assets_inventory(receptor_ids: list[str] | None = None, ligand_names: list[str] | None = None) -> dict[str, Any]:
    inventory = _inventory_for(receptor_ids or _state_receptor_ids(), ligand_names or list(STATE.get("active_ligands") or []))
    AGENT_STATE["inventory"] = inventory
    return _compact_inventory(inventory)


def get_dockup_state() -> dict[str, Any]:
    return _compact_agent_state(allowed_next_tools=["fetch_assets", "inspect_assets", "read_tool_details"])


def fetch_assets(receptors: str = "", ligands: str = "") -> dict[str, Any]:
    pdb_ids = [_normalize_receptor_id(pid) for pid in _split_tokens(receptors)]
    loaded_receptors, failed_receptors = _refresh_receptor_state(pdb_ids)
    saved_ligands, failed_ligands, attempts = _fetch_ligands_from_specs(ligands)
    current = [name for name in STATE.get("active_ligands", []) if isinstance(name, str)]
    for name in saved_ligands:
        if name not in current:
            current.append(name)
    STATE["active_ligands"] = current
    save_state_cache()
    AGENT_STATE["inventory"] = _inventory_for(loaded_receptors or pdb_ids, saved_ligands or current)
    summary = f"Loaded {len(loaded_receptors)} receptor(s), saved {len(saved_ligands)} ligand file(s)."
    retry_hint = ""
    if failed_receptors or failed_ligands:
        retry_hint = "Retry once with the cleanest obvious alternative from retry_attempts or a corrected spelling, then keep the assets that already loaded."
        summary += f" Failed {len(failed_receptors)} receptor(s), {len(failed_ligands)} ligand(s). {retry_hint}"
    return {
        "ok": not failed_receptors and not failed_ligands,
        "summary": summary,
        "retry_hint": retry_hint,
        "loaded_receptors": loaded_receptors,
        "saved_ligands": saved_ligands,
        "failed_receptors": failed_receptors[:6],
        "failed_ligands": failed_ligands[:6],
        "retry_attempts": attempts[:12],
        "allowed_next_tools": ["inspect_assets", "fetch_assets", "read_tool_details"],
    }


def _persist_agent_grid_data(grid_data: dict[str, Any]) -> None:
    normalized = {
        _normalize_receptor_id(pdb_id): grid
        for pdb_id, grid in dict(grid_data or {}).items()
        if _normalize_receptor_id(pdb_id) and isinstance(grid, dict)
    }
    AGENT_STATE["grid_data"] = normalized
    STATE["agent_grid_data"] = dict(normalized)
    save_state_cache()


def delete_ligands(target: str = "all") -> dict[str, Any]:
    raw_target = str(target or "all").strip()
    all_requested = raw_target.lower() in {"", "all", "*", "delete_all"}
    available = {path.name.lower(): path for path in LIGAND_DIR.glob("*.sdf")}
    available.update({path.stem.lower(): path for path in LIGAND_DIR.glob("*.sdf")})
    requested = list({token.strip() for token in _split_tokens(raw_target) if token.strip()})
    targets = sorted({path for path in LIGAND_DIR.glob("*.sdf")}) if all_requested else []
    missing: list[str] = []
    if not all_requested:
        seen_paths: set[Path] = set()
        for token in requested:
            key = token.lower()
            if key.endswith(".sdf"):
                key = key[:-4]
            path = available.get(token.lower()) or available.get(key)
            if path and path not in seen_paths:
                targets.append(path)
                seen_paths.add(path)
            else:
                missing.append(token)
    deleted: list[str] = []
    for path in targets:
        try:
            resolved = path.resolve()
            if LIGAND_DIR.resolve() not in resolved.parents or resolved.suffix.lower() != ".sdf":
                continue
            resolved.unlink()
            deleted.append(path.name)
        except FileNotFoundError:
            continue
        except Exception as exc:
            missing.append(f"{path.name}: {exc}")
    deleted_set = set(deleted)
    STATE["active_ligands"] = [
        name for name in STATE.get("active_ligands", [])
        if str(name or "").strip() not in deleted_set
    ]
    for row in STATE.get("selection_map", {}).values():
        if not isinstance(row, dict):
            continue
        names = [name for name in row.get("ligand_resnames", []) if str(name or "").strip() not in deleted_set]
        row["ligand_resnames"] = names
        if str(row.get("ligand_resname") or "").strip() in deleted_set:
            row["ligand_resname"] = names[0] if names else ""
    if isinstance(AGENT_STATE.get("inventory"), dict):
        AGENT_STATE["inventory"] = _inventory_for(_state_receptor_ids(), list(STATE.get("active_ligands") or []))
    save_state_cache()
    return {
        "ok": not missing,
        "summary": f"Deleted {len(deleted)} ligand file(s).",
        "deleted": deleted,
        "missing": missing[:12],
        "active_ligands": list(STATE.get("active_ligands") or []),
        "allowed_next_tools": ["inspect_assets", "fetch_assets", "delete_ligands", "get_dockup_state"],
    }


def delete_receptors(target: str = "all") -> dict[str, Any]:
    raw_target = str(target or "all").strip()
    all_requested = raw_target.lower() in {"", "all", "*", "delete_all"}
    requested_ids = {_normalize_receptor_id(token) for token in _split_tokens(raw_target) if _normalize_receptor_id(token)}
    deleted: list[str] = []
    missing: list[str] = []
    remaining_meta: list[dict[str, Any]] = []
    matched_ids: set[str] = set()
    for item in STATE.get("receptor_meta", []):
        if not isinstance(item, dict):
            continue
        pdb_id = _normalize_receptor_id(item.get("pdb_id"))
        pdb_file = str(item.get("pdb_file") or "").strip()
        should_delete = all_requested or pdb_id in requested_ids
        if should_delete:
            matched_ids.add(pdb_id)
            if pdb_file:
                try:
                    path = Path(pdb_file).resolve()
                    if RECEPTOR_DIR.resolve() in path.parents and path.suffix.lower() == ".pdb" and path.exists():
                        path.unlink()
                except Exception:
                    pass
            deleted.append(pdb_id)
        else:
            remaining_meta.append(item)
    if all_requested:
        for path in RECEPTOR_DIR.glob("*.pdb"):
            try:
                path.unlink()
            except Exception:
                pass
    else:
        for pdb_id in requested_ids - matched_ids:
            path = (RECEPTOR_DIR / f"{pdb_id}.pdb").resolve()
            if path.exists() and RECEPTOR_DIR.resolve() in path.parents:
                try:
                    path.unlink()
                    deleted.append(pdb_id)
                    matched_ids.add(pdb_id)
                except Exception:
                    missing.append(pdb_id)
            else:
                missing.append(pdb_id)
    STATE["receptor_meta"] = [] if all_requested else remaining_meta
    for pdb_id in set(deleted):
        STATE.get("selection_map", {}).pop(pdb_id, None)
    if all_requested:
        STATE["selection_map"] = {}
        STATE["selected_receptor"] = ""
        STATE["selected_ligand"] = ""
        STATE["selected_chain"] = "all"
        STATE["selected_ids"] = []
        STATE["agent_grid_data"] = {}
        AGENT_STATE["setup_rows"] = []
        AGENT_STATE["grid_data"] = {}
        AGENT_STATE["batch_config"] = {}
    else:
        deleted_ids = set(deleted)
        STATE["selected_ids"] = [rid for rid in STATE.get("selected_ids", []) if _normalize_receptor_id(rid) not in deleted_ids]
        if _normalize_receptor_id(STATE.get("selected_receptor")) in deleted_ids:
            first = STATE["receptor_meta"][0] if STATE.get("receptor_meta") else {}
            STATE["selected_receptor"] = _normalize_receptor_id(first.get("pdb_id")) if first else ""
            STATE["selected_ligand"] = ""
            STATE["selected_chain"] = "all"
            STATE["selected_ids"] = [STATE["selected_receptor"]] if STATE["selected_receptor"] else []
        grid_data = STATE.get("agent_grid_data") if isinstance(STATE.get("agent_grid_data"), dict) else {}
        for pdb_id in deleted_ids:
            grid_data.pop(pdb_id, None)
        STATE["agent_grid_data"] = grid_data
        AGENT_STATE["setup_rows"] = [
            row for row in AGENT_STATE.get("setup_rows", [])
            if _normalize_receptor_id(row[0] if row else "") not in deleted_ids
        ]
        AGENT_STATE["grid_data"] = {
            pdb_id: grid for pdb_id, grid in (AGENT_STATE.get("grid_data") or {}).items()
            if _normalize_receptor_id(pdb_id) not in deleted_ids
        }
    STATE["queue"] = [
        job for job in STATE.get("queue", [])
        if _normalize_receptor_id(job.get("pdb_id")) not in set(deleted)
    ]
    if isinstance(AGENT_STATE.get("inventory"), dict):
        AGENT_STATE["inventory"] = _inventory_for(_state_receptor_ids(), list(STATE.get("active_ligands") or []))
    save_state_cache()
    return {
        "ok": not missing,
        "summary": f"Deleted {len(set(deleted))} receptor(s).",
        "deleted": sorted(set(deleted)),
        "missing": sorted(set(missing))[:12],
        "remaining_receptors": _state_receptor_ids(),
        "allowed_next_tools": ["inspect_assets", "fetch_assets", "delete_receptors", "get_dockup_state"],
    }


def delete_queue_batches(batch_id: str = "all") -> dict[str, Any]:
    raw = str(batch_id or "all").strip()
    all_requested = raw.lower() in {"", "all", "*", "delete_all"}
    requested = {token.strip() for token in _split_tokens(raw) if token.strip()}
    queue = list(STATE.get("queue") or [])
    if all_requested:
        deleted_ids = sorted({str(job.get("batch_id") or "").strip() for job in queue if str(job.get("batch_id") or "").strip()})
        STATE["queue"] = []
    else:
        deleted_ids = sorted({str(job.get("batch_id") or "").strip() for job in queue if str(job.get("batch_id") or "").strip() in requested})
        STATE["queue"] = [job for job in queue if str(job.get("batch_id") or "").strip() not in requested]
    save_state_cache()
    return {
        "ok": True,
        "summary": f"Deleted {len(deleted_ids)} queue batch(es).",
        "deleted_batch_ids": deleted_ids,
        "queue_count": len(STATE.get("queue") or []),
        "allowed_next_tools": ["build_or_run_queue", "get_dockup_state", "delete_queue_batches"],
    }


def inspect_assets() -> dict[str, Any]:
    active = [str(name) for name in STATE.get("active_ligands", []) if str(name).strip()]
    inventory = _compact_assets_inventory(ligand_names=active)
    return {
        "ok": True,
        "summary": f"Inspected {len(inventory.get('receptors') or {})} receptor(s) and {len(active)} active ligand(s).",
        "inventory": inventory,
        "allowed_next_tools": ["select_workspace", "fetch_assets", "read_tool_details"],
    }


def show_in_viewer(receptor: str = "", chain: str = "all", native_ligand: str = "") -> dict[str, Any]:
    pdb_id = _normalize_receptor_id(receptor or STATE.get("selected_receptor") or "")
    if not pdb_id:
        return {"ok": False, "summary": "No receptor selected for viewer.", "error": "missing receptor", "allowed_next_tools": ["fetch_assets", "inspect_assets"]}
    if not _receptor_meta(pdb_id):
        loaded, failed = _refresh_receptor_state([pdb_id])
        if failed or pdb_id not in loaded:
            return {"ok": False, "summary": f"{pdb_id} is not loaded.", "error": f"{pdb_id}: receptor not loaded", "allowed_next_tools": ["fetch_assets"]}
    selected_chain = str(chain or "all").strip() or "all"
    selected_ligand = str(native_ligand or "").strip()
    STATE["selected_receptor"] = pdb_id
    STATE["selected_ids"] = [pdb_id]
    STATE["selected_chain"] = selected_chain
    if selected_ligand:
        STATE["selected_ligand"] = selected_ligand
    selection_map = STATE.setdefault("selection_map", {})
    row = selection_map.setdefault(pdb_id, {})
    row["chain"] = selected_chain
    if selected_ligand:
        row["ligand_resname"] = selected_ligand
    save_state_cache()
    return {
        "ok": True,
        "summary": f"Viewer selected {pdb_id}" + (f" chain {selected_chain}" if selected_chain != "all" else ""),
        "selected_receptor": pdb_id,
        "selected_chain": selected_chain,
        "selected_native_ligand": selected_ligand,
        "allowed_next_tools": ["inspect_assets", "select_workspace", "show_residues", "set_gridbox"],
    }


def _residue_alias(value: str) -> str:
    raw = str(value or "").strip().upper()
    aliases = {
        "TRYPTOPHAN": "TRP",
        "TRYPTHOPHAN": "TRP",
        "TRP": "TRP",
        "TYROSINE": "TYR",
        "PHENYLALANINE": "PHE",
        "HISTIDINE": "HIS",
    }
    return aliases.get(raw, raw[:3])


def _residue_selection(resno: str, chain: str) -> str:
    clean_chain = str(chain or "").strip()
    return f"{resno}:{clean_chain}" if clean_chain and clean_chain != "_" else str(resno)


def show_residues(receptor: str = "", residue: str = "TRP", chain: str = "all") -> dict[str, Any]:
    pdb_id = _normalize_receptor_id(receptor or STATE.get("selected_receptor") or "")
    if not pdb_id:
        return {"ok": False, "summary": "No receptor selected.", "error": "missing receptor", "allowed_next_tools": ["fetch_assets", "show_in_viewer"]}
    if not _receptor_meta(pdb_id):
        loaded, failed = _refresh_receptor_state([pdb_id])
        if failed or pdb_id not in loaded:
            return {"ok": False, "summary": f"{pdb_id} is not loaded.", "error": f"{pdb_id}: receptor not loaded", "allowed_next_tools": ["fetch_assets"]}
    target_residue = _residue_alias(residue)
    target_chain = str(chain or "all").strip()
    residues: dict[tuple[str, str, str], dict[str, Any]] = {}
    for line in _receptor_pdb_text(pdb_id).splitlines():
        if not line.startswith(("ATOM  ", "HETATM")):
            continue
        resname = line[17:20].strip().upper()
        if resname != target_residue:
            continue
        line_chain = line[21].strip() or "_"
        if target_chain not in {"", "all"} and line_chain != target_chain:
            continue
        resno = line[22:26].strip()
        if not resno:
            continue
        key = (line_chain, resno, resname)
        row = residues.setdefault(
            key,
            {
                "chain": line_chain,
                "resno": resno,
                "resname": resname,
                "atom_count": 0,
                "bbox": {"minX": 1e9, "minY": 1e9, "minZ": 1e9, "maxX": -1e9, "maxY": -1e9, "maxZ": -1e9},
            },
        )
        try:
            x, y, z = float(line[30:38]), float(line[38:46]), float(line[46:54])
        except ValueError:
            continue
        row["atom_count"] += 1
        bbox = row["bbox"]
        bbox["minX"] = min(bbox["minX"], x)
        bbox["minY"] = min(bbox["minY"], y)
        bbox["minZ"] = min(bbox["minZ"], z)
        bbox["maxX"] = max(bbox["maxX"], x)
        bbox["maxY"] = max(bbox["maxY"], y)
        bbox["maxZ"] = max(bbox["maxZ"], z)
    rows = sorted(
        residues.values(),
        key=lambda item: (
            str(item["chain"]),
            0 if str(item["resno"]).isdigit() else 1,
            int(item["resno"]) if str(item["resno"]).isdigit() else str(item["resno"]),
        ),
    )
    selection = " or ".join(_residue_selection(row["resno"], row["chain"]) for row in rows[:64])
    combined_bbox: dict[str, float] | None = None
    if rows:
        combined_bbox = {
            "minX": round(min(row["bbox"]["minX"] for row in rows), 3),
            "minY": round(min(row["bbox"]["minY"] for row in rows), 3),
            "minZ": round(min(row["bbox"]["minZ"] for row in rows), 3),
            "maxX": round(max(row["bbox"]["maxX"] for row in rows), 3),
            "maxY": round(max(row["bbox"]["maxY"] for row in rows), 3),
            "maxZ": round(max(row["bbox"]["maxZ"] for row in rows), 3),
        }
    show_in_viewer(pdb_id, target_chain if target_chain not in {"", "all"} else "all")
    return {
        "ok": True,
        "summary": f"Found {len(rows)} {target_residue} residue(s) in {pdb_id}.",
        "receptor": pdb_id,
        "residue": target_residue,
        "chain": target_chain,
        "residues": rows[:64],
        "selection": selection,
        "viewer_selection": {
            "label": f"{pdb_id} {target_residue} ({len(rows)})",
            "selection": selection,
            "residues": rows[:64],
            "bbox": combined_bbox,
        } if rows and combined_bbox else None,
        "allowed_next_tools": ["show_in_viewer", "set_gridbox", "select_workspace"],
    }


def _workspace_rows_for(receptor: str, chain: str, native_ligand: str, dock_ligands: str, box_size: float = 20.0) -> str:
    inventory = AGENT_STATE.get("inventory") or _compact_assets_inventory()
    receptor_value = str(receptor or "all").strip()
    if not receptor_value or receptor_value.lower() == "all":
        receptor_ids = list((inventory.get("receptors") or {}).keys()) or _state_receptor_ids()
    else:
        receptor_ids = [_normalize_receptor_id(pid) for pid in _split_tokens(receptor_value)]
    rows: list[str] = []
    for pdb_id in receptor_ids:
        resolved_chain, resolved_native = _resolve_chain_native(pdb_id, chain or "auto", native_ligand or "auto")
        if not resolved_native:
            receptor_inv = (AGENT_STATE.get("inventory") or {}).get("receptors", {}).get(pdb_id, {})
            suggested = _suggest_setup_rows({"receptors": {pdb_id: receptor_inv}, "ligands": STATE.get("active_ligands", [])}, box_size)
            if suggested:
                rows.append(suggested)
                continue
        rows.append(f"{pdb_id},{resolved_chain},{resolved_native},{box_size:g},{dock_ligands or 'all'}")
    return ";".join(row for row in rows if row)


def select_workspace(receptor: str = "all", chain: str = "auto", native_ligand: str = "auto", dock_ligands: str = "all") -> dict[str, Any]:
    rows_text = _workspace_rows_for(receptor, chain, native_ligand, dock_ligands)
    setup = setup_docking(rows_text)
    rows = setup.get("rows") or []
    if rows:
        first = rows[0]
        STATE["selected_receptor"] = first[0]
        STATE["selected_chain"] = first[1]
        STATE["selected_ligand"] = first[2]
        selection_map = STATE.setdefault("selection_map", {})
        for pdb_id, row in _selection_for_rows().items():
            selection_map[pdb_id] = dict(row)
        save_state_cache()
    if isinstance(AGENT_STATE.get("batch_config"), dict) and AGENT_STATE["batch_config"]:
        AGENT_STATE["batch_config"]["selection_map"] = _selection_for_rows()
    return {
        "ok": bool(rows),
        "summary": f"Selected workspace for {len(rows)} receptor(s).",
        "selected": [
            {"receptor": row[0], "chain": row[1], "native_ligand": row[2], "dock_ligands": row[4]}
            for row in rows[:8]
        ],
        "allowed_next_tools": ["set_gridbox", "select_workspace", "read_tool_details"],
    }


def set_gridbox(
    method: str = "native_ligand",
    size: float = 20.0,
    padding: float = 0.0,
    center: str = "",
    pocket_rank: int = 1,
    p2rank_mode: str = "fit",
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if not AGENT_STATE.get("setup_rows"):
        select_workspace("all", "auto", "auto", "all")
    method_norm = str(method or "native_ligand").strip().lower()
    if method_norm == "manual" and center:
        parts = [p.strip() for p in re.split(r"[, ]+", center) if p.strip()]
        if len(parts) != 3:
            return {"ok": False, "error": "manual gridbox center must be x,y,z", "allowed_next_tools": ["set_gridbox"]}
        try:
            cx, cy, cz = [round(float(p), 3) for p in parts]
        except ValueError:
            return {"ok": False, "error": "manual gridbox center must be numeric", "allowed_next_tools": ["set_gridbox"]}
        grid_data = {}
        for row in AGENT_STATE.get("setup_rows", []):
            grid_data[row[0]] = {"cx": cx, "cy": cy, "cz": cz, "sx": float(size), "sy": float(size), "sz": float(size)}
        warnings: list[str] = []
    else:
        rows = AGENT_STATE.get("setup_rows") or []
        rows_text = ";".join(",".join(str(part) for part in [row[0], row[1], row[2], size, row[4]]) for row in rows)
        result = make_gridboxes(
            rows_text,
            method=method_norm,
            pocket_rank=pocket_rank,
            p2rank_mode=p2rank_mode,
            fixed_size=size,
            padding=padding,
            progress_callback=progress_callback,
        )
        grid_data = result.get("grid_data") or {}
        warnings = result.get("warnings") or []
    if padding:
        for grid in grid_data.values():
            grid["sx"] = round(float(grid.get("sx", size)) + float(padding), 3)
            grid["sy"] = round(float(grid.get("sy", size)) + float(padding), 3)
            grid["sz"] = round(float(grid.get("sz", size)) + float(padding), 3)
    _persist_agent_grid_data(grid_data)
    if isinstance(AGENT_STATE.get("batch_config"), dict) and AGENT_STATE["batch_config"]:
        AGENT_STATE["batch_config"]["grid_data"] = dict(grid_data)
        AGENT_STATE["batch_config"]["selection_map"] = _selection_for_rows()
    return {
        "ok": bool(grid_data),
        "summary": f"Gridbox ready for {len(grid_data)} receptor(s).",
        "grid_data": dict(grid_data),
        "resolved_gridbox_mode": "p2rank" if (method_norm != "manual" and any("P2Rank" in str(w) or "p2rank" in str(w).lower() for w in warnings)) else method_norm,
        "gridboxes": {
            pdb_id: {
                "center": [grid.get("cx"), grid.get("cy"), grid.get("cz")],
                "size": [grid.get("sx"), grid.get("sy"), grid.get("sz")],
            }
            for pdb_id, grid in list(grid_data.items())[:8]
        },
        "gridbox_mode": method_norm if method_norm != "manual" else "manual",
        "warnings": warnings[:6],
        "allowed_next_tools": ["set_docking_config", "set_gridbox", "read_tool_details"],
    }


def _advanced_settings_dict(advanced: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in re.split(r"[;\n]+", str(advanced or "")):
        if "=" not in item:
            continue
        key, value = [part.strip() for part in item.split("=", 1)]
        if not key:
            continue
        if re.fullmatch(r"-?\d+", value):
            out[key] = int(value)
        else:
            try:
                out[key] = float(value)
            except ValueError:
                out[key] = value
    return out


def set_docking_config(
    engine: str = "vina_gpu_21",
    mode: str = "standard",
    run_count: int = 1,
    padding: float = 0.0,
    out_root_name: str = "",
    exhaustiveness: int | None = None,
    num_modes: int | None = None,
    energy_range: float | None = None,
    cpu: int | None = None,
    seed: int | None = None,
    ph: float | None = None,
    advanced: str = "",
) -> dict[str, Any]:
    batch = prepare_batch(
        run_count=run_count,
        padding=padding,
        out_root_name=out_root_name,
        docking_engine=engine,
        docking_mode=mode,
        pdb2pqr_ph=ph,
        vina_exhaustiveness=exhaustiveness,
        vina_num_modes=num_modes,
        vina_energy_range=energy_range,
        vina_cpu=cpu,
        vina_seed=seed,
    )
    extras = _advanced_settings_dict(advanced)
    if extras:
        merged = normalize_docking_config({**batch.get("docking_config", {}), **extras})
        batch["docking_config"] = merged
        AGENT_STATE["batch_config"] = batch
    cfg = batch.get("docking_config") or {}
    validation = validate_batch()
    return {
        "ok": True,
        "summary": f"Config set: engine={cfg.get('docking_engine')} mode={cfg.get('docking_mode')} run_count={batch.get('run_count')}.",
        "config": {
            "engine": cfg.get("docking_engine"),
            "mode": cfg.get("docking_mode"),
            "run_count": batch.get("run_count"),
            "padding": batch.get("padding"),
            "out_root_name": batch.get("out_root_name"),
            "exhaustiveness": cfg.get("vina_exhaustiveness"),
            "num_modes": cfg.get("vina_num_modes"),
            "seed": cfg.get("vina_seed"),
        },
        "validation": {k: validation.get(k) for k in ("ok", "job_count", "total_runs", "errors", "warnings")},
        "allowed_next_tools": ["build_or_run_queue", "set_docking_config", "read_tool_details"],
    }


def _sync_batch_config_from_state() -> dict[str, Any]:
    batch_config = AGENT_STATE.get("batch_config")
    if isinstance(batch_config, dict) and batch_config:
        return batch_config

    docking_config = normalize_docking_config(STATE.get("docking_config") or {})
    selection_map = _normalize_selection_map(STATE.get("selection_map") or {})
    grid_source = STATE.get("agent_grid_data") if isinstance(STATE.get("agent_grid_data"), dict) else {}
    grid_data = {str(key): value for key, value in dict(grid_source or {}).items() if str(key).strip()}
    if not docking_config or not selection_map:
        return {}

    try:
        run_count = max(1, int(STATE.get("runs") or 1))
    except (TypeError, ValueError):
        run_count = 1

    try:
        padding = float(STATE.get("grid_pad") or 0.0)
    except (TypeError, ValueError):
        padding = 0.0

    batch_config = {
        "schema": "dockup.config.v1",
        "mode": str(STATE.get("mode") or "Docking"),
        "run_count": run_count,
        "padding": padding,
        "out_root_path": str(STATE.get("out_root_path") or "data/dock"),
        "out_root_name": str(STATE.get("out_root_name") or f"agent_{time.strftime('%Y%m%d_%H%M%S')}"),
        "docking_config": docking_config,
        "selection_map": selection_map,
        "grid_data": grid_data,
    }
    AGENT_STATE["batch_config"] = batch_config
    return batch_config


def build_or_run_queue(
    action: str = "build_test",
    replace_queue: bool = True,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    action_norm = str(action or "build_test").strip().lower()
    replace_existing = bool(replace_queue)
    _emit_progress(
        progress_callback,
        type="status",
        stage="build_or_run_queue",
        delta=f"Validating queue for {action_norm} ({'replace' if replace_existing else 'append'} mode)...",
    )
    batch_config = _sync_batch_config_from_state()
    if not batch_config:
        return {
            "ok": False,
            "summary": "Docking config is not set yet.",
            "validation": {
                "ok": False,
                "errors": ["set_docking_config must be called before building the queue"],
                "job_count": 0,
                "total_runs": 0,
            },
            "allowed_next_tools": ["set_docking_config", "select_workspace", "set_gridbox", "read_tool_details"],
        }
    elif isinstance(AGENT_STATE.get("batch_config"), dict):
        if isinstance(AGENT_STATE.get("grid_data"), dict) and AGENT_STATE.get("grid_data"):
            AGENT_STATE["batch_config"]["grid_data"] = dict(AGENT_STATE.get("grid_data") or {})
        if AGENT_STATE.get("setup_rows"):
            AGENT_STATE["batch_config"]["selection_map"] = _selection_for_rows()
    validation = validate_batch()
    if not validation.get("ok"):
        return {"ok": False, "summary": "Batch validation failed.", "validation": validation, "allowed_next_tools": ["select_workspace", "set_gridbox", "set_docking_config"]}
    queue_result = build_queue(replace_queue=replace_existing)
    run_result: dict[str, Any] = {}
    if action_norm in {"build_test", "test", "run_test", "test_run", "dry_run", "log", "plan"}:
        run_result = run_queue(test_mode=True, progress_callback=progress_callback) if progress_callback is not None else run_queue(test_mode=True)
    elif action_norm in {"run_full", "full", "run", "start", "start_run", "real", "real_run", "start_full", "full_run", "production"}:
        _emit_progress(
            progress_callback,
            type="status",
            stage="build_or_run_queue",
            delta=f"Queue built; starting real run for batch {queue_result.get('batch_id') or '-'}...",
        )
        run_result = run_queue(test_mode=False, progress_callback=progress_callback) if progress_callback is not None else run_queue(test_mode=False)
    elif action_norm not in {"build_only", "build"}:
        return {
            "ok": False,
            "summary": f"Unknown queue action: {action_norm}",
            "error": f"Unknown queue action: {action_norm}",
            "allowed_next_tools": ["build_or_run_queue", "read_tool_details"],
        }
    return {
        "ok": bool(queue_result.get("ok")) and (not run_result or bool(run_result.get("ok"))),
        "summary": (
            f"Queue action {action_norm}: {queue_result.get('new_jobs', 0)} job(s), batch {queue_result.get('batch_id') or '-'}, mode={'append' if not replace_existing else 'replace'}"
            + (f"; run error: {run_result.get('error')}" if run_result and not run_result.get("ok", True) else "")
        ),
        "queue": {
            "batch_id": queue_result.get("batch_id"),
            "new_jobs": queue_result.get("new_jobs"),
            "queue_count": queue_result.get("queue_count"),
            "job_count": queue_result.get("job_count"),
            "total_runs": queue_result.get("total_runs"),
            "replace_queue": replace_existing,
        },
        "replace_queue": replace_existing,
        "run": {
            "started": bool(run_result and run_result.get("ok", True) and (run_result.get("started", True))),
            "test_mode": run_result.get("test_mode") if run_result else None,
            "ok": run_result.get("ok") if run_result else None,
            "error": run_result.get("error") if run_result else "",
            "planned_total_runs": run_result.get("planned_total_runs") if run_result else None,
            "out_root": run_result.get("out_root") if run_result else "",
        },
        "allowed_next_tools": ["get_dockup_state", "read_tool_details"],
    }


def read_tool_details(topic: str = "workflow") -> dict[str, Any]:
    topic_norm = str(topic or "workflow").strip().lower()
    details = {
        "workflow": (
            "Recommended order is a guide, not a script: inspect state when needed, then use the minimal next tool. "
            "Use one tool at a time unless the next step is obvious from the previous tool result. "
            "Do not invent loaded assets, chains, native ligands, gridboxes, queue rows, or run status. "
            "If the receptor has a clear native ligand, use it for the grid center. If the best native ligand is missing or only helper ions are present, switch the gridbox tool to P2Rank/gridfinder mode. "
            "If fetch_assets fails, keep the successful assets and retry once with the cleanest obvious alternative from retry_attempts before asking the user."
        ),
        "ligand_ranges": (
            "fetch_assets supports explicit ligand forms with name[count,count]. Example: ethylene[1,3,4] "
            "means monomer, trimer, tetramer. Counts are clamped to 1..10. The legacy name[start:end] range is accepted, "
            "but prefer explicit lists. If direct PubChem SDF fetch fails, "
            "the backend tries simple name variants. For count > 1, backend fetches PubChem SMILES and generates 3D SDF via the ligand_3d converter. "
            "Natural words map to counts: monomer=1, dimer=2, trimer=3, tetramer=4."
        ),
        "asset_resolution": (
            "fetch_assets(receptors, ligands) accepts comma-separated PDB IDs and semicolon-separated ligand specs. "
            "Receptors are normalized as PDB IDs. Ligands first resolve local .sdf files by exact/fuzzy name, then PubChem by CID/name. "
            "Name retries include raw text, underscore/space/dash variants. Failed assets are returned compactly; keep successful assets and retry once with the cleanest corrected names from retry_attempts."
        ),
        "workspace": (
            "select_workspace(receptor, chain, native_ligand, dock_ligands) updates DockUP state and viewer-facing selection. "
            "Use receptor='all' for simple multi-receptor jobs. Use chain='auto' and native_ligand='auto' when inspect_assets returned native ligands and no user restriction exists. "
            "dock_ligands='all' means all active ligand files; otherwise pass comma-separated saved SDF filenames."
        ),
        "gridbox": (
            "set_gridbox(method, size, padding, center, pocket_rank, p2rank_mode) supports method=native_ligand, current_selection, p2rank, gridfinder, auto, or manual. "
            "For native_ligand/current_selection the backend computes coordinates from the selected main native ligand; the model should not calculate coordinates. "
            "If no usable native ligand exists, use p2rank/gridfinder and wait for the short P2Rank status message before continuing. "
            "For manual, center must be 'x,y,z'. size is the base cubic box size; padding adds to each dimension."
        ),
        "settings": (
            "set_docking_config supports engine, mode, run_count, padding, out_root_name, exhaustiveness, "
            "num_modes, energy_range, cpu, seed, ph. Rare settings can be passed as advanced='key=value;key=value'. "
            "Allowed advanced keys include pdb2pqr_ff, pdb2pqr_ffout, pdb2pqr_nodebump, pdb2pqr_keep_chain, "
            "mkrec_allow_bad_res, mkrec_default_altloc, ligand_binding_mode, flex_distance, flex_include_backbone, "
            "flex_max_residues, and any normalized docking_config key accepted by DockUP."
        ),
        "setting_catalog": (
            "Common settings: engine=vina_gpu_21|vina, mode=standard|flexible, run_count=integer repeats per job, "
            "padding=float, out_root_name=safe folder name, exhaustiveness=int, num_modes=int, energy_range=float, cpu=int, seed=int, ph=float. "
            "Advanced examples: ligand_binding_mode=single; pdb2pqr_ff=PARSE; pdb2pqr_keep_chain=true; "
            "mkrec_allow_bad_res=false; flex_distance=4.0; flex_max_residues=12."
        ),
        "counts": (
            "job_count is receptor-ligand combinations. run_count is repeated runs per job. "
            "If the user says 3 receptors x 2 ligands = 6 dockings, keep run_count=1 and expect job_count=6."
        ),
        "queue_actions": (
            "build_or_run_queue(action) accepts build_only/build, build_test/test/dry_run, or run_full/full/run/start/real_run. "
            "build_only validates and creates queue rows. build_test materializes/plans the batch without starting a heavy docking process. "
            "run_full starts the real DockUP queue runner. Use replace_queue=false to append a new batch for a different config instead of clearing the queue."
        ),
        "tools": (
            "Tools: get_dockup_state(), fetch_assets(receptors, ligands), inspect_assets(), "
            "show_in_viewer(receptor, chain, native_ligand), show_residues(receptor, residue, chain), "
            "select_workspace(receptor, chain, native_ligand, dock_ligands), set_gridbox(method, size, padding, center, pocket_rank, p2rank_mode), "
            "set_docking_config(...), build_or_run_queue(action, replace_queue), delete_ligands(target), "
            "delete_receptors(target), delete_queue_batches(batch_id), read_tool_details(topic)."
        ),
    }
    return {"ok": True, "topic": topic_norm, "details": details.get(topic_norm, details["workflow"])}


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_dockup_state",
            "description": "Return compact DockUP state without large files or long inventories.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_in_viewer",
            "description": "Select/focus a loaded receptor in the DockUP NGL viewer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "receptor": {"type": "string", "description": "PDB ID to show, e.g. 6CM4."},
                    "chain": {"type": "string", "description": "Optional chain ID or all."},
                    "native_ligand": {"type": "string", "description": "Optional native ligand to select/highlight."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_residues",
            "description": "List and highlight residues such as TRP/tryptophan in a receptor viewer selection.",
            "parameters": {
                "type": "object",
                "properties": {
                    "receptor": {"type": "string", "description": "PDB ID, e.g. 6CM4."},
                    "residue": {"type": "string", "description": "Residue name or code, e.g. TRP or tryptophan."},
                    "chain": {"type": "string", "description": "Optional chain ID or all."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_assets",
            "description": "Fetch/load receptors and ligands. Supports explicit ligand forms like ethylene[1,3,4].",
            "parameters": {
                "type": "object",
                "properties": {
                    "receptors": {"type": "string", "description": "Comma-separated PDB IDs, e.g. 5MOZ,6CM4."},
                    "ligands": {"type": "string", "description": "Semicolon-separated ligands, e.g. aspirin;ethylene[1,3,4]."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_assets",
            "description": "Inspect loaded receptors/ligands and return compact chains/native-ligand inventory.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_workspace",
            "description": "Select receptor workspace, chain, native ligand, and dock ligands. Also updates UI-facing state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "receptor": {"type": "string", "description": "PDB ID, comma-separated IDs, or all."},
                    "chain": {"type": "string", "description": "Chain ID or auto."},
                    "native_ligand": {"type": "string", "description": "Native ligand for grid center or auto."},
                    "dock_ligands": {"type": "string", "description": "Comma-separated SDF filenames or all."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_gridbox",
            "description": "Set gridbox from the main native ligand, current selection, P2Rank/gridfinder fallback, or manual center. Backend computes coordinates. Use this before set_docking_config and build_or_run_queue when preparing a full docking task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "description": "native_ligand, current_selection, p2rank, gridfinder, auto, or manual."},
                    "size": {"type": "number", "description": "Gridbox size in Angstrom, default 20."},
                    "padding": {"type": "number", "description": "Extra size padding, default 0."},
                    "center": {"type": "string", "description": "Manual x,y,z center only when method=manual."},
                    "pocket_rank": {"type": "integer", "description": "Pocket rank when using P2Rank/gridfinder fallback."},
                    "p2rank_mode": {"type": "string", "description": "P2Rank box mode: fit or fixed."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_docking_config",
            "description": "Set docking settings compactly. Use advanced only after read_tool_details('settings'). Call this before build_or_run_queue when preparing a full docking task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "engine": {"type": "string"},
                    "mode": {"type": "string"},
                    "run_count": {"type": "integer", "description": "Repeated runs per job, not total combinations."},
                    "padding": {"type": "number"},
                    "out_root_name": {"type": "string"},
                    "exhaustiveness": {"type": "integer"},
                    "num_modes": {"type": "integer"},
                    "energy_range": {"type": "number"},
                    "cpu": {"type": "integer"},
                    "seed": {"type": "integer"},
                    "ph": {"type": "number"},
                    "advanced": {"type": "string", "description": "Optional key=value pairs separated by semicolons."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_or_run_queue",
            "description": "Build queue only, build and test/log run, or full run. Use action='build_test' for validation-only planning, and action='run_full' for a real docking start. Use replace_queue=false to append a new batch for multi-config experiments. Use only after gridbox and set_docking_config have already succeeded.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "build_only/build, build_test/test/dry_run, or run_full/full/run/start/real_run."},
                    "replace_queue": {"type": "boolean", "description": "true replaces the queue, false appends a new batch. Use false after the first batch for multi-config experiments."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_ligands",
            "description": "Delete ligand SDF files from DockUP. Use target='all' for all ligands or pass specific filenames/names.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "all, or comma/semicolon-separated ligand filenames or names."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_receptors",
            "description": "Delete receptor PDB files and related DockUP receptor/grid/queue state. Use target='all' or specific PDB IDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "all, or comma/semicolon-separated receptor PDB IDs."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_queue_batches",
            "description": "Delete queued docking batches. Use batch_id='all' for every queued batch or pass specific batch IDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "batch_id": {"type": "string", "description": "all, or comma/semicolon-separated queue batch IDs."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_tool_details",
            "description": "Read detailed instructions only when needed, without bloating normal tool results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "workflow, ligand_ranges, asset_resolution, workspace, gridbox, settings, setting_catalog, counts, queue_actions, or tools.",
                    },
                },
            },
        },
    },
]


AVAILABLE_FUNCTIONS = {
    "get_dockup_state": get_dockup_state,
    "fetch_assets": fetch_assets,
    "inspect_assets": inspect_assets,
    "show_in_viewer": show_in_viewer,
    "show_residues": show_residues,
    "select_workspace": select_workspace,
    "set_gridbox": set_gridbox,
    "set_docking_config": set_docking_config,
    "build_or_run_queue": build_or_run_queue,
    "delete_ligands": delete_ligands,
    "delete_receptors": delete_receptors,
    "delete_queue_batches": delete_queue_batches,
    "read_tool_details": read_tool_details,
}


def run_agent(
    *,
    base_url: str,
    model: str,
    prompt: str,
    options: dict[str, Any] | None = None,
    think: bool | None = None,
    test_mode: bool = True,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    raise RuntimeError("run_agent has been retired. Use the interactive DockUP agent loop instead.")
