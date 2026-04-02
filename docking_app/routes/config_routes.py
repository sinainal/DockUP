from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from ..config import BASE, DOCK_DIR, RECEPTOR_DIR
from ..helpers import (
    build_flex_residue_spec,
    normalize_docking_config,
    normalize_flex_residue_list,
    normalize_ligand_name_list,
    read_json,
    write_json,
)
from ..services import _existing_files, _init_selection_map, _load_receptor_meta, _normalize_receptor_id
from ..state import DOCKING_CONFIG_DEFAULTS, STATE

router = APIRouter()

@router.post("/api/config/save")
def save_config(payload: dict[str, Any]) -> StreamingResponse:
    # Single sheet "Configuration" containing all receptor data
    # Columns: pdb_id, chain, ligand, grid + run settings + docking settings
    
    # Global settings from payload
    global_runs = payload.get("run_count", STATE["runs"])
    global_pad = payload.get("padding", STATE.get("grid_pad", 0))
    docking_cfg = normalize_docking_config(payload.get("docking_config") or STATE.get("docking_config") or {})
    
    rows = []
    row_type = "Multi-Ligand" if docking_cfg.get("ligand_binding_mode") == "multi_ligand" else STATE["mode"]
    
    # Iterate over loaded receptors
    sel_map = payload.get("selection_map", {})
    grid_data = payload.get("grid_data", {})
    
    for r in STATE["receptor_meta"]:
        pdb_id = _normalize_receptor_id(r.get("pdb_id"))
        if not pdb_id:
            continue
        sel = sel_map.get(pdb_id, {})
        grid = grid_data.get(pdb_id, {})
        
        rows.append({
            "type": row_type, # Add type (Docking/Redocking/Multi-Ligand)
            "pdb_id": pdb_id,
            "chain": sel.get("chain", "all"),
            "ligand": sel.get("ligand_resname", "") or sel.get("ligand", ""),
            "ligands": ",".join(normalize_ligand_name_list(sel.get("ligand_resnames") or [])),
            "ligand_binding_mode": docking_cfg.get("ligand_binding_mode"),
            "grid_center_x": grid.get("cx"),
            "grid_center_y": grid.get("cy"),
            "grid_center_z": grid.get("cz"),
            "grid_size_x": grid.get("sx"),
            "grid_size_y": grid.get("sy"),
            "grid_size_z": grid.get("sz"),
            "run_count": global_runs, # Saving global setting for reference
            "padding": global_pad,     # Saving global setting for reference
            "docking_mode": docking_cfg.get("docking_mode"),
            "flex_residues": build_flex_residue_spec(sel.get("flex_residues") or sel.get("flex_residue_spec") or []),
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
        
    df = pd.DataFrame(rows)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Configuration', index=False)
            
    output.seek(0)
    
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=docking_config.xlsx"}
    )


@router.post("/api/config/load")
def load_config(file: UploadFile = File(...)) -> JSONResponse:
    try:
        content = file.file.read()
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

        return JSONResponse({
            "ok": True,
            "selection_map": selection_map,
            "grid_data": grid_data,
            "queue": STATE["queue"], # Return existing queue (empty or not)
            "mode": STATE["mode"],
            "docking_config": loaded_docking_config,
        })

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
