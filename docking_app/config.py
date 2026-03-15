from __future__ import annotations

from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
PACKAGE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = PACKAGE_DIR / "workspace"

DATA_DIR = WORKSPACE_DIR / "data"
LIGAND_DIR = DATA_DIR / "ligand"
RECEPTOR_DIR = DATA_DIR / "receptor"
DOCK_DIR = DATA_DIR / "dock"
POCKET_FINDER_DIR = DATA_DIR / ".pocket_finder"
PLIP_DIR = WORKSPACE_DIR / "plip-2.4.0"

TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"

for _dir in (WORKSPACE_DIR, DATA_DIR, LIGAND_DIR, RECEPTOR_DIR, DOCK_DIR, POCKET_FINDER_DIR):
    _dir.mkdir(parents=True, exist_ok=True)
