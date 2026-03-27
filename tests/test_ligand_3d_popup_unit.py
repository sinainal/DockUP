from __future__ import annotations

import copy
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from docking_app.config import LIGAND_DIR
from docking_app.ligand_3d.app import DeleteLigandPayload, delete_ligand
from docking_app.state import STATE, save_state_cache


def test_popup_delete_ligand_removes_db_file_and_shared_state():
    snapshot = copy.deepcopy(STATE)
    ligand_name = f"popup_delete_unit_{uuid.uuid4().hex}.sdf"
    ligand_path = LIGAND_DIR / ligand_name
    ligand_path.write_text(
        f"{ligand_name}\n  Ketcher\n\n  0  0  0     0  0            999 V2000\nM  END\n$$$$\n",
        encoding="utf-8",
    )
    try:
        STATE["active_ligands"] = [ligand_name]
        STATE["selected_ligand"] = ligand_name
        STATE["selection_map"] = {
            "6CM4": {"chain": "all", "ligand_resname": ligand_name, "flex_residues": []},
        }

        payload = delete_ligand(DeleteLigandPayload(name=ligand_name))
        assert payload["deleted"] == ligand_name
        assert ligand_name not in set(payload.get("ligands") or [])
        assert not ligand_path.exists()
        assert ligand_name not in STATE.get("active_ligands", [])
        assert str(STATE.get("selected_ligand") or "") == ""
        assert STATE["selection_map"]["6CM4"]["ligand_resname"] == ""
    finally:
        ligand_path.unlink(missing_ok=True)
        STATE.clear()
        STATE.update(snapshot)
        save_state_cache()
