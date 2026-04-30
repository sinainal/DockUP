from __future__ import annotations

from typing import Any

from ..helpers import normalize_docking_config
from ..state import RUN_STATE, STATE


def docking_state_context() -> dict[str, Any]:
    queue = list(STATE.get("queue") or [])
    docking_config = normalize_docking_config(STATE.get("docking_config") or {})
    return {
        "mode": STATE.get("mode", "Docking"),
        "selected_receptor": STATE.get("selected_receptor", ""),
        "selected_chain": STATE.get("selected_chain", "all"),
        "selected_ligand": STATE.get("selected_ligand", ""),
        "active_ligands": list(STATE.get("active_ligands") or []),
        "queue_count": len(queue),
        "run_status": RUN_STATE.get("status", "idle"),
        "run_out_root": RUN_STATE.get("out_root", ""),
        "docking_config": docking_config,
    }


def state_system_prompt() -> str:
    return (
        "You are DockUP Local AI, a concise docking-focused assistant inside DockUP. "
        "You can explain the current DockUP state and suggest next manual steps, but you cannot run tools, "
        "fetch molecules, edit docking settings, or start docking in this first version. "
        "If the user asks you to perform an action, explain that action execution will arrive in a future tool-calling version."
    )

