from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import docking_app.services as services
from docking_app.config import RECEPTOR_DIR


def test_load_receptor_meta_ignores_unrequested_local_files(monkeypatch):
    receptor_path = RECEPTOR_DIR / "6CM4.pdb"
    receptor_text = receptor_path.read_text(encoding="utf-8", errors="ignore")
    pdb_files = services._existing_files(RECEPTOR_DIR, (".pdb",))

    monkeypatch.setattr(
        services,
        "_fetch_pdb_text",
        lambda pdb_id: receptor_text if str(pdb_id).upper() == "6CM4" else None,
    )

    meta = services._load_receptor_meta(["6CM4"], pdb_files)

    assert [row["pdb_id"] for row in meta] == ["6CM4"]


def test_summarize_receptors_counts_unique_ligands_once():
    summary = services._summarize_receptors(
        [
            {
                "pdb_id": "6CM4",
                "pdb_file": "/tmp/6CM4.pdb",
                "chains": ["all", "A", "B"],
                "ligands_by_chain": {
                    "A": ["LIG 1", "LIG 2"],
                    "B": ["LIG 2", "LIG 3"],
                    "all": ["LIG 1", "LIG 2", "LIG 3"],
                },
            }
        ]
    )

    assert summary[0]["ligands"] == 3
