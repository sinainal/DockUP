"""Shared utility functions used across the docking_app package.

Consolidates previously duplicated helpers from routes.py and services.py.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from .config import BASE, DATA_DIR, DOCK_DIR, WORKSPACE_DIR
from .state import DOCKING_CONFIG_DEFAULTS

logger = logging.getLogger(__name__)

LIGAND_TIMESTAMP_SUFFIX_RE = re.compile(r"_(\d{8}_\d{6})(?:_\d+)?$", re.IGNORECASE)
LIGAND_DUPLICATE_SUFFIX_RE = re.compile(r"^(?P<base>.+?)_(?P<index>\d+)$")

# Resolved path constants for safety checks
BASE_RESOLVED = BASE.resolve()
DATA_DIR_RESOLVED = DATA_DIR.resolve()
DOCK_DIR_RESOLVED = DOCK_DIR.resolve()
WORKSPACE_RESOLVED = WORKSPACE_DIR.resolve()


# ---------------------------------------------------------------------------
# Type coercion helpers
# ---------------------------------------------------------------------------

def boolish(value: Any, default: bool) -> bool:
    """Convert a value to bool, accepting common truthy/falsy strings."""
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def to_optional_int(
    value: Any,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    """Parse *value* to int, clamping to [minimum, maximum]. Returns None on empty/invalid."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        val = int(float(text))
    except (TypeError, ValueError):
        return None
    if minimum is not None:
        val = max(minimum, val)
    if maximum is not None:
        val = min(maximum, val)
    return val


def to_optional_float(
    value: Any,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    """Parse *value* to float, clamping to [minimum, maximum]. Returns None on empty/invalid."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        val = float(text)
    except (TypeError, ValueError):
        return None
    if minimum is not None:
        val = max(minimum, val)
    if maximum is not None:
        val = min(maximum, val)
    return val


def normalize_docking_mode(value: Any) -> str:
    """Return the supported docking mode string."""
    mode = str(value or "").strip().lower()
    return "flexible" if mode == "flexible" else "standard"


def parse_flex_residue_spec(raw: Any) -> list[dict[str, str]]:
    """Parse ``A:114,A:118`` style specs into residue rows."""
    text = str(raw or "").strip()
    if not text:
        return []
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for chunk in text.split(","):
        token = str(chunk or "").strip()
        if not token:
            continue
        parts = [part.strip() for part in token.split(":") if str(part or "").strip()]
        if len(parts) < 2:
            continue
        chain = parts[0]
        resno = parts[1]
        resname = parts[2].upper() if len(parts) > 2 else ""
        key = (chain, resno, resname)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"chain": chain, "resno": resno, "resname": resname})
    return rows


def normalize_flex_residue_list(raw: Any) -> list[dict[str, str]]:
    """Normalize flex residue rows from list/dict/string input."""
    if isinstance(raw, str):
        return parse_flex_residue_spec(raw)
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw:
        if isinstance(item, str):
            candidates = parse_flex_residue_spec(item)
        elif isinstance(item, dict):
            chain = str(item.get("chain") or "").strip()
            resno = str(item.get("resno") or item.get("resid") or item.get("residue_number") or "").strip()
            resname = str(item.get("resname") or item.get("residue_name") or "").strip().upper()
            candidates = [{"chain": chain, "resno": resno, "resname": resname}] if chain and resno else []
        else:
            candidates = []
        for row in candidates:
            chain = str(row.get("chain") or "").strip()
            resno = str(row.get("resno") or "").strip()
            resname = str(row.get("resname") or "").strip().upper()
            if not chain or not resno:
                continue
            key = (chain, resno, resname)
            if key in seen:
                continue
            seen.add(key)
            rows.append({"chain": chain, "resno": resno, "resname": resname})
    return rows


def build_flex_residue_spec(rows: Any) -> str:
    """Serialize normalized flex residue rows into a Meeko/Vina residue spec."""
    normalized = normalize_flex_residue_list(rows)
    return ",".join(f"{row['chain']}:{row['resno']}" for row in normalized if row.get("chain") and row.get("resno"))


def normalize_ligand_name_list(raw: Any) -> list[str]:
    """Normalize a ligand name payload into a stable deduplicated list."""
    if isinstance(raw, str):
        values = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(raw, list):
        values = [str(part or "").strip() for part in raw]
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = str(value or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def normalize_ligand_db_filename(filename: str) -> str:
    """Normalize ligand storage names while stripping generator timestamps."""
    src = Path(str(filename or "").strip())
    suffix = src.suffix.lower() or ".sdf"
    stem = str(src.stem or "ligand").strip()
    stem = LIGAND_TIMESTAMP_SUFFIX_RE.sub("", stem).strip("._-")
    if not stem:
        stem = "ligand"
    return f"{stem}{suffix}"


def next_available_ligand_path(directory: Path, filename: str) -> Path:
    """Return a stable deduplicated ligand path inside *directory*."""
    directory = Path(directory).expanduser().resolve()
    normalized_name = normalize_ligand_db_filename(filename)
    stem = Path(normalized_name).stem
    suffix = Path(normalized_name).suffix or ".sdf"

    duplicate_match = LIGAND_DUPLICATE_SUFFIX_RE.fullmatch(stem)
    if duplicate_match:
        candidate_base = str(duplicate_match.group("base") or "").strip("._-")
        if candidate_base:
            unsuffixed = directory / f"{candidate_base}{suffix}"
            siblings_pattern = f"{candidate_base}_[0-9]*{suffix}"
            if unsuffixed.exists() or any(directory.glob(siblings_pattern)):
                stem = candidate_base

    candidate = directory / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate

    idx = 1
    while True:
        next_path = directory / f"{stem}_{idx}{suffix}"
        if not next_path.exists():
            return next_path
        idx += 1


def find_identical_file_by_bytes(
    directory: Path,
    content: bytes,
    *,
    suffixes: tuple[str, ...] = (),
    preferred_name: str = "",
) -> Path | None:
    """Return an existing file whose byte content matches *content*."""
    directory = Path(directory).expanduser().resolve()
    suffix_filter = {str(suffix or "").lower() for suffix in suffixes if str(suffix or "").strip()}
    content_size = len(content)
    matches: list[Path] = []

    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return None

    for path in entries:
        if not path.is_file():
            continue
        if suffix_filter and path.suffix.lower() not in suffix_filter:
            continue
        try:
            if path.stat().st_size != content_size:
                continue
            if path.read_bytes() != content:
                continue
        except OSError:
            continue
        matches.append(path)

    if not matches:
        return None

    normalized_preferred = normalize_ligand_db_filename(preferred_name) if preferred_name else ""
    matches.sort(
        key=lambda path: (
            len(path.stem),
            path.name != normalized_preferred,
            path.name.lower(),
        )
    )
    return matches[0]


# ---------------------------------------------------------------------------
# Docking configuration normalisation
# ---------------------------------------------------------------------------

def normalize_docking_config(raw: Any) -> dict[str, Any]:
    """Normalise a raw docking configuration dict into a well-typed dict."""
    source = raw if isinstance(raw, dict) else {}
    defaults = dict(DOCKING_CONFIG_DEFAULTS)
    ligand_binding_mode = str(source.get("ligand_binding_mode", defaults.get("ligand_binding_mode", "single")) or "single").strip().lower()
    ligand_binding_mode = "multi_ligand" if ligand_binding_mode in {"multi_ligand", "multi-ligand"} else "single"
    cfg: dict[str, Any] = {
        "docking_mode": normalize_docking_mode(source.get("docking_mode", defaults.get("docking_mode", "standard"))),
        "ligand_binding_mode": ligand_binding_mode,
        "pdb2pqr_ph": defaults["pdb2pqr_ph"],
        "pdb2pqr_ff": str(source.get("pdb2pqr_ff", defaults["pdb2pqr_ff"]) or defaults["pdb2pqr_ff"]).strip() or defaults["pdb2pqr_ff"],
        "pdb2pqr_ffout": str(source.get("pdb2pqr_ffout", defaults["pdb2pqr_ffout"]) or defaults["pdb2pqr_ffout"]).strip() or defaults["pdb2pqr_ffout"],
        "pdb2pqr_nodebump": boolish(source.get("pdb2pqr_nodebump", defaults["pdb2pqr_nodebump"]), defaults["pdb2pqr_nodebump"]),
        "pdb2pqr_keep_chain": boolish(source.get("pdb2pqr_keep_chain", defaults["pdb2pqr_keep_chain"]), defaults["pdb2pqr_keep_chain"]),
        "mkrec_allow_bad_res": boolish(source.get("mkrec_allow_bad_res", defaults["mkrec_allow_bad_res"]), defaults["mkrec_allow_bad_res"]),
        "mkrec_default_altloc": str(source.get("mkrec_default_altloc", defaults["mkrec_default_altloc"]) or defaults["mkrec_default_altloc"]).strip() or defaults["mkrec_default_altloc"],
        "vina_exhaustiveness": defaults["vina_exhaustiveness"],
        "vina_num_modes": None,
        "vina_energy_range": None,
        "vina_cpu": None,
        "vina_seed": None,
    }
    ph_val = to_optional_float(source.get("pdb2pqr_ph"), 0.0, 14.0)
    if ph_val is not None:
        cfg["pdb2pqr_ph"] = ph_val
    ex_val = to_optional_int(source.get("vina_exhaustiveness"), 1, 512)
    if ex_val is not None:
        cfg["vina_exhaustiveness"] = ex_val
    cfg["vina_num_modes"] = to_optional_int(source.get("vina_num_modes"), 1, 200)
    cfg["vina_energy_range"] = to_optional_float(source.get("vina_energy_range"), 0.0, 1000.0)
    cfg["vina_cpu"] = to_optional_int(source.get("vina_cpu"), 1, 512)
    cfg["vina_seed"] = to_optional_int(source.get("vina_seed"), 0, None)
    return cfg


# ---------------------------------------------------------------------------
# Manifest value helpers
# ---------------------------------------------------------------------------

def restore_manifest_value(raw: Any) -> str:
    """Restore a manifest value, converting the ``__EMPTY__`` sentinel to ``""``."""
    value = str(raw or "").strip()
    return "" if value == "__EMPTY__" else value


# ---------------------------------------------------------------------------
# Path / display helpers
# ---------------------------------------------------------------------------

def to_display_path(path: Path) -> str:
    """Convert an absolute *path* to a user-friendly relative string."""
    resolved = path.resolve()
    try:
        rel_to_data = resolved.relative_to(DATA_DIR_RESOLVED)
        rel_str = str(rel_to_data).replace("\\", "/")
        return "data" if rel_str in {"", "."} else f"data/{rel_str}"
    except ValueError:
        pass
    try:
        rel_to_base = resolved.relative_to(BASE_RESOLVED)
        rel_str = str(rel_to_base).replace("\\", "/")
        return "." if rel_str in {"", "."} else rel_str
    except ValueError:
        return str(resolved).replace("\\", "/")


def relative_to_base(path: Path) -> str | None:
    """Return a display-path only if *path* is inside BASE or WORKSPACE; otherwise ``None``."""
    resolved = path.resolve()
    if (
        resolved != BASE_RESOLVED and BASE_RESOLVED not in resolved.parents
        and resolved != WORKSPACE_RESOLVED and WORKSPACE_RESOLVED not in resolved.parents
    ):
        return None
    return to_display_path(resolved)


def resolve_dock_directory(
    path_text: str,
    *,
    default: Path,
    allow_create: bool,
) -> Path:
    """Resolve a user-provided path so that it stays inside DOCK_DIR."""
    from fastapi import HTTPException

    def _rebase_to_dock(raw_text: str) -> Path | None:
        raw_candidate = Path(str(raw_text or "").strip().replace("\\", "/")).expanduser()
        parts = [part for part in raw_candidate.parts if part not in {"", "."}]
        if not parts:
            return None
        lowered = [part.lower() for part in parts]
        idx = None
        for i in range(len(lowered) - 1):
            if lowered[i] == "data" and lowered[i + 1] == "dock":
                idx = i
                break
        if idx is None:
            return None
        tail = [part for part in parts[idx + 2 :] if part not in {"", "."}]
        if any(part == ".." for part in tail):
            return None
        rebased = DOCK_DIR_RESOLVED / Path(*tail) if tail else DOCK_DIR_RESOLVED
        return rebased.resolve()

    raw = str(path_text or "").strip()
    if not raw:
        return default.resolve()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        # Try WORKSPACE_DIR first (data/dock lives there), then BASE
        ws = (WORKSPACE_DIR / candidate).resolve()
        if ws.exists() and ws.is_dir():
            candidate = ws
        else:
            candidate = (BASE / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if candidate != DOCK_DIR_RESOLVED and DOCK_DIR_RESOLVED not in candidate.parents:
        rebased = _rebase_to_dock(raw)
        if rebased is not None:
            candidate = rebased
        if candidate != DOCK_DIR_RESOLVED and DOCK_DIR_RESOLVED not in candidate.parents:
            raise HTTPException(status_code=400, detail="Path must be inside data/dock.")
    if candidate.exists():
        if not candidate.is_dir():
            raise HTTPException(status_code=400, detail="Path is not a directory.")
    else:
        if not allow_create:
            raise HTTPException(status_code=400, detail="Path not found.")
        candidate.mkdir(parents=True, exist_ok=True)
    return candidate


# ---------------------------------------------------------------------------
# File-system helpers
# ---------------------------------------------------------------------------

def safe_mtime(path: Path) -> float:
    """Return file mtime or ``0.0`` on any OS error."""
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return 0.0


def read_json(path: Path, default: Any) -> Any:
    """Read JSON from *path*, returning *default* on any failure."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("read_json failed for %s: %s", path, exc)
        return default


def write_json(path: Path, payload: Any) -> None:
    """Atomically write *payload* as JSON to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp.replace(path)


def timestamp_token() -> str:
    """Return a compact timestamp suitable for file-name suffixes."""
    return time.strftime("%Y%m%d_%H%M%S")
