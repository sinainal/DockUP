from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from docking_app.helpers import find_identical_file_by_bytes
from docking_app.helpers import next_available_ligand_path, normalize_ligand_db_filename


def test_next_available_ligand_path_keeps_original_root_for_duplicate_suffixes(tmp_path):
    (tmp_path / "ethylene.sdf").write_text("", encoding="utf-8")
    (tmp_path / "ethylene_1.sdf").write_text("", encoding="utf-8")

    target = next_available_ligand_path(tmp_path, "ethylene_1.sdf")

    assert target.name == "ethylene_2.sdf"


def test_next_available_ligand_path_strips_generated_timestamp_suffixes(tmp_path):
    (tmp_path / "ethylene.sdf").write_text("", encoding="utf-8")

    target = next_available_ligand_path(tmp_path, "ethylene_20260324_153000.sdf")

    assert target.name == "ethylene_1.sdf"


def test_normalize_ligand_db_filename_preserves_plain_duplicate_suffixes():
    assert normalize_ligand_db_filename("ethylene_1.sdf") == "ethylene_1.sdf"


def test_find_identical_file_by_bytes_prefers_root_name(tmp_path):
    root = tmp_path / "ethylene.sdf"
    duplicate = tmp_path / "ethylene_1.sdf"
    root.write_bytes(b"same")
    duplicate.write_bytes(b"same")

    match = find_identical_file_by_bytes(
        tmp_path,
        b"same",
        suffixes=(".sdf",),
        preferred_name="ethylene_1.sdf",
    )

    assert match is not None
    assert match.name == "ethylene.sdf"
