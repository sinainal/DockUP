from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RUN_RE = re.compile(r"^run(\d+)$", re.IGNORECASE)
CASE_RUN_RE = re.compile(r"^([A-Za-z0-9]+)_(.+)_run(\d+)$", re.IGNORECASE)
RECEPTOR_DTYPE_RE = re.compile(r"^D(\d+)$", re.IGNORECASE)
METADATA_FILENAME = ".docking_app_meta.json"
SKIP_NAMES = {
    "__pycache__",
    "plip",
    "plots",
    "render_images",
    "reports",
    "report_outputs",
    ".tmp_render",
}


@dataclass(frozen=True)
class SourceMetadata:
    main_type: str
    receptor_labels: dict[str, str]
    ligand_labels: dict[str, str]
    receptor_order: tuple[str, ...]
    ligand_order: tuple[str, ...]

    def receptor_display(self, receptor_id: str) -> str:
        return self.receptor_labels.get(receptor_id, prettify_label(receptor_id))

    def ligand_display(self, ligand_id: str) -> str:
        return self.ligand_labels.get(ligand_id, prettify_label(ligand_id, trim_run_suffix=True))


def prettify_label(name: str, *, trim_run_suffix: bool = False) -> str:
    text = str(name or "").strip()
    if trim_run_suffix:
        text = re.sub(r"_\d+$", "", text)
    text = re.sub(r"[_-]+", " ", text).strip()
    return text or str(name or "")


def run_sort_key(name: str) -> tuple[int, int, str]:
    match = RUN_RE.fullmatch(name or "")
    if match:
        return (0, int(match.group(1)), (name or "").lower())
    return (1, 10**9, (name or "").lower())


def receptor_sort_key(name: str) -> tuple[int, int, str]:
    match = RECEPTOR_DTYPE_RE.fullmatch(name or "")
    if match:
        return (0, int(match.group(1)), (name or "").upper())
    return (1, 10**9, (name or "").lower())


def ligand_sort_key(name: str) -> tuple[str, str]:
    return (prettify_label(name, trim_run_suffix=True).lower(), (name or "").lower())


def _valid_run_dir(run_dir: Path, required_files: tuple[str, ...]) -> bool:
    if not run_dir.exists() or not run_dir.is_dir():
        return False
    for rel_file in required_files:
        if not (run_dir / rel_file).exists():
            return False
    return True


def collect_inventory(
    root: Path,
    *,
    required_files: tuple[str, ...] = ("plip/report.xml",),
) -> dict[str, dict[str, list[tuple[str, Path]]]]:
    root = root.resolve()
    inventory: dict[str, dict[str, list[tuple[str, Path]]]] = {}
    if not root.exists() or not root.is_dir():
        return inventory

    def add_run(receptor_id: str, ligand_name: str, run_name: str, run_dir: Path) -> None:
        receptor_key = str(receptor_id or "").strip()
        ligand_key = str(ligand_name or "").strip()
        run_key = str(run_name or "").strip()
        if not receptor_key or not ligand_key or not run_key:
            return
        receptor_bucket = inventory.setdefault(receptor_key, {})
        run_bucket = receptor_bucket.setdefault(ligand_key, [])
        resolved = run_dir.resolve()
        if any(existing_name == run_key and existing_dir == resolved for existing_name, existing_dir in run_bucket):
            return
        run_bucket.append((run_key, resolved))

    def collect_hierarchical(base_dir: Path, receptor_id: str) -> None:
        if not base_dir.exists() or not base_dir.is_dir():
            return
        for ligand_dir in sorted((p for p in base_dir.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
            if ligand_dir.name.startswith(".") or ligand_dir.name in SKIP_NAMES:
                continue
            run_dirs = [p for p in ligand_dir.iterdir() if p.is_dir() and RUN_RE.fullmatch(p.name)]
            for run_dir in sorted(run_dirs, key=lambda p: run_sort_key(p.name)):
                if _valid_run_dir(run_dir, required_files):
                    add_run(receptor_id, ligand_dir.name, run_dir.name, run_dir)

    collect_hierarchical(root, root.name)
    for receptor_dir in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
        if receptor_dir.name.startswith(".") or receptor_dir.name in SKIP_NAMES:
            continue
        collect_hierarchical(receptor_dir, receptor_dir.name)

    for case_dir in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
        if case_dir.name.startswith(".") or case_dir.name in SKIP_NAMES:
            continue
        matched = CASE_RUN_RE.fullmatch(case_dir.name)
        if not matched:
            continue
        if not _valid_run_dir(case_dir, required_files):
            continue
        receptor_id = matched.group(1)
        ligand_name = matched.group(2)
        run_name = f"run{int(matched.group(3))}"
        add_run(receptor_id, ligand_name, run_name, case_dir)

    for receptor_id, ligand_map in inventory.items():
        for ligand_name, run_entries in ligand_map.items():
            run_entries.sort(key=lambda item: run_sort_key(item[0]))

    return inventory


def inventory_entities(inventory: dict[str, dict[str, list[tuple[str, Path]]]]) -> tuple[list[str], list[str]]:
    receptors = sorted(inventory.keys(), key=receptor_sort_key)
    ligands: set[str] = set()
    for ligand_map in inventory.values():
        ligands.update(ligand_map.keys())
    return receptors, sorted(ligands, key=ligand_sort_key)


def _default_main_type(root: Path) -> str:
    default = prettify_label(root.name)
    if default.lower() in {
        "data",
        "dock",
        "results",
        "dimer final",
        "dimer final linked",
        "dimer full",
        "report outputs",
    }:
        return ""
    return default


def _normalize_map(raw_map: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(raw_map, dict):
        return out
    for key, value in raw_map.items():
        raw_key = str(key or "").strip()
        raw_value = str(value or "").strip()
        if raw_key and raw_value:
            out[raw_key] = raw_value
    return out


def _normalize_order(raw_list: Any, allowed_items: list[str]) -> list[str]:
    allowed = [str(item) for item in allowed_items if str(item)]
    allowed_set = set(allowed)
    out: list[str] = []
    seen: set[str] = set()

    if isinstance(raw_list, list):
        for item in raw_list:
            key = str(item or "").strip()
            if not key or key in seen or key not in allowed_set:
                continue
            seen.add(key)
            out.append(key)

    for key in allowed:
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def load_source_metadata(root: Path, receptors: list[str], ligands: list[str]) -> SourceMetadata:
    root = root.resolve()
    metadata_file = root / METADATA_FILENAME
    raw: dict[str, Any] = {}
    if metadata_file.exists() and metadata_file.is_file():
        try:
            raw = json.loads(metadata_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = {}

    receptor_order = _normalize_order(raw.get("receptor_order"), receptors)
    ligand_order = _normalize_order(raw.get("ligand_order"), ligands)
    receptor_overrides = _normalize_map(raw.get("receptor_labels"))
    ligand_overrides = _normalize_map(raw.get("ligand_labels"))

    receptor_labels: dict[str, str] = {}
    for receptor_id in receptor_order:
        receptor_labels[receptor_id] = receptor_overrides.get(receptor_id, prettify_label(receptor_id))

    ligand_labels: dict[str, str] = {}
    for ligand_id in ligand_order:
        ligand_labels[ligand_id] = ligand_overrides.get(ligand_id, prettify_label(ligand_id, trim_run_suffix=True))

    main_type = str(raw.get("main_type") or "").strip() or _default_main_type(root)
    return SourceMetadata(
        main_type=main_type,
        receptor_labels=receptor_labels,
        ligand_labels=ligand_labels,
        receptor_order=tuple(receptor_order),
        ligand_order=tuple(ligand_order),
    )
