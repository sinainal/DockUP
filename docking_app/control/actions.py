from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from ..config import LIGAND_DIR, RECEPTOR_DIR
from ..helpers import normalize_docking_config
from ..models import FetchLigandsPayload, LoadReceptorsPayload, ModePayload, RunStartPayload, SelectLigandPayload, SelectReceptorPayload
from ..agent import autonomous_docking
from ..routes import core
from ..routes import results as result_routes
from ..state import RUN_STATE, STATE, save_state_cache
from .events import publish_control_event
from .models import ControlEnvelope, ControlError


def _safe_ligand_filename(value: str) -> str:
    name = Path(str(value or "").strip()).name
    if not name:
        raise ValueError("Ligand filename is empty.")
    if not name.lower().endswith(".sdf"):
        name = f"{name}.sdf"
    if name in {".sdf", "..sdf"} or "/" in name or "\\" in name:
        raise ValueError("Invalid ligand filename.")
    return name


def _normalize_receptor_id(value: str) -> str:
    return str(value or "").strip().upper()


def _normalize_grid_row(row: dict[str, Any]) -> dict[str, float]:
    aliases = {
        "center_x": "cx",
        "center_y": "cy",
        "center_z": "cz",
        "size_x": "sx",
        "size_y": "sy",
        "size_z": "sz",
    }
    clean: dict[str, float] = {}
    for key, value in row.items():
        normalized_key = aliases.get(str(key), str(key))
        if normalized_key in {"cx", "cy", "cz", "sx", "sy", "sz"}:
            clean[normalized_key] = float(value)
    missing = [key for key in ("cx", "cy", "cz", "sx", "sy", "sz") if key not in clean]
    if missing:
        raise ValueError(f"Grid row missing fields: {', '.join(missing)}")
    return clean


def _queue_summary(queue: list[dict[str, Any]]) -> dict[str, Any]:
    total_runs = 0
    job_types: set[str] = set()
    batches: set[str] = set()
    for job in queue:
        if not isinstance(job, dict):
            continue
        try:
            total_runs += int(job.get("run_count") or job.get("runs") or 1)
        except (TypeError, ValueError):
            total_runs += 1
        if job.get("job_type"):
            job_types.add(str(job.get("job_type")))
        if job.get("batch_id") is not None:
            batches.add(str(job.get("batch_id")))
    return {
        "queue_count": len(queue),
        "total_runs": total_runs,
        "job_types": sorted(job_types),
        "batch_ids": sorted(batches),
    }


def _trace_id(action: str) -> str:
    safe_action = "".join(ch if ch.isalnum() else "-" for ch in action).strip("-")
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000000:06d}-{safe_action}"


def _response_payload(response: Any) -> tuple[dict[str, Any], int]:
    if isinstance(response, JSONResponse):
        raw = response.body.decode("utf-8") if isinstance(response.body, bytes) else str(response.body or "")
        try:
            payload = json.loads(raw or "{}")
        except ValueError:
            payload = {"error": raw or "Invalid JSON response."}
        return payload if isinstance(payload, dict) else {"data": payload}, response.status_code
    if isinstance(response, dict):
        return response, 200
    return {"data": response}, 200


def _call_route(func: Any, *args: Any, **kwargs: Any) -> tuple[dict[str, Any], int]:
    try:
        return _response_payload(func(*args, **kwargs))
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict):
            data = dict(detail)
            data.setdefault("error", data.get("detail") or "Request failed.")
        else:
            data = {"error": str(detail or "Request failed.")}
        return data, int(exc.status_code or 500)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}, 500


def _state_snapshot() -> dict[str, Any]:
    receptors = STATE.get("receptor_meta") if isinstance(STATE.get("receptor_meta"), list) else []
    active_ligands = STATE.get("active_ligands") if isinstance(STATE.get("active_ligands"), list) else []
    grid_data = STATE.get("agent_grid_data") if isinstance(STATE.get("agent_grid_data"), dict) else {}
    queue = STATE.get("queue") if isinstance(STATE.get("queue"), list) else []
    return {
        "selected_receptor": str(STATE.get("selected_receptor") or ""),
        "selected_chain": str(STATE.get("selected_chain") or "all"),
        "selected_ligand": str(STATE.get("selected_ligand") or ""),
        "receptor_count": len(receptors),
        "ligand_count": len(list(LIGAND_DIR.glob("*.sdf"))),
        "active_ligand_count": len(active_ligands),
        "queue_count": len(queue),
        "gridbox_count": len(grid_data),
        "gridbox_ready": bool(grid_data or STATE.get("grid_file_path")),
        "run_status": str(RUN_STATE.get("status") or "idle"),
        "docking_config": normalize_docking_config(STATE.get("docking_config") or {}),
    }


def _changed(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        key: {"before": before.get(key), "after": after.get(key)}
        for key in sorted(set(before) | set(after))
        if before.get(key) != after.get(key)
    }


def _envelope(
    action: str,
    data: dict[str, Any],
    *,
    before: dict[str, Any],
    after: dict[str, Any] | None = None,
    message: str = "",
    ui_hints: dict[str, Any] | None = None,
    status_code: int = 200,
    error_code: str = "control_error",
    next_actions: list[str] | None = None,
) -> dict[str, Any]:
    after_payload = after if after is not None else _state_snapshot()
    error_text = str(data.get("error") or data.get("detail") or "").strip()
    ok = status_code < 400 and not error_text
    error = None
    if not ok:
        error = ControlError(
            code=error_code,
            message=error_text or f"{action} failed.",
            recoverable=True,
            next_actions=next_actions or [],
        )
    payload = ControlEnvelope(
        ok=ok,
        action=action,
        trace_id=_trace_id(action),
        message=message or (error.message if error else f"{action} completed."),
        data=data,
        before=before,
        after=after_payload,
        changed=_changed(before, after_payload),
        ui_hints=ui_hints or {},
        error=error,
    )
    envelope = payload.model_dump()
    if _should_publish_event(action, envelope):
        publish_control_event(envelope)
    return envelope


def _should_publish_event(action: str, envelope: dict[str, Any]) -> bool:
    if not envelope.get("ok"):
        return False
    if action in {"state.get", "receptor.list", "ligand.list", "queue.list", "run.status", "results.folders"}:
        return False
    if action.startswith("report.") and action in {"report.status", "report.list", "report.images", "report.preview"}:
        return False
    hints = envelope.get("ui_hints") if isinstance(envelope.get("ui_hints"), dict) else {}
    refresh = hints.get("refresh") if isinstance(hints.get("refresh"), list) else []
    return bool(refresh or hints.get("viewer_selection"))


def get_state() -> dict[str, Any]:
    before = _state_snapshot()
    data, status = _response_payload(core.api_state())
    return _envelope(
        "state.get",
        data,
        before=before,
        after=_state_snapshot(),
        message=f"state: receptor={data.get('selected_receptor') or '-'} queue={data.get('queue_count', 0)} run={data.get('run_status') or '-'}",
        ui_hints={"refresh": ["state"]},
        status_code=status,
    )


def list_receptors() -> dict[str, Any]:
    before = _state_snapshot()
    data, status = _response_payload(core.list_receptors())
    receptors = data.get("receptors") if isinstance(data.get("receptors"), list) else []
    return _envelope(
        "receptor.list",
        data,
        before=before,
        after=_state_snapshot(),
        message=f"receptors: {len(receptors)}",
        ui_hints={"refresh": ["receptors"]},
        status_code=status,
    )


def load_receptors(pdb_ids: str) -> dict[str, Any]:
    before = _state_snapshot()
    data, status = _response_payload(core.load_receptors(LoadReceptorsPayload(pdb_ids=pdb_ids)))
    summary = data.get("summary") if isinstance(data.get("summary"), list) else []
    ignored = data.get("ignored_ids") if isinstance(data.get("ignored_ids"), list) else []
    return _envelope(
        "receptor.load",
        data,
        before=before,
        message=f"loaded receptors: {len(summary)}" + (f"; ignored={','.join(str(item) for item in ignored)}" if ignored else ""),
        ui_hints={"refresh": ["state", "receptors"]},
        status_code=status,
        next_actions=["receptor.select", "viewer.show"],
    )


def select_receptor(pdb_id: str) -> dict[str, Any]:
    before = _state_snapshot()
    selected = core._normalize_receptor_id(pdb_id)
    detail, detail_status = _response_payload(core.receptor_detail(selected)) if selected else ({"error": "Missing receptor id."}, 400)
    failed = False
    if detail_status >= 400 or detail.get("error"):
        data = {"error": detail.get("error") or "Receptor detail is not available.", "detail": detail}
        status = detail_status
        failed = True
    else:
        data, status = _response_payload(core.receptor_select(SelectReceptorPayload(pdb_id=selected)))
        selected = str(data.get("selected_receptor") or selected or "").upper()
        failed = status >= 400 or bool(data.get("error"))
    return _envelope(
        "receptor.select",
        data,
        before=before,
        message=f"receptor not available: {selected or '-'}" if failed else f"selected receptor: {selected or '-'}",
        ui_hints={"refresh": ["state", "viewer"]} if failed else {"refresh": ["state", "viewer"], "selected_receptor": selected},
        status_code=status,
        next_actions=["receptor.load", "receptor.list"],
    )


def _receptor_filename_for_target(target: str) -> str:
    raw = str(target or "").strip()
    if not raw:
        return ""
    if raw.lower().endswith(".pdb"):
        return Path(raw).name
    receptor_id = core._normalize_receptor_id(raw)
    candidate = RECEPTOR_DIR / f"{receptor_id}.pdb"
    if candidate.exists():
        return candidate.name
    return Path(raw).name


def delete_receptor(target: str) -> dict[str, Any]:
    before = _state_snapshot()
    filename = _receptor_filename_for_target(target)
    data, status = _response_payload(core.delete_receptor_file({"name": filename}))
    return _envelope(
        "receptor.delete",
        data,
        before=before,
        message=f"deleted receptor: {filename or '-'}",
        ui_hints={"refresh": ["state", "receptors", "viewer"]},
        status_code=status,
        next_actions=["receptor.list"],
    )


def clear_receptors() -> dict[str, Any]:
    before = _state_snapshot()
    data, status = _response_payload(core.clear_all_receptors())
    return _envelope(
        "receptor.clear",
        data,
        before=before,
        message="cleared receptors",
        ui_hints={"refresh": ["state", "receptors", "viewer"]},
        status_code=status,
    )


def list_ligands() -> dict[str, Any]:
    before = _state_snapshot()
    data, status = _response_payload(core.list_ligands())
    ligands = data.get("ligands") if isinstance(data.get("ligands"), list) else []
    return _envelope(
        "ligand.list",
        data,
        before=before,
        message=f"ligands: {len(ligands)}",
        ui_hints={"refresh": ["ligands"]},
        status_code=status,
    )


def inspect_assets() -> dict[str, Any]:
    before = _state_snapshot()
    result = autonomous_docking.inspect_assets()
    status = 200 if result.get("ok", True) else 400
    inventory = result.get("inventory") if isinstance(result.get("inventory"), dict) else {}
    receptors = inventory.get("receptors") if isinstance(inventory.get("receptors"), dict) else {}
    ligands = inventory.get("ligands") if isinstance(inventory.get("ligands"), list) else []
    return _envelope(
        "assets.inspect",
        result,
        before=before,
        message=str(result.get("summary") or f"assets: {len(receptors)} receptor(s), {len(ligands)} ligand(s)"),
        ui_hints={"refresh": ["state", "receptors", "ligands", "workspace"]},
        status_code=status,
        next_actions=["receptor.load", "ligand.fetch", "workspace.select"],
    )


def fetch_ligands(ligand_ids: str) -> dict[str, Any]:
    before = _state_snapshot()
    data, status = _response_payload(core.fetch_ligands(FetchLigandsPayload(ligand_ids=ligand_ids)))
    saved = data.get("saved") if isinstance(data.get("saved"), list) else []
    failed = data.get("failed") if isinstance(data.get("failed"), list) else []
    status_code = 400 if failed and not saved else status
    return _envelope(
        "ligand.fetch",
        data,
        before=before,
        message=f"fetched ligands: {len(saved)}" + (f"; failed={','.join(str(item) for item in failed)}" if failed else ""),
        ui_hints={"refresh": ["state", "ligands"]},
        status_code=status_code,
        error_code="ligand_fetch_failed",
        next_actions=["ligand.fetch", "ligand.list"],
    )


def delete_ligand(name: str) -> dict[str, Any]:
    before = _state_snapshot()
    data, status = _response_payload(core.delete_ligand({"name": str(name or "").strip()}))
    return _envelope(
        "ligand.delete",
        data,
        before=before,
        message=f"deleted ligand: {name or '-'}",
        ui_hints={"refresh": ["state", "ligands"]},
        status_code=status,
        next_actions=["ligand.list"],
    )


def clear_ligands() -> dict[str, Any]:
    before = _state_snapshot()
    data, status = _response_payload(core.clear_all_ligands())
    return _envelope(
        "ligand.clear",
        data,
        before=before,
        message="cleared ligands",
        ui_hints={"refresh": ["state", "ligands"]},
        status_code=status,
    )


def set_active_ligands(names: list[str], *, replace: bool = True) -> dict[str, Any]:
    before = _state_snapshot()
    clean_names = [_safe_ligand_filename(name) for name in names if str(name or "").strip()]
    if replace:
        _response_payload(core.clear_active_ligands())
    data, status = _response_payload(core.add_active_ligands({"names": clean_names}))
    return _envelope(
        "ligand.active.set",
        data,
        before=before,
        message=f"active ligands: {len(data.get('active_ligands') or [])}",
        ui_hints={"refresh": ["state", "ligands", "queue"]},
        status_code=status,
        error_code="ligand_active_set_failed",
        next_actions=["ligand.list", "ligand.active.set"],
    )


def generate_ligands(specs: list[dict[str, Any]], *, reset: bool = False, activate: bool = True) -> dict[str, Any]:
    before = _state_snapshot()
    saved: list[str] = []
    failed: list[dict[str, str]] = []
    try:
        from convert_3D import build_oligomer_smiles, smiles_to_3d_sdf
    except Exception as exc:
        return _envelope(
            "ligand.generate",
            {"error": f"Cannot import ligand generator: {exc}", "saved": [], "failed": []},
            before=before,
            status_code=500,
            error_code="ligand_generate_unavailable",
            next_actions=["ligand.fetch"],
        )
    for spec in specs:
        raw_spec = spec if isinstance(spec, dict) else {}
        try:
            filename = _safe_ligand_filename(str(raw_spec.get("filename") or raw_spec.get("name") or ""))
            monomer_smiles = str(raw_spec.get("smiles") or raw_spec.get("monomer_smiles") or "").strip()
            count = int(raw_spec.get("count") or raw_spec.get("n") or 1)
            if not monomer_smiles:
                raise ValueError("SMILES is required for deterministic ligand generation.")
            target = LIGAND_DIR / filename
            if reset and target.exists():
                _response_payload(core.delete_ligand({"name": filename}))
            oligomer_smiles = build_oligomer_smiles(monomer_smiles, count)
            smiles_to_3d_sdf(oligomer_smiles, target)
            saved.append(filename)
        except Exception as exc:
            failed.append({"spec": str(raw_spec.get("filename") or raw_spec.get("name") or raw_spec), "error": str(exc)})
    if activate and saved:
        _response_payload(core.add_active_ligands({"names": saved}))
    status = 400 if failed and not saved else 200
    return _envelope(
        "ligand.generate",
        {"saved": saved, "failed": failed, "active_ligands": STATE.get("active_ligands", [])},
        before=before,
        message=f"generated ligands: {len(saved)}" + (f"; failed={len(failed)}" if failed else ""),
        ui_hints={"refresh": ["state", "ligands"]},
        status_code=status,
        error_code="ligand_generate_failed",
        next_actions=["ligand.generate", "ligand.list"],
    )


def show_viewer(pdb_id: str, *, chain: str = "") -> dict[str, Any]:
    before = _state_snapshot()
    selected = select_receptor(pdb_id)
    if not selected.get("ok"):
        data = {"error": (selected.get("error") or {}).get("message") or "Could not select receptor.", "selection": selected}
        return _envelope(
            "viewer.show",
            data,
            before=before,
            message=str(data["error"]),
            ui_hints={"refresh": ["state", "viewer"]},
            status_code=400,
            next_actions=["receptor.load", "receptor.list"],
        )
    receptor_id = str((selected.get("data") or {}).get("selected_receptor") or pdb_id or "").upper()
    detail, status = _response_payload(core.receptor_detail(receptor_id, chain=chain))
    pdb_text = str(detail.get("pdb_text") or "")
    compact = {
        "pdb_id": detail.get("pdb_id") or receptor_id,
        "pdb_text_length": len(pdb_text),
        "chains": detail.get("chains") or [],
        "ligands_by_chain": detail.get("ligands_by_chain") or {},
        "pdb_file": detail.get("pdb_file") or "",
        "grid_data": detail.get("grid_data") or {},
        "selected_chain": detail.get("selected_chain") or chain or "all",
        "selected_ligand": detail.get("selected_ligand") or "",
    }
    if detail.get("error"):
        compact["error"] = detail.get("error")
    if not compact["pdb_text_length"] and status < 400:
        status = 404
        compact["error"] = "Viewer receptor payload has no PDB text."
    return _envelope(
        "viewer.show",
        compact,
        before=before,
        message=(
            f"viewer ready: {compact['pdb_id']} ({compact['pdb_text_length']} pdb chars)"
            if compact.get("pdb_text_length")
            else f"viewer data missing: {receptor_id}"
        ),
        ui_hints={"refresh": ["state", "viewer"], "selected_receptor": compact.get("pdb_id")},
        status_code=status,
        next_actions=["receptor.load", "receptor.select"],
    )


def show_residues(pdb_id: str = "", *, residue: str = "TRP", chain: str = "all") -> dict[str, Any]:
    before = _state_snapshot()
    result = autonomous_docking.show_residues(receptor=pdb_id, residue=residue, chain=chain)
    status = 200 if result.get("ok", True) else 400
    viewer_selection = result.get("viewer_selection") if isinstance(result.get("viewer_selection"), dict) else {}
    return _envelope(
        "viewer.residues",
        result,
        before=before,
        message=str(result.get("summary") or f"residues: {len(result.get('residues') or [])}"),
        ui_hints={
            "refresh": ["state", "viewer", "grid-selection"],
            "viewer_selection": viewer_selection,
            "selected_receptor": result.get("receptor") or pdb_id,
        },
        status_code=status,
        error_code="viewer_residues_failed",
        next_actions=["receptor.load", "viewer.show"],
    )


def select_workspace(
    receptor: str = "all",
    *,
    chain: str = "auto",
    native_ligand: str = "auto",
    dock_ligands: str = "all",
) -> dict[str, Any]:
    before = _state_snapshot()
    _prepare_active_dock_ligands(dock_ligands)
    result = autonomous_docking.select_workspace(
        receptor=receptor,
        chain=chain,
        native_ligand=native_ligand,
        dock_ligands=dock_ligands,
    )
    status = 200 if result.get("ok") else 400
    return _envelope(
        "workspace.select",
        result,
        before=before,
        message=str(result.get("summary") or "workspace selected"),
        ui_hints={"refresh": ["state", "workspace", "viewer"]},
        status_code=status,
        next_actions=["receptor.load", "ligand.fetch", "workspace.select"],
    )


def _prepare_active_dock_ligands(dock_ligands: str) -> None:
    raw = str(dock_ligands or "").strip()
    available = {path.name for path in LIGAND_DIR.glob("*.sdf") if path.is_file()}
    if not available:
        return
    current = [str(name or "").strip() for name in STATE.get("active_ligands", []) if str(name or "").strip() in available]
    seen = set(current)
    if not raw or raw.lower() == "all":
        if not current:
            current = sorted(available)
    else:
        for item in [part.strip() for part in raw.split(",") if part.strip()]:
            if item in available and item not in seen:
                current.append(item)
                seen.add(item)
    STATE["active_ligands"] = current
    autonomous_docking.AGENT_STATE["inventory"] = autonomous_docking._inventory_for(
        autonomous_docking._state_receptor_ids(),
        current,
    )
    save_state_cache()


def set_gridbox(
    method: str = "native_ligand",
    *,
    size: float = 20.0,
    padding: float = 0.0,
    center: str = "",
    pocket_rank: int = 1,
    p2rank_mode: str = "fit",
) -> dict[str, Any]:
    before = _state_snapshot()
    result = autonomous_docking.set_gridbox(
        method=method,
        size=size,
        padding=padding,
        center=center,
        pocket_rank=pocket_rank,
        p2rank_mode=p2rank_mode,
    )
    status = 200 if result.get("ok") else 400
    return _envelope(
        "gridbox.set",
        result,
        before=before,
        message=str(result.get("summary") or "gridbox set"),
        ui_hints={"refresh": ["state", "viewer", "gridbox"]},
        status_code=status,
        error_code="gridbox_set_failed",
        next_actions=["workspace.select", "gridbox.set"],
    )


def set_gridboxes(grid_data: dict[str, dict[str, Any]]) -> dict[str, Any]:
    before = _state_snapshot()
    normalized: dict[str, dict[str, float]] = {}
    errors: list[dict[str, str]] = []
    for raw_id, raw_row in (grid_data or {}).items():
        pdb_id = _normalize_receptor_id(str(raw_id))
        if not pdb_id:
            continue
        try:
            normalized[pdb_id] = _normalize_grid_row(raw_row if isinstance(raw_row, dict) else {})
        except Exception as exc:
            errors.append({"pdb_id": pdb_id, "error": str(exc)})
    if errors and not normalized:
        return _envelope(
            "gridbox.set_many",
            {"error": "No valid gridbox rows.", "errors": errors},
            before=before,
            status_code=400,
            error_code="gridbox_set_many_failed",
            next_actions=["gridbox.set_many"],
        )
    current = STATE.get("agent_grid_data") if isinstance(STATE.get("agent_grid_data"), dict) else {}
    STATE["agent_grid_data"] = {**current, **normalized}
    save_state_cache()
    return _envelope(
        "gridbox.set_many",
        {"grid_data": normalized, "errors": errors, "gridbox_count": len(STATE["agent_grid_data"])},
        before=before,
        message=f"gridboxes set: {len(normalized)}",
        ui_hints={"refresh": ["state", "gridbox", "queue"]},
        status_code=400 if errors and not normalized else 200,
        error_code="gridbox_set_many_failed",
        next_actions=["queue.prepare", "queue.build"],
    )


def set_config(
    *,
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
    before = _state_snapshot()
    result = autonomous_docking.set_docking_config(
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
    status = 200 if result.get("ok", True) else 400
    return _envelope(
        "config.set",
        result,
        before=before,
        message=str(result.get("summary") or "config set"),
        ui_hints={"refresh": ["state", "config"]},
        status_code=status,
        next_actions=["gridbox.set", "queue.build"],
    )


def _queue_build_payload(replace_queue: bool) -> dict[str, Any]:
    docking_config = normalize_docking_config(STATE.get("docking_config") or {})
    requested_mode = str(STATE.get("mode") or "Docking")
    ligand_binding_mode = str(docking_config.get("ligand_binding_mode") or "single").strip().lower()
    if ligand_binding_mode == "multi_ligand":
        effective_mode = "Multi-Ligand"
    elif requested_mode == "Multi-Ligand":
        effective_mode = "Docking"
    else:
        effective_mode = requested_mode or "Docking"
    return {
        "mode": effective_mode,
        "run_count": int(STATE.get("runs") or 1),
        "padding": float(STATE.get("grid_pad") or 0.0),
        "out_root_path": str(STATE.get("out_root_path") or "data/dock"),
        "out_root_name": str(STATE.get("out_root_name") or ""),
        "docking_config": docking_config,
        "selection_map": STATE.get("selection_map") if isinstance(STATE.get("selection_map"), dict) else {},
        "grid_data": STATE.get("agent_grid_data") if isinstance(STATE.get("agent_grid_data"), dict) else {},
        "replace_queue": bool(replace_queue),
    }


def get_queue() -> dict[str, Any]:
    before = _state_snapshot()
    queue = STATE.get("queue") if isinstance(STATE.get("queue"), list) else []
    batches = sorted({str(row.get("batch_id") or "") for row in queue if isinstance(row, dict) and row.get("batch_id")})
    data = {"queue_count": len(queue), "queue": queue, "batch_ids": batches}
    return _envelope(
        "queue.list",
        data,
        before=before,
        after=_state_snapshot(),
        message=f"queue: {len(queue)} job(s)",
        ui_hints={"refresh": ["state", "queue"]},
    )


def build_queue(*, replace_queue: bool = True) -> dict[str, Any]:
    before = _state_snapshot()
    data, status = _call_route(core.queue_build, _queue_build_payload(replace_queue))
    return _envelope(
        "queue.build",
        data,
        before=before,
        message=f"queue built: {data.get('queue_count', 0)} job(s)",
        ui_hints={"refresh": ["state", "queue"]},
        status_code=status,
        error_code="queue_build_failed",
        next_actions=["workspace.select", "gridbox.set", "config.set"],
    )


def prepare_queue(payload: dict[str, Any]) -> dict[str, Any]:
    before = _state_snapshot()
    trace: list[str] = []
    mode = str(payload.get("mode") or "Docking").strip()
    if mode not in {"Docking", "Multi-Ligand", "Redocking"}:
        mode = "Docking"

    if bool(payload.get("reset_queue", True)):
        STATE["queue"] = []
        trace.append("queue cleared")

    receptor_ids = [_normalize_receptor_id(item) for item in payload.get("receptors") or []]
    receptor_ids = [item for item in receptor_ids if item]
    if receptor_ids:
        loaded, load_status = _response_payload(core.add_receptors(LoadReceptorsPayload(pdb_ids=",".join(receptor_ids))))
        if load_status >= 400 or loaded.get("error"):
            return _envelope(
                "queue.prepare",
                loaded,
                before=before,
                message=str(loaded.get("error") or "Could not load receptors."),
                status_code=load_status,
                error_code="queue_prepare_receptors_failed",
                next_actions=["receptor.load"],
            )
        trace.append(f"receptors loaded: {len(receptor_ids)}")

    ligand_names = [_safe_ligand_filename(item) for item in payload.get("ligands") or [] if str(item or "").strip()]
    ligand_specs = [item for item in payload.get("ligand_specs") or [] if isinstance(item, dict)]
    generated_names: list[str] = []
    if bool(payload.get("reset_ligands", False)):
        for name in ligand_names:
            if (LIGAND_DIR / name).exists():
                _response_payload(core.delete_ligand({"name": name}))
        for spec in ligand_specs:
            raw_name = str(spec.get("filename") or spec.get("name") or "").strip()
            if raw_name:
                safe_name = _safe_ligand_filename(raw_name)
                if (LIGAND_DIR / safe_name).exists():
                    _response_payload(core.delete_ligand({"name": safe_name}))
        trace.append("requested ligand files deleted before fetch/generate")

    if ligand_specs:
        generated = generate_ligands(ligand_specs, reset=False, activate=False)
        generated_data = generated.get("data") if isinstance(generated.get("data"), dict) else {}
        generated_names = [str(item) for item in generated_data.get("saved") or []]
        failures = generated_data.get("failed") if isinstance(generated_data.get("failed"), list) else []
        if failures and not generated_names:
            return _envelope(
                "queue.prepare",
                {"error": "Ligand generation failed.", "failed": failures},
                before=before,
                status_code=400,
                error_code="queue_prepare_ligands_failed",
                next_actions=["ligand.generate"],
            )
        trace.append(f"ligands generated: {len(generated_names)}")

    all_ligands = [*ligand_names, *[name for name in generated_names if name not in ligand_names]]
    if payload.get("activate_ligands", True) and all_ligands:
        active = set_active_ligands(all_ligands, replace=True)
        active_data = active.get("data") if isinstance(active.get("data"), dict) else {}
        ignored = active_data.get("ignored") if isinstance(active_data.get("ignored"), list) else []
        if ignored:
            return _envelope(
                "queue.prepare",
                {"error": "Some requested active ligands are missing.", "ignored": ignored, "active_ligands": active_data.get("active_ligands", [])},
                before=before,
                status_code=400,
                error_code="queue_prepare_ligands_missing",
                next_actions=["ligand.list", "ligand.generate", "ligand.fetch"],
            )
        trace.append(f"active ligands set: {len(all_ligands)}")

    grid_payload = payload.get("grid_data") if isinstance(payload.get("grid_data"), dict) else {}
    if grid_payload:
        grid_result = set_gridboxes(grid_payload)
        if not grid_result.get("ok"):
            return _envelope(
                "queue.prepare",
                grid_result.get("data") if isinstance(grid_result.get("data"), dict) else {"error": "Invalid gridboxes."},
                before=before,
                status_code=400,
                error_code="queue_prepare_gridbox_failed",
                next_actions=["gridbox.set_many"],
            )
        trace.append(f"gridboxes set: {len(grid_payload)}")

    _response_payload(core.api_mode(ModePayload(mode=mode)))
    chains = payload.get("chains") if isinstance(payload.get("chains"), dict) else {}
    incoming_selection = payload.get("selection_map") if isinstance(payload.get("selection_map"), dict) else {}
    selection_map: dict[str, dict[str, Any]] = {}
    target_receptors = receptor_ids or [_normalize_receptor_id(item.get("pdb_id")) for item in STATE.get("receptor_meta", []) if isinstance(item, dict)]
    for pdb_id in target_receptors:
        incoming = incoming_selection.get(pdb_id) if isinstance(incoming_selection.get(pdb_id), dict) else {}
        chain = str(incoming.get("chain") or chains.get(pdb_id) or "all")
        ligand_label = str(incoming.get("ligand_resname") or incoming.get("ligand") or "").strip()
        ligand_members = incoming.get("ligand_resnames") if isinstance(incoming.get("ligand_resnames"), list) else []
        if mode == "Docking" and not ligand_label:
            ligand_label = "all_set"
            ligand_members = []
        selection_map[pdb_id] = {
            "chain": chain,
            "ligand_resname": ligand_label,
            "ligand_resnames": ligand_members,
            "flex_residues": incoming.get("flex_residues") or [],
        }
        _response_payload(core.ligand_select(SelectLigandPayload(pdb_id=pdb_id, chain=chain, ligand=ligand_label, ligands=ligand_members)))

    raw_docking_config = payload.get("docking_config")
    if isinstance(raw_docking_config, dict) and raw_docking_config:
        docking_config = normalize_docking_config(raw_docking_config)
    else:
        docking_config = normalize_docking_config(STATE.get("docking_config") or {})
    STATE["mode"] = mode
    STATE["runs"] = int(payload.get("run_count") or STATE.get("runs") or 1)
    STATE["grid_pad"] = float(payload.get("padding") if payload.get("padding") is not None else STATE.get("grid_pad") or 0.0)
    STATE["out_root_path"] = str(payload.get("out_root_path") or STATE.get("out_root_path") or "data/dock")
    STATE["out_root_name"] = str(payload.get("out_root_name") or STATE.get("out_root_name") or "")
    STATE["docking_config"] = docking_config
    save_state_cache()

    queue_payload = {
        "mode": mode,
        "run_count": STATE["runs"],
        "padding": STATE["grid_pad"],
        "out_root_path": STATE["out_root_path"],
        "out_root_name": STATE["out_root_name"],
        "docking_config": docking_config,
        "selection_map": selection_map or STATE.get("selection_map") or {},
        "grid_data": STATE.get("agent_grid_data") if isinstance(STATE.get("agent_grid_data"), dict) else {},
        "replace_queue": bool(payload.get("replace_queue", True)),
    }
    data, status = _call_route(core.queue_build, queue_payload)
    queue = data.get("queue") if isinstance(data.get("queue"), list) else STATE.get("queue", [])
    summary = _queue_summary(queue if isinstance(queue, list) else [])
    data = {
        **data,
        **summary,
        "mode": mode,
        "active_ligands": STATE.get("active_ligands", []),
        "selection_map": selection_map,
        "gridbox_count": len(STATE.get("agent_grid_data") if isinstance(STATE.get("agent_grid_data"), dict) else {}),
        "trace": trace,
    }
    return _envelope(
        "queue.prepare",
        data,
        before=before,
        message=f"prepared queue: {summary['queue_count']} job(s), {summary['total_runs']} run(s)",
        ui_hints={"refresh": ["state", "ligands", "gridbox", "queue"]},
        status_code=status,
        error_code="queue_prepare_failed",
        next_actions=["queue.list", "run.start"],
    )


def remove_queue_batch(batch_id: str) -> dict[str, Any]:
    before = _state_snapshot()
    data, status = _call_route(core.remove_batch, {"batch_id": str(batch_id or "").strip()})
    return _envelope(
        "queue.remove",
        data,
        before=before,
        message=f"queue batch removed: {batch_id or 'all'}",
        ui_hints={"refresh": ["state", "queue"]},
        status_code=status,
        next_actions=["queue.list"],
    )


def run_start(*, test_mode: bool = False, batch_id: int | None = None) -> dict[str, Any]:
    before = _state_snapshot()
    data, status = _call_route(core.run_start, RunStartPayload(is_test_mode=bool(test_mode), batch_id=batch_id))
    return _envelope(
        "run.start",
        data,
        before=before,
        message=f"run status: {data.get('status') or '-'}",
        ui_hints={"refresh": ["state", "run"]},
        status_code=status,
        error_code="run_start_failed",
        next_actions=["queue.build", "run.status"],
    )


def run_stop() -> dict[str, Any]:
    before = _state_snapshot()
    data, status = _call_route(core.run_stop)
    return _envelope(
        "run.stop",
        data,
        before=before,
        message=str(data.get("message") or f"run status: {data.get('status') or '-'}"),
        ui_hints={"refresh": ["state", "run"]},
        status_code=status,
        next_actions=["run.status"],
    )


def run_status() -> dict[str, Any]:
    before = _state_snapshot()
    data, status = _call_route(core.run_status)
    return _envelope(
        "run.status",
        data,
        before=before,
        after=_state_snapshot(),
        message=f"run: {data.get('status') or '-'} {data.get('completed_runs', 0)}/{data.get('total_runs', 0)}",
        ui_hints={"refresh": ["run"]},
        status_code=status,
    )


def results_folders() -> dict[str, Any]:
    before = _state_snapshot()
    data, status = _call_route(result_routes.results_dock_folders)
    folders = data.get("folders") if isinstance(data.get("folders"), list) else []
    return _envelope(
        "results.folders",
        data,
        before=before,
        after=_state_snapshot(),
        message=f"result folders: {len(folders)}",
        ui_hints={"refresh": ["results"]},
        status_code=status,
    )


def results_scan(root_path: str = "data/dock") -> dict[str, Any]:
    before = _state_snapshot()
    data, status = _call_route(result_routes.scan_results, {"root_path": root_path})
    results = data.get("results") if isinstance(data.get("results"), list) else []
    return _envelope(
        "results.scan",
        data,
        before=before,
        message=f"results: {len(results)}",
        ui_hints={"refresh": ["state", "results"]},
        status_code=status,
        error_code="results_scan_failed",
    )


def results_detail(result_dir: str) -> dict[str, Any]:
    before = _state_snapshot()
    data, status = _call_route(result_routes.results_detail, {"result_dir": str(result_dir or "").strip()})
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    label = result.get("label") or result.get("folder_name") or result.get("ligand_display_name") or "-"
    return _envelope(
        "results.detail",
        data,
        before=before,
        after=_state_snapshot(),
        message=f"result detail: {label}",
        ui_hints={"refresh": ["results", "viewer"]},
        status_code=status,
        error_code="results_detail_failed",
        next_actions=["results.scan"],
    )
