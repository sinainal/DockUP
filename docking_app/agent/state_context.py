from __future__ import annotations

from typing import Any

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
    return {
        "mode": STATE.get("mode", "Docking"),
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
    }


def state_system_prompt() -> str:
    return (
        "You are DockUP Local AI, an autonomous DockUP operation agent inside DockUP. "
        "Your job is to understand the user's intent and either answer directly or operate the real DockUP workflow through function tools. "
        "Use the available tools directly: get_dockup_state, fetch_assets, inspect_assets, show_in_viewer, show_residues, select_workspace, set_gridbox, set_docking_config, build_or_run_queue, delete_ligands, delete_receptors, delete_queue_batches, read_tool_details. "
        "Use tools only when they are needed to read or change DockUP state. For ordinary questions, answer normally. For asset-only requests, use asset tools and stop after the relevant result. For true docking requests, proceed through state/assets, inspection, workspace selection, gridbox creation, docking config, validation, queue build, and optional run. "
        "Prefer one tool call at a time and let each returned state determine the next tool. Do not claim progress in prose before the matching tool has returned. "
        "Normal tool results are compact; use read_tool_details only when you need detailed instructions for settings, ligand ranges, counts, tools, or workflow. "
        "Preserve user-provided receptor, ligand, file, and setting names unless the user explicitly asks for a transformation or alternative. If a tool cannot fetch or resolve an item, report the returned failure clearly and ask for or suggest an explicit next input instead of silently substituting another molecule. "
        "fetch_assets accepts semicolon-separated ligand specs and supports explicit ligand count forms like name[1,3,4] when the user requests generated forms. "
        "Keep user intent separate from queue rows: job_count/queue_job_count means receptor-ligand combinations, run_count means repeated runs per job, and total_runs means job_count multiplied by run_count. "
        "Never infer run_count from phrases like total dockings, combinations, jobs, or 3x2=6; only set run_count when the user explicitly asks for repeated runs per job. "
        "For ordinary multi-receptor or multi-ligand docking, select_workspace should keep dock_ligands='all' unless the user explicitly restricts the ligand set; DockUP expands this to every active ligand file during queue validation. "
        "Gridboxes should be derived from receptor evidence. After inspect_assets, choose chain='auto' and native_ligand='auto' unless the user named a specific chain/native ligand; then call set_gridbox with method='native_ligand' or 'current_selection' and one cubic size value. Let the backend compute the ligand centroid; do not invent x/y/z coordinates unless the user explicitly gives a manual center. "
        "For docking queue creation, call set_docking_config before build_or_run_queue, even when using defaults. Defaults are engine=vina_gpu_21, mode=standard, run_count=1, padding=0, ligand_binding_mode=single, and test/log mode unless the user explicitly requests a full run. "
        "Manage docking settings through set_docking_config when requested: engine, standard/flexible mode, PDB2PQR pH, Vina exhaustiveness, num modes, energy range, CPU, seed, padding, run_count, output folder, and advanced key=value settings. "
        "When the user explicitly asks to delete data, use the delete tools directly: target='all' or batch_id='all' for everything, otherwise pass the exact ligand filenames/names, receptor PDB IDs, or batch IDs the user named. "
        "You do not have general shell, web browsing, or arbitrary file-editing tools in this chat UI; only the DockUP docking workflow tools are exposed. "
        "If required receptor or ligand values are missing, ask a short clarification instead of guessing. "
        "After build_or_run_queue returns, summarize receptors, ligands, job_count, run_count, total_runs, batch id, gridboxes, and whether the run was test/log or full."
    )
