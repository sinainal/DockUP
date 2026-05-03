from __future__ import annotations

from typing import Any, Callable

from ...control import actions


def _error_from_envelope(envelope: dict[str, Any]) -> str:
    error = envelope.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "").strip()
    return str(error or "").strip()


def _base_result(envelope: dict[str, Any], *, summary: str = "") -> dict[str, Any]:
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    result = {
        "ok": bool(envelope.get("ok")),
        "summary": summary or str(envelope.get("message") or data.get("summary") or ""),
        "control_action": envelope.get("action"),
        "control_trace_id": envelope.get("trace_id"),
        "ui_hints": envelope.get("ui_hints") if isinstance(envelope.get("ui_hints"), dict) else {},
    }
    error = _error_from_envelope(envelope)
    if error:
        result["error"] = error
    return result


def show_in_viewer(receptor: str = "", chain: str = "", native_ligand: str = "") -> dict[str, Any]:
    envelope = actions.show_viewer(receptor or "", chain=chain or "")
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    result = _base_result(envelope)
    result.update(
        {
            "selected_receptor": data.get("pdb_id") or receptor,
            "selected_chain": data.get("selected_chain") or chain or "all",
            "selected_native_ligand": data.get("selected_ligand") or native_ligand or "",
            "chains": data.get("chains") or [],
            "ligands_by_chain": data.get("ligands_by_chain") or {},
            "allowed_next_tools": ["select_workspace", "set_gridbox", "fetch_assets"],
        }
    )
    return result


def show_residues(receptor: str = "", residue: str = "TRP", chain: str = "all") -> dict[str, Any]:
    envelope = actions.show_residues(receptor or "", residue=residue or "TRP", chain=chain or "all")
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    result = _base_result(envelope)
    result.update(
        {
            "receptor": data.get("receptor") or receptor,
            "residue": data.get("residue") or residue,
            "chain": data.get("chain") or chain or "all",
            "residues": data.get("residues") or [],
            "selection": data.get("selection") or "",
            "viewer_selection": data.get("viewer_selection"),
            "allowed_next_tools": data.get("allowed_next_tools") or ["show_in_viewer", "set_gridbox", "select_workspace"],
        }
    )
    return result


def inspect_assets() -> dict[str, Any]:
    envelope = actions.inspect_assets()
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    result = _base_result(envelope)
    result.update(
        {
            "inventory": data.get("inventory") or {},
            "allowed_next_tools": data.get("allowed_next_tools") or ["select_workspace", "fetch_assets", "read_tool_details"],
        }
    )
    return result


def select_workspace(receptor: str = "all", chain: str = "auto", native_ligand: str = "auto", dock_ligands: str = "all") -> dict[str, Any]:
    envelope = actions.select_workspace(receptor, chain=chain, native_ligand=native_ligand, dock_ligands=dock_ligands)
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    result = _base_result(envelope)
    result.update(
        {
            "selected": data.get("selected") or [],
            "allowed_next_tools": data.get("allowed_next_tools") or ["set_gridbox", "select_workspace", "read_tool_details"],
        }
    )
    return result


def set_gridbox(
    method: str = "native_ligand",
    size: float = 20.0,
    padding: float = 0.0,
    center: str = "",
    pocket_rank: int = 1,
    p2rank_mode: str = "fit",
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    envelope = actions.set_gridbox(
        method=method,
        size=size,
        padding=padding,
        center=center,
        pocket_rank=pocket_rank,
        p2rank_mode=p2rank_mode,
    )
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    result = _base_result(envelope)
    result.update(
        {
            "grid_data": data.get("grid_data") or {},
            "gridboxes": data.get("gridboxes") or {},
            "gridbox_mode": data.get("gridbox_mode") or method,
            "resolved_gridbox_mode": data.get("resolved_gridbox_mode") or method,
            "warnings": data.get("warnings") or [],
            "allowed_next_tools": data.get("allowed_next_tools") or ["set_docking_config", "set_gridbox", "read_tool_details"],
        }
    )
    if progress_callback is not None and str(method or "").lower() in {"p2rank", "gridfinder", "auto"}:
        progress_callback({"type": "status", "stage": "set_gridbox", "delta": result["summary"], "result": result})
    return result


def set_docking_config(
    engine: str = "vina_gpu_21",
    mode: str = "standard",
    run_count: int = 1,
    padding: float = 0.0,
    out_root_name: str = "",
    exhaustiveness: int | None = None,
    num_modes: int | None = None,
    energy_range: float | None = None,
    cpu: int | None = None,
    seed: int | None = None,
    ph: float | None = None,
    advanced: str = "",
) -> dict[str, Any]:
    envelope = actions.set_config(
        engine=engine,
        mode=mode,
        run_count=run_count,
        padding=padding,
        out_root_name=out_root_name,
        exhaustiveness=exhaustiveness,
        num_modes=num_modes,
        energy_range=energy_range,
        cpu=cpu,
        seed=seed,
        ph=ph,
        advanced=advanced,
    )
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    result = _base_result(envelope)
    result.update(
        {
            "config": data.get("config") or {},
            "validation": data.get("validation") or {},
            "allowed_next_tools": data.get("allowed_next_tools") or ["build_or_run_queue", "set_docking_config", "read_tool_details"],
        }
    )
    return result


def _queue_data(envelope: dict[str, Any]) -> dict[str, Any]:
    return envelope.get("data") if isinstance(envelope.get("data"), dict) else {}


def _queue_stats(data: dict[str, Any], replace_queue: bool) -> dict[str, Any]:
    rows = data.get("queue") if isinstance(data.get("queue"), list) else []
    new_jobs = data.get("new_jobs_added", data.get("new_jobs"))
    try:
        new_jobs_int = int(new_jobs)
    except (TypeError, ValueError):
        new_jobs_int = len(rows)
    batch_ids = [str(row.get("batch_id") or "").strip() for row in rows if isinstance(row, dict) and row.get("batch_id")]
    batch_id = batch_ids[-1] if batch_ids else data.get("batch_id")
    total_runs = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            total_runs += max(1, int(row.get("run_count") or 1))
        except (TypeError, ValueError):
            total_runs += 1
    if not total_runs and data.get("total_runs") is not None:
        try:
            total_runs = int(data.get("total_runs") or 0)
        except (TypeError, ValueError):
            total_runs = 0
    return {
        "batch_id": batch_id,
        "new_jobs": new_jobs_int,
        "queue_count": data.get("queue_count", len(rows)),
        "job_count": new_jobs_int if replace_queue else len(rows),
        "total_runs": total_runs,
        "replace_queue": replace_queue,
    }


def build_or_run_queue(
    action: str = "build_test",
    replace_queue: bool = True,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    action_norm = str(action or "build_test").strip().lower()
    replace_existing = bool(replace_queue)
    if progress_callback is not None:
        progress_callback(
            {
                "type": "status",
                "stage": "build_or_run_queue",
                "delta": f"Building queue ({'replace' if replace_existing else 'append'} mode)...",
            }
        )
    queue_envelope = actions.build_queue(replace_queue=replace_existing)
    queue_data = _queue_data(queue_envelope)
    queue_stats = _queue_stats(queue_data, replace_existing)
    run_data: dict[str, Any] = {}
    run_envelope: dict[str, Any] = {}
    run_aliases = {"build_test", "test", "run_test", "test_run", "dry_run", "log", "plan"}
    full_aliases = {"run_full", "full", "run", "start", "start_run", "real", "real_run", "start_full", "full_run", "production"}
    if not queue_envelope.get("ok"):
        error = _error_from_envelope(queue_envelope)
        return {
            "ok": False,
            "summary": str(queue_envelope.get("message") or error or "Queue build failed."),
            "error": error or "Queue build failed.",
            "queue": queue_stats,
            "replace_queue": replace_existing,
            "run": {"started": False, "test_mode": None, "ok": None, "error": "", "planned_total_runs": 0, "out_root": ""},
            "allowed_next_tools": ["select_workspace", "set_gridbox", "set_docking_config", "read_tool_details"],
        }
    if action_norm in run_aliases | full_aliases:
        test_mode = action_norm in run_aliases
        batch_id = queue_stats.get("batch_id")
        if progress_callback is not None:
            progress_callback(
                {
                    "type": "status",
                    "stage": "build_or_run_queue",
                    "delta": f"Queue built; starting {'test' if test_mode else 'real'} run for batch {batch_id or '-'}...",
                }
            )
        try:
            batch_int = int(batch_id) if batch_id not in {None, ""} else None
        except (TypeError, ValueError):
            batch_int = None
        run_envelope = actions.run_start(test_mode=test_mode, batch_id=batch_int)
        run_data = _queue_data(run_envelope)
        if progress_callback is not None:
            progress_callback(
                {
                    "type": "status",
                    "stage": "run_start",
                    "delta": str(run_envelope.get("message") or f"run status: {run_data.get('status') or '-'}"),
                    "result": run_data,
                }
            )
    elif action_norm not in {"build_only", "build"}:
        return {
            "ok": False,
            "summary": f"Unknown queue action: {action_norm}",
            "error": f"Unknown queue action: {action_norm}",
            "allowed_next_tools": ["build_or_run_queue", "read_tool_details"],
        }
    ok = bool(queue_envelope.get("ok")) and (not run_envelope or bool(run_envelope.get("ok")))
    error = _error_from_envelope(queue_envelope) or _error_from_envelope(run_envelope)
    result = {
        "ok": ok,
        "summary": (
            f"Queue action {action_norm}: {queue_stats.get('new_jobs', 0)} job(s), "
            f"batch {queue_stats.get('batch_id') or '-'}, mode={'append' if not replace_existing else 'replace'}"
            + (f"; run error: {error}" if error else "")
        ),
        "queue": queue_stats,
        "replace_queue": replace_existing,
        "run": {
            "started": bool(run_data and run_envelope.get("ok")),
            "test_mode": (action_norm in run_aliases) if run_envelope else None,
            "ok": run_envelope.get("ok") if run_envelope else None,
            "error": _error_from_envelope(run_envelope),
            "planned_total_runs": run_data.get("total_runs") or run_data.get("planned_total_runs") or queue_stats.get("total_runs"),
            "out_root": run_data.get("out_root") or "",
            "status": run_data.get("status") or "",
        },
        "control_action": "queue.build+run.start" if run_envelope else "queue.build",
        "control_trace_id": queue_envelope.get("trace_id"),
        "allowed_next_tools": ["get_dockup_state", "read_tool_details"],
    }
    if error:
        result["error"] = error
    return result


def delete_ligands(target: str = "all") -> dict[str, Any]:
    raw = str(target or "all").strip()
    names = ["all"] if raw.lower() == "all" else [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]
    deleted: list[str] = []
    missing: list[str] = []
    last_envelope: dict[str, Any] = {}
    for name in names:
        last_envelope = actions.clear_ligands() if name.lower() == "all" else actions.delete_ligand(name)
        data = last_envelope.get("data") if isinstance(last_envelope.get("data"), dict) else {}
        deleted.extend(data.get("deleted") or data.get("removed") or ([name] if last_envelope.get("ok") else []))
        if not last_envelope.get("ok"):
            missing.append(name)
    result = _base_result(last_envelope, summary=f"Deleted {len(deleted)} ligand file(s).")
    result.update({"deleted": deleted, "missing": missing, "active_ligands": [], "allowed_next_tools": ["get_dockup_state", "fetch_assets"]})
    return result


def delete_receptors(target: str = "all") -> dict[str, Any]:
    raw = str(target or "all").strip()
    names = ["all"] if raw.lower() == "all" else [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]
    deleted: list[str] = []
    missing: list[str] = []
    last_envelope: dict[str, Any] = {}
    for name in names:
        last_envelope = actions.clear_receptors() if name.lower() == "all" else actions.delete_receptor(name)
        data = last_envelope.get("data") if isinstance(last_envelope.get("data"), dict) else {}
        deleted.extend(data.get("deleted") or data.get("removed") or ([name] if last_envelope.get("ok") else []))
        if not last_envelope.get("ok"):
            missing.append(name)
    state = actions.get_state()
    state_data = state.get("data") if isinstance(state.get("data"), dict) else {}
    receptors = state_data.get("loaded_receptors") if isinstance(state_data.get("loaded_receptors"), list) else []
    result = _base_result(last_envelope, summary=f"Deleted {len(set(deleted))} receptor(s).")
    result.update({"deleted": sorted(set(deleted)), "missing": missing, "remaining_receptors": receptors, "allowed_next_tools": ["get_dockup_state", "fetch_assets"]})
    return result


def delete_queue_batches(batch_id: str = "all") -> dict[str, Any]:
    raw = str(batch_id or "all").strip()
    ids = ["all"] if raw.lower() == "all" else [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]
    deleted: list[str] = []
    last_envelope: dict[str, Any] = {}
    for item in ids:
        last_envelope = actions.remove_queue_batch(item)
        data = last_envelope.get("data") if isinstance(last_envelope.get("data"), dict) else {}
        deleted.extend([str(row) for row in data.get("deleted_batch_ids") or data.get("removed_batch_ids") or ([item] if last_envelope.get("ok") else [])])
    queue = actions.get_queue()
    queue_data = queue.get("data") if isinstance(queue.get("data"), dict) else {}
    result = _base_result(last_envelope, summary=f"Deleted {len(deleted)} queue batch(es).")
    result.update({"deleted_batch_ids": deleted, "queue_count": queue_data.get("queue_count", 0), "allowed_next_tools": ["get_dockup_state", "build_or_run_queue"]})
    return result


CONTROL_TOOL_FUNCTIONS = {
    "inspect_assets": inspect_assets,
    "show_in_viewer": show_in_viewer,
    "show_residues": show_residues,
    "select_workspace": select_workspace,
    "set_gridbox": set_gridbox,
    "set_docking_config": set_docking_config,
    "build_or_run_queue": build_or_run_queue,
    "delete_ligands": delete_ligands,
    "delete_receptors": delete_receptors,
    "delete_queue_batches": delete_queue_batches,
}
