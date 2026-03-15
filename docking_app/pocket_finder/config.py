from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..config import BASE, POCKET_FINDER_DIR, WORKSPACE_DIR

P2RANK_ENV_VAR = "DOCKUP_P2RANK_BIN"


def candidate_p2rank_paths() -> list[Path]:
    env = os.environ.get(P2RANK_ENV_VAR, "").strip()
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env).expanduser())

    candidates.extend(
        [
            WORKSPACE_DIR / "tools" / "p2rank" / "distro" / "prank",
            BASE.parent / "pocket_test" / "p2rank" / "distro" / "prank",
        ]
    )

    which_hit = shutil.which("prank")
    if which_hit:
        candidates.append(Path(which_hit))
    return candidates


def resolve_p2rank_bin() -> Path:
    for candidate in candidate_p2rank_paths():
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved.exists() and resolved.is_file():
            return resolved
    raise FileNotFoundError(
        "P2Rank executable not found. Set DOCKUP_P2RANK_BIN or install prank."
    )


def receptor_run_dir(pdb_id: str) -> Path:
    return POCKET_FINDER_DIR / pdb_id.upper()
