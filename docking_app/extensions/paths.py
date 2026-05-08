from __future__ import annotations

import os
from pathlib import Path

from ..config import BASE


def venv_dir() -> Path:
    return Path(os.environ.get("DOCKUP_VENV", BASE / ".venv")).expanduser()


def extensions_dir() -> Path:
    raw = os.environ.get("DOCKUP_EXTENSIONS_DIR", "").strip()
    return Path(raw).expanduser() if raw else venv_dir() / "dockup_extensions"


def extension_root(extension_id: str) -> Path:
    return extensions_dir() / extension_id


def extension_state_path(extension_id: str) -> Path:
    return extension_root(extension_id) / "state.json"


def extension_log_path(extension_id: str, name: str = "install.log") -> Path:
    return extension_root(extension_id) / name
