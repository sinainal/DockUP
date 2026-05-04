from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..config import RECEPTOR_DIR
from ..helpers import (
    build_flex_residue_spec,
    normalize_docking_config,
    normalize_flex_residue_list,
    normalize_ligand_name_list,
)
from ..services import _existing_files, _init_selection_map, _load_receptor_meta, _normalize_receptor_id
from ..state import STATE, save_state_cache

router = APIRouter()
CONFIG_SCHEMA = "dockup.config.v1"


@router.post("/api/config/docking")
def save_docking_config(payload: dict[str, Any]) -> JSONResponse:
    cfg = normalize_docking_config(payload.get("docking_config") or payload or STATE.get("docking_config") or {})
    STATE["docking_config"] = cfg
    save_state_cache()
    return JSONResponse({"ok": True, "docking_config": cfg})


def _to_int(value: Any, default: int = 1) -> int:
    try:
        parsed = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    return max(1, parsed)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _clean_optional_number(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalise_selection_map(raw: Any) -> dict[str, dict[str, Any]]:
    source = raw if isinstance(raw, dict) else {}
    out: dict[str, dict[str, Any]] = {}
    for raw_pdb_id, raw_sel in source.items():
        pdb_id = _normalize_receptor_id(raw_pdb_id)
        if not pdb_id:
            continue
        sel = raw_sel if isinstance(raw_sel, dict) else {}
        ligand_names = normalize_ligand_name_list(sel.get("ligand_resnames") or sel.get("ligands") or [])
        ligand_label = str(sel.get("ligand_resname") or sel.get("ligand") or "").strip()
        if not ligand_names and ligand_label and ligand_label != "all_set":
            ligand_names = [ligand_label]
        out[pdb_id] = {
            "chain": str(sel.get("chain") or "all").strip() or "all",
            "ligand_resname": ligand_label,
            "ligand_resnames": ligand_names,
            "flex_residues": normalize_flex_residue_list(sel.get("flex_residues") or sel.get("flex_residue_spec") or []),
        }
    return out


def _normalise_grid_data(raw: Any) -> dict[str, dict[str, float]]:
    source = raw if isinstance(raw, dict) else {}
    out: dict[str, dict[str, float]] = {}
    for raw_pdb_id, raw_grid in source.items():
        pdb_id = _normalize_receptor_id(raw_pdb_id)
        grid = raw_grid if isinstance(raw_grid, dict) else {}
        values = {
            "cx": _clean_optional_number(grid.get("cx")),
            "cy": _clean_optional_number(grid.get("cy")),
            "cz": _clean_optional_number(grid.get("cz")),
            "sx": _clean_optional_number(grid.get("sx")),
            "sy": _clean_optional_number(grid.get("sy")),
            "sz": _clean_optional_number(grid.get("sz")),
        }
        if pdb_id and all(value is not None for value in values.values()):
            out[pdb_id] = {key: float(value) for key, value in values.items() if value is not None}
    return out


def _config_document_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    selection_source = payload.get("selection_map") if "selection_map" in payload else STATE.get("selection_map", {})
    grid_source = payload.get("grid_data") if "grid_data" in payload else STATE.get("agent_grid_data", {})
    return {
        "schema": CONFIG_SCHEMA,
        "mode": str(payload.get("mode") or STATE.get("mode") or "Docking"),
        "run_count": _to_int(payload.get("run_count", STATE.get("runs", 1))),
        "padding": _to_float(payload.get("padding", STATE.get("grid_pad", 0))),
        "out_root_path": str(payload.get("out_root_path") or STATE.get("out_root_path") or "data/dock"),
        "out_root_name": str(payload.get("out_root_name") or STATE.get("out_root_name") or ""),
        "docking_config": normalize_docking_config(payload.get("docking_config") or STATE.get("docking_config") or {}),
        "selection_map": _normalise_selection_map(selection_source),
        "grid_data": _normalise_grid_data(grid_source),
    }


def _load_receptors_for_config(pdb_ids: list[str]) -> None:
    requested = [_normalize_receptor_id(pid) for pid in pdb_ids if _normalize_receptor_id(pid)]
    if not requested:
        return
    existing_ids = {_normalize_receptor_id(r.get("pdb_id")) for r in STATE.get("receptor_meta", [])}
    new_ids = [pid for pid in requested if pid not in existing_ids]
    if not new_ids:
        return
    pdb_files = _existing_files(RECEPTOR_DIR, (".pdb",))
    meta = _load_receptor_meta(new_ids, pdb_files)
    if meta:
        STATE["receptor_meta"].extend(meta)
        STATE["selection_map"].update(_init_selection_map(meta))


def _apply_config_document(config: dict[str, Any]) -> dict[str, Any]:
    selection_map = _normalise_selection_map(config.get("selection_map") or config.get("s") or {})
    grid_data = _normalise_grid_data(config.get("grid_data") or config.get("g") or {})
    pdb_ids = sorted(set(selection_map.keys()) | set(grid_data.keys()))
    _load_receptors_for_config(pdb_ids)

    loaded_docking_config = normalize_docking_config(
        config.get("docking_config") or config.get("dc") or STATE.get("docking_config") or {}
    )
    mode = str(config.get("mode") or config.get("m") or STATE.get("mode") or "Docking").strip() or "Docking"
    if mode == "Multi-Ligand":
        mode = "Docking"
        loaded_docking_config = normalize_docking_config({**loaded_docking_config, "ligand_binding_mode": "multi_ligand"})

    STATE["mode"] = mode
    STATE["runs"] = _to_int(config.get("run_count", config.get("runs", STATE.get("runs", 1))))
    STATE["grid_pad"] = _to_float(config.get("padding", config.get("grid_pad", STATE.get("grid_pad", 0))))
    STATE["out_root_path"] = str(config.get("out_root_path") or STATE.get("out_root_path") or "data/dock")
    STATE["out_root_name"] = str(config.get("out_root_name") or STATE.get("out_root_name") or "")
    STATE["selection_map"].update(selection_map)
    if grid_data:
        current_grid = STATE.get("agent_grid_data") if isinstance(STATE.get("agent_grid_data"), dict) else {}
        STATE["agent_grid_data"] = {**current_grid, **grid_data}
    STATE["docking_config"] = loaded_docking_config
    save_state_cache()

    return {
        "ok": True,
        "schema": CONFIG_SCHEMA,
        "selection_map": selection_map,
        "grid_data": grid_data,
        "queue": STATE["queue"],
        "mode": STATE["mode"],
        "run_count": STATE["runs"],
        "padding": STATE["grid_pad"],
        "out_root_path": STATE.get("out_root_path", "data/dock"),
        "out_root_name": STATE.get("out_root_name", ""),
        "docking_config": loaded_docking_config,
    }


def _xlsx_response_from_document(config: dict[str, Any]) -> StreamingResponse:
    docking_cfg = normalize_docking_config(config.get("docking_config") or {})
    rows = []
    row_type = "Multi-Ligand" if docking_cfg.get("ligand_binding_mode") == "multi_ligand" else config.get("mode", "Docking")
    sel_map = _normalise_selection_map(config.get("selection_map") or {})
    grid_data = _normalise_grid_data(config.get("grid_data") or {})

    for pdb_id, sel in sel_map.items():
        grid = grid_data.get(pdb_id, {})
        rows.append({
            "type": row_type,
            "pdb_id": pdb_id,
            "chain": sel.get("chain", "all"),
            "ligand": sel.get("ligand_resname", ""),
            "ligands": ",".join(normalize_ligand_name_list(sel.get("ligand_resnames") or [])),
            "ligand_binding_mode": docking_cfg.get("ligand_binding_mode"),
            "grid_center_x": grid.get("cx"),
            "grid_center_y": grid.get("cy"),
            "grid_center_z": grid.get("cz"),
            "grid_size_x": grid.get("sx"),
            "grid_size_y": grid.get("sy"),
            "grid_size_z": grid.get("sz"),
            "run_count": config.get("run_count", 1),
            "padding": config.get("padding", 0),
            "docking_mode": docking_cfg.get("docking_mode"),
            "flex_residues": build_flex_residue_spec(sel.get("flex_residues") or []),
            "pdb2pqr_ph": docking_cfg.get("pdb2pqr_ph"),
            "pdb2pqr_ff": docking_cfg.get("pdb2pqr_ff"),
            "pdb2pqr_ffout": docking_cfg.get("pdb2pqr_ffout"),
            "pdb2pqr_nodebump": docking_cfg.get("pdb2pqr_nodebump"),
            "pdb2pqr_keep_chain": docking_cfg.get("pdb2pqr_keep_chain"),
            "mkrec_allow_bad_res": docking_cfg.get("mkrec_allow_bad_res"),
            "mkrec_default_altloc": docking_cfg.get("mkrec_default_altloc"),
            "vina_exhaustiveness": docking_cfg.get("vina_exhaustiveness"),
            "vina_num_modes": docking_cfg.get("vina_num_modes"),
            "vina_energy_range": docking_cfg.get("vina_energy_range"),
            "vina_cpu": docking_cfg.get("vina_cpu"),
            "vina_seed": docking_cfg.get("vina_seed"),
        })

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="Configuration", index=False)
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=docking_config.xlsx"},
    )


@router.post("/api/config/save")
def save_config(payload: dict[str, Any]) -> Response:
    config = _config_document_from_payload(payload)
    fmt = str(payload.get("format") or payload.get("config_format") or "xlsx").strip().lower()
    if fmt == "json":
        content = json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=docking_config.json"},
        )
    return _xlsx_response_from_document(config)


@router.post("/api/config/load")
def load_config(file: UploadFile = File(...)) -> JSONResponse:
    try:
        content = file.file.read()
        filename = str(file.filename or "").lower()
        content_type = str(file.content_type or "").lower()
        if filename.endswith(".json") or "json" in content_type:
            data = json.loads(content.decode("utf-8"))
            if not isinstance(data, dict):
                return JSONResponse({"error": "Config JSON must be an object."}, status_code=400)
            return JSONResponse(_apply_config_document(data))

        xls = pd.ExcelFile(io.BytesIO(content))
        
        selection_map = {}
        grid_data = {}
        loaded_docking_config = normalize_docking_config(STATE.get("docking_config") or {})
        
        if 'Configuration' in xls.sheet_names:
            df = pd.read_excel(xls, 'Configuration')
            
            # 1. Load Receptors
            pdb_ids = df["pdb_id"].dropna().astype(str).unique().tolist()
            pdb_ids = [_normalize_receptor_id(pid) for pid in pdb_ids if _normalize_receptor_id(pid)]
            if pdb_ids:
                existing_ids = {_normalize_receptor_id(r.get("pdb_id")) for r in STATE["receptor_meta"]}
                new_ids = [p for p in pdb_ids if p not in existing_ids]
                
                if new_ids:
                    pdb_files = _existing_files(RECEPTOR_DIR, (".pdb",))
                    meta = _load_receptor_meta(new_ids, pdb_files)
                    STATE["receptor_meta"].extend(meta)
                    _init_selection_map(meta)

            # 2. Restore State
            for _, row in df.iterrows():
                pdb_id = _normalize_receptor_id(row.get("pdb_id"))
                if not pdb_id:
                    continue
                
                # Selection
                selection_map[pdb_id] = {
                    "chain": str(row["chain"]) if pd.notna(row["chain"]) else "all",
                    "ligand_resname": str(row["ligand"]) if pd.notna(row["ligand"]) else "",
                    "ligand_resnames": normalize_ligand_name_list(row.get("ligands") if pd.notna(row.get("ligands")) else []),
                    "flex_residues": normalize_flex_residue_list(row.get("flex_residues") if pd.notna(row.get("flex_residues")) else []),
                }
                
                # Grid
                if pd.notna(row.get("grid_center_x")):
                    grid_data[pdb_id] = {
                        "cx": float(row["grid_center_x"]),
                        "cy": float(row["grid_center_y"]),
                        "cz": float(row["grid_center_z"]),
                        "sx": float(row["grid_size_x"]),
                        "sy": float(row["grid_size_y"]),
                        "sz": float(row["grid_size_z"])
                    }
            
            # Update server state
            STATE["selection_map"].update(selection_map)
            if grid_data:
                current_grid = STATE.get("agent_grid_data") if isinstance(STATE.get("agent_grid_data"), dict) else {}
                STATE["agent_grid_data"] = {**current_grid, **grid_data}
            
            # Optionally restore global settings from first row
            if not df.empty:
                first_row = df.iloc[0]
                if pd.notna(first_row.get("run_count")):
                    STATE["runs"] = int(first_row["run_count"])
                if pd.notna(first_row.get("padding")):
                    STATE["grid_pad"] = float(first_row["padding"])
                if pd.notna(first_row.get("type")):
                    loaded_type = str(first_row["type"])
                    STATE["mode"] = "Docking" if loaded_type == "Multi-Ligand" else loaded_type

                loaded_docking_config = normalize_docking_config(
                    {
                        "docking_mode": first_row.get("docking_mode"),
                        "ligand_binding_mode": (
                            "multi_ligand"
                            if str(first_row.get("type") or "").strip() == "Multi-Ligand"
                            else first_row.get("ligand_binding_mode")
                        ),
                        "pdb2pqr_ph": first_row.get("pdb2pqr_ph"),
                        "pdb2pqr_ff": first_row.get("pdb2pqr_ff"),
                        "pdb2pqr_ffout": first_row.get("pdb2pqr_ffout"),
                        "pdb2pqr_nodebump": first_row.get("pdb2pqr_nodebump"),
                        "pdb2pqr_keep_chain": first_row.get("pdb2pqr_keep_chain"),
                        "mkrec_allow_bad_res": first_row.get("mkrec_allow_bad_res"),
                        "mkrec_default_altloc": first_row.get("mkrec_default_altloc"),
                        "vina_exhaustiveness": first_row.get("vina_exhaustiveness"),
                        "vina_num_modes": first_row.get("vina_num_modes"),
                        "vina_energy_range": first_row.get("vina_energy_range"),
                        "vina_cpu": first_row.get("vina_cpu"),
                        "vina_seed": first_row.get("vina_seed"),
                    }
                )
                STATE["docking_config"] = loaded_docking_config

        save_state_cache()
        return JSONResponse({
            "ok": True,
            "schema": CONFIG_SCHEMA,
            "selection_map": selection_map,
            "grid_data": grid_data,
            "queue": STATE["queue"],
            "mode": STATE["mode"],
            "run_count": STATE["runs"],
            "padding": STATE["grid_pad"],
            "out_root_path": STATE.get("out_root_path", "data/dock"),
            "out_root_name": STATE.get("out_root_name", ""),
            "docking_config": loaded_docking_config,
        })

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
