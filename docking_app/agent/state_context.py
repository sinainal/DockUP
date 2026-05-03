from __future__ import annotations

from typing import Any

from .autonomous_docking import AGENT_STATE
from ..helpers import normalize_docking_config
from ..state import RUN_STATE, STATE


def _queue_batch_context(queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    batches: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in queue:
        if not isinstance(row, dict):
            continue
        batch_id = str(row.get("batch_id") or "unbatched").strip() or "unbatched"
        if batch_id not in batches:
            cfg = normalize_docking_config(row.get("docking_config") or STATE.get("docking_config") or {})
            try:
                run_count = max(1, int(row.get("run_count") or STATE.get("runs") or 1))
            except (TypeError, ValueError):
                run_count = 1
            batches[batch_id] = {
                "batch_id": batch_id,
                "job_count": 0,
                "run_count": run_count,
                "total_runs": 0,
                "mode": str(row.get("job_type") or row.get("mode") or STATE.get("mode", "Docking")),
                "out_root_name": str(row.get("out_root_name") or STATE.get("out_root_name") or ""),
                "docking_config": {
                    "docking_engine": cfg.get("docking_engine"),
                    "docking_mode": cfg.get("docking_mode"),
                    "ligand_binding_mode": cfg.get("ligand_binding_mode"),
                    "pdb2pqr_ph": cfg.get("pdb2pqr_ph"),
                    "vina_exhaustiveness": cfg.get("vina_exhaustiveness"),
                    "vina_num_modes": cfg.get("vina_num_modes"),
                    "vina_energy_range": cfg.get("vina_energy_range"),
                    "vina_cpu": cfg.get("vina_cpu"),
                    "vina_seed": cfg.get("vina_seed"),
                },
            }
            order.append(batch_id)
        batches[batch_id]["job_count"] += 1
    for batch in batches.values():
        batch["total_runs"] = int(batch.get("job_count") or 0) * int(batch.get("run_count") or 1)
    return [batches[batch_id] for batch_id in order[:8]]


def docking_state_context() -> dict[str, Any]:
    queue = list(STATE.get("queue") or [])
    docking_config = normalize_docking_config(STATE.get("docking_config") or {})
    queue_batches = _queue_batch_context(queue)
    run_source = queue_batches[0].get("run_count") if queue_batches else STATE.get("runs")
    try:
        run_count = max(1, int(run_source or 1))
    except (TypeError, ValueError):
        run_count = 1

    recent_actions_raw = list(AGENT_STATE.get("recent_actions") or [])
    recent_actions: list[dict[str, Any]] = []
    for row in recent_actions_raw[-4:]:
        if not isinstance(row, dict):
            continue
        recent_actions.append(
            {
                "step": row.get("step"),
                "kind": str(row.get("kind") or "").strip(),
                "tool": str(row.get("tool") or "").strip(),
                "summary": str(row.get("summary") or "").strip(),
                "ok": bool(row.get("ok", True)),
            }
        )

    workflow_stage = str(AGENT_STATE.get("workflow_stage") or "").strip()
    if not workflow_stage:
        if str(RUN_STATE.get("status") or "").strip() not in {"", "idle"}:
            workflow_stage = "running"
        elif AGENT_STATE.get("batch_id"):
            workflow_stage = "queued"
        elif AGENT_STATE.get("batch_config"):
            workflow_stage = "batch_configured"
        elif AGENT_STATE.get("grid_data"):
            workflow_stage = "grid_ready"
        elif AGENT_STATE.get("setup_rows"):
            workflow_stage = "workspace_selected"
        elif AGENT_STATE.get("inventory"):
            workflow_stage = "assets_loaded"
        else:
            workflow_stage = "idle"

    active_preview = ", ".join(str(name) for name in list(STATE.get("active_ligands") or [])[:3] if str(name or "").strip()) or "-"
    state_summary = (
        f"stage={workflow_stage}; "
        f"receptor={str(STATE.get('selected_receptor') or '-').strip() or '-'}; "
        f"chain={str(STATE.get('selected_chain') or 'all').strip() or 'all'}; "
        f"ligand={str(STATE.get('selected_ligand') or '-').strip() or '-'}; "
        f"active_ligands={len(list(STATE.get('active_ligands') or []))}[{active_preview}]; "
        f"queue={len(queue)}; "
        f"run={str(RUN_STATE.get('status') or 'idle').strip() or 'idle'}"
    )

    return {
        "mode": STATE.get("mode", "Docking"),
        "workflow_stage": workflow_stage,
        "state_summary": state_summary,
        "selected_receptor": STATE.get("selected_receptor", ""),
        "selected_chain": STATE.get("selected_chain", "all"),
        "selected_ligand": STATE.get("selected_ligand", ""),
        "active_ligands": list(STATE.get("active_ligands") or []),
        "queue_count": len(queue),
        "queue_job_count": len(queue),
        "run_count": run_count,
        "queue_total_runs": sum(int(batch.get("total_runs") or 0) for batch in queue_batches),
        "queue_batches": queue_batches,
        "run_status": RUN_STATE.get("status", "idle"),
        "run_out_root": RUN_STATE.get("out_root", ""),
        "docking_config": docking_config,
        "agent_memory": {
            "workflow_stage": workflow_stage,
            "last_tool": str(AGENT_STATE.get("last_tool") or "").strip(),
            "last_tool_summary": str(AGENT_STATE.get("last_tool_summary") or "").strip(),
            "last_answer": str(AGENT_STATE.get("last_answer") or "").strip(),
            "last_error": str(AGENT_STATE.get("last_error") or "").strip(),
            "memory_summary": str(AGENT_STATE.get("memory_summary") or "").strip(),
            "recent_actions": recent_actions,
        },
    }


def state_system_prompt() -> str:
    from .agent_runtime import AGENT_SYSTEM_PROMPT

    return AGENT_SYSTEM_PROMPT
