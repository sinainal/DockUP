from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse

from ..config import LIGAND_DIR, RECEPTOR_DIR
from ..helpers import normalize_docking_config
from ..models import FetchLigandsPayload, LoadReceptorsPayload, SelectReceptorPayload
from ..routes import core
from ..state import RUN_STATE, STATE
from .models import ControlEnvelope, ControlError


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
    return payload.model_dump()


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
