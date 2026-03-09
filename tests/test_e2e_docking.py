#!/usr/bin/env python3
"""Manual-flow E2E docking test.

This script mimics the server-side flow a user performs from the UI:
1) Load receptor 6CM4
2) Fetch ethylene from PubChem via ligand-3d API
3) Convert to 3D SDF and add it to docking ligands
4) Compute a 20A grid centered on receptor main ligand neighborhood
5) Build queue (1 run) and start docking
6) Poll until completion and validate output artifacts
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


BASE_URL = "http://localhost:8000"
POLL_INTERVAL_SECONDS = 3
TOTAL_TIMEOUT_SECONDS = 20 * 60

WATER_NAMES = {"HOH", "WAT", "DOD"}
NON_MAIN_LIGAND_HINTS = {"PEG", "OLA", "EDO", "GOL", "SO4"}

_CFG = {"base_url": BASE_URL}


def ts() -> str:
    return time.strftime("%H:%M:%S")


def step(msg: str) -> None:
    print(f"[{ts()}] {msg}")


def _parse_json_bytes(data: bytes) -> dict[str, Any]:
    text = data.decode("utf-8", errors="replace")
    if not text.strip():
        return {}
    return json.loads(text)


def api(method: str, path: str, body: dict[str, Any] | None = None, timeout: int = 60) -> dict[str, Any]:
    base = _CFG["base_url"].rstrip("/")
    url = f"{base}{path}"
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _parse_json_bytes(resp.read())
    except urllib.error.HTTPError as exc:
        detail_text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {detail_text}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method} {path} -> network error: {exc}") from exc


def _parse_pdb_atom(line: str) -> dict[str, Any] | None:
    if not (line.startswith("ATOM") or line.startswith("HETATM")):
        return None
    if len(line) < 54:
        return None
    try:
        return {
            "record": line[0:6].strip(),
            "resname": line[17:20].strip(),
            "chain": (line[21].strip() or "_"),
            "resno": line[22:26].strip(),
            "x": float(line[30:38]),
            "y": float(line[38:46]),
            "z": float(line[46:54]),
        }
    except ValueError:
        return None


def _label_parts(label: str) -> tuple[str, str]:
    parts = [p for p in str(label or "").split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _pick_main_ligand(ligand_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not ligand_rows:
        raise RuntimeError("Receptor ligand list is empty.")

    for row in ligand_rows:
        lig_label = str(row.get("ligand") or "").strip()
        resname, _ = _label_parts(lig_label)
        if resname.upper() not in NON_MAIN_LIGAND_HINTS:
            return row
    return ligand_rows[0]


def _compute_grid_around_ligand(
    pdb_text: str,
    ligand_resname: str,
    ligand_resno: str,
    ligand_chain: str,
    cutoff: float = 5.0,
    fixed_size: float = 20.0,
) -> dict[str, float]:
    atoms: list[dict[str, Any]] = []
    for line in pdb_text.splitlines():
        atom = _parse_pdb_atom(line)
        if atom:
            atoms.append(atom)

    lig_atoms = [
        a
        for a in atoms
        if a["resname"] == ligand_resname
        and a["resno"] == ligand_resno
        and a["chain"] == ligand_chain
    ]
    if not lig_atoms:
        raise RuntimeError(
            f"Failed to locate ligand atoms for {ligand_resname} {ligand_resno} chain {ligand_chain}."
        )

    cutoff_sq = cutoff * cutoff
    neighbor_atoms: list[dict[str, Any]] = []
    for atom in atoms:
        if (
            atom["resname"] == ligand_resname
            and atom["resno"] == ligand_resno
            and atom["chain"] == ligand_chain
        ):
            continue
        if atom["resname"] in WATER_NAMES:
            continue
        for lig_atom in lig_atoms:
            dx = atom["x"] - lig_atom["x"]
            dy = atom["y"] - lig_atom["y"]
            dz = atom["z"] - lig_atom["z"]
            if (dx * dx + dy * dy + dz * dz) < cutoff_sq:
                neighbor_atoms.append(atom)
                break

    points = lig_atoms + neighbor_atoms
    min_x = min(p["x"] for p in points)
    min_y = min(p["y"] for p in points)
    min_z = min(p["z"] for p in points)
    max_x = max(p["x"] for p in points)
    max_y = max(p["y"] for p in points)
    max_z = max(p["z"] for p in points)

    return {
        "cx": (min_x + max_x) / 2.0,
        "cy": (min_y + max_y) / 2.0,
        "cz": (min_z + max_z) / 2.0,
        "sx": float(fixed_size),
        "sy": float(fixed_size),
        "sz": float(fixed_size),
    }


def _drain_queue() -> None:
    probe = api(
        "POST",
        "/api/queue/build",
        {
            "run_count": 1,
            "padding": 0.0,
            "selection_map": {},
            "grid_data": {},
            "mode": "Docking",
            "docking_config": {},
            "out_root_path": "data/dock",
            "out_root_name": f"e2e_probe_{int(time.time())}",
        },
    )
    queue = list(probe.get("queue") or [])
    if not queue:
        return

    batch_ids = sorted(
        {int(item["batch_id"]) for item in queue if isinstance(item, dict) and item.get("batch_id") is not None}
    )
    if not batch_ids:
        raise RuntimeError("Queue contains items but no batch_id values; cannot clear queue safely.")

    for batch_id in batch_ids:
        api("POST", "/api/queue/remove_batch", {"batch_id": batch_id})


def _clear_loaded_receptors() -> None:
    summary_payload = api("GET", "/api/receptors/summary")
    for row in list(summary_payload.get("summary") or []):
        pdb_id = str(row.get("pdb_id") or "").strip()
        if pdb_id:
            api("POST", "/api/receptors/remove", {"pdb_id": pdb_id})


def _fetch_ethylene_smiles() -> tuple[str, str]:
    data = api("GET", "/ligand-3d/api/pubchem/search?q=ethylene&limit=10")
    rows = list(data.get("results") or [])
    if not rows:
        raise RuntimeError("PubChem search returned no results for ethylene.")

    chosen = None
    for row in rows:
        name = str(row.get("name") or "").strip().lower()
        if name == "ethylene":
            chosen = row
            break
    if chosen is None:
        for row in rows:
            smiles = str(row.get("smiles") or "").strip()
            if smiles == "C=C":
                chosen = row
                break
    if chosen is None:
        chosen = rows[0]

    smiles = str(chosen.get("smiles") or "").strip()
    if not smiles:
        raise RuntimeError(f"Chosen PubChem row has empty SMILES: {chosen}")

    source_name = str(chosen.get("name") or "").strip() or "unknown"
    return smiles, source_name


def _wait_for_run_end(timeout_sec: int) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    last_status = ""
    while time.time() < deadline:
        status = api("GET", "/api/run/status")
        current = str(status.get("status") or "idle")
        if current != last_status:
            completed = int(status.get("completed_runs") or 0)
            total = int(status.get("total_runs") or 0)
            step(f"Run status: {current} ({completed}/{total})")
            last_status = current
        if current in {"done", "error", "stopped", "idle"}:
            return status
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"Run did not finish within {timeout_sec} seconds.")


def run_e2e() -> None:
    run_stamp = time.strftime("%Y%m%d_%H%M%S")
    created_out_root: Path | None = None
    created_ligand_name = ""
    loaded_receptor_id = ""

    try:
        step("Health check /api/state")
        state = api("GET", "/api/state")
        if "mode" not in state:
            raise RuntimeError(f"Unexpected /api/state response: {state}")

        step("Setting mode=Docking")
        api("POST", "/api/mode", {"mode": "Docking"})

        status = api("GET", "/api/run/status")
        if str(status.get("status") or "idle") in {"running", "stopping"}:
            step("Active run detected; sending stop request")
            api("POST", "/api/run/stop", {})
            _wait_for_run_end(timeout_sec=120)

        step("Clearing queue")
        _drain_queue()

        step("Clearing loaded receptors")
        _clear_loaded_receptors()

        step("Clearing dock-ready ligand pool")
        api("POST", "/api/ligands/active/clear", {})

        step("Loading receptor 6CM4")
        load_resp = api("POST", "/api/receptors/load", {"pdb_ids": "6CM4"}, timeout=180)
        summary = list(load_resp.get("summary") or [])
        if not summary:
            raise RuntimeError(f"/api/receptors/load returned empty summary: {load_resp}")

        receptor_id = ""
        for row in summary:
            pdb_id = str(row.get("pdb_id") or "")
            if pdb_id.upper() == "6CM4":
                receptor_id = pdb_id
                if pdb_id == "6CM4":
                    break
        if not receptor_id:
            raise RuntimeError(f"6CM4 not found in receptor summary: {summary}")
        loaded_receptor_id = receptor_id

        step(f"Using receptor id: {receptor_id}")
        ligands_resp = api("GET", f"/api/receptors/{urllib.parse.quote(receptor_id)}/ligands")
        ligand_rows = list(ligands_resp.get("rows") or [])
        if not ligand_rows:
            raise RuntimeError(f"No native ligands returned for receptor {receptor_id}.")

        main_ligand_row = _pick_main_ligand(ligand_rows)
        main_ligand_label = str(main_ligand_row.get("ligand") or "").strip()
        main_ligand_chain = str(main_ligand_row.get("chain") or "all").strip() or "all"
        main_ligand_resname, main_ligand_resno = _label_parts(main_ligand_label)
        if not main_ligand_resname or not main_ligand_resno:
            raise RuntimeError(f"Invalid ligand label format: {main_ligand_label}")

        step(f"Selected native ligand for grid: {main_ligand_label} (chain {main_ligand_chain})")

        detail_resp = api("GET", f"/api/receptors/{urllib.parse.quote(receptor_id)}")
        pdb_text = str(detail_resp.get("pdb_text") or "")
        if not pdb_text:
            raise RuntimeError(f"Receptor {receptor_id} has empty pdb_text; cannot compute native-ligand grid.")

        grid = _compute_grid_around_ligand(
            pdb_text=pdb_text,
            ligand_resname=main_ligand_resname,
            ligand_resno=main_ligand_resno,
            ligand_chain=main_ligand_chain,
            cutoff=5.0,
            fixed_size=20.0,
        )
        step("Computed grid center=" f"({grid['cx']:.3f}, {grid['cy']:.3f}, {grid['cz']:.3f}) size=20x20x20")

        step("Fetching ethylene from PubChem endpoint")
        smiles, source_name = _fetch_ethylene_smiles()
        step(f"PubChem hit: {source_name} | SMILES={smiles}")

        step("Converting ethylene SMILES to 3D SDF via ligand-3d API")
        convert_resp = api(
            "POST",
            "/ligand-3d/api/convert3d",
            {"smiles": smiles, "name": "ethylene", "file_stem": f"ethylene_dock_test_{run_stamp}"},
            timeout=180,
        )
        generated_name = str(convert_resp.get("name") or "").strip()
        if not generated_name:
            raise RuntimeError(f"convert3d response missing file name: {convert_resp}")
        step(f"Generated ligand file: {generated_name}")

        step("Adding generated ligand into main docking ligand directory")
        before_list = api("GET", "/api/ligands/list")
        before_ligands = {
            str(item["name"] if isinstance(item, dict) else item).strip()
            for item in (before_list.get("ligands") or [])
            if str(item["name"] if isinstance(item, dict) else item).strip()
        }
        add_resp = api("POST", "/ligand-3d/api/ligands/add", {"file_names": [generated_name]})
        copied = list(add_resp.get("copied") or [])
        if not copied:
            raise RuntimeError(f"/ligand-3d/api/ligands/add copied nothing: {add_resp}")
        ligand_filename = str(copied[0] or "").strip()

        lig_list_resp = api("GET", "/api/ligands/list")
        ligands = {
            str(item["name"] if isinstance(item, dict) else item).strip()
            for item in (lig_list_resp.get("ligands") or [])
            if str(item["name"] if isinstance(item, dict) else item).strip()
        }
        if ligand_filename not in ligands:
            newly_added = sorted(name for name in ligands if name not in before_ligands)
            if len(newly_added) == 1:
                ligand_filename = newly_added[0]
            else:
                raise RuntimeError(f"Cannot resolve added ligand name. copied={copied} newly_added={newly_added}")
        step(f"Ligand added to docking inventory: {ligand_filename}")
        created_ligand_name = ligand_filename

        step("Adding ligand into dock-ready pool")
        active_resp = api("POST", "/api/ligands/active/add", {"names": [ligand_filename]})
        active_ligands = [str(item or "").strip() for item in (active_resp.get("active_ligands") or [])]
        if ligand_filename not in active_ligands:
            raise RuntimeError(
                f"Failed to add ligand into dock-ready pool: {ligand_filename} response={active_resp}"
            )

        step("Selecting ethylene ligand for receptor")
        api(
            "POST",
            "/api/ligands/select",
            {"pdb_id": receptor_id, "chain": main_ligand_chain, "ligand": ligand_filename},
        )

        out_root_name = f"e2e_6cm4_ethylene_{run_stamp}"
        step("Building queue")
        queue_resp = api(
            "POST",
            "/api/queue/build",
            {
                "run_count": 1,
                "padding": 0.0,
                "out_root_name": out_root_name,
                "out_root_path": "data/dock",
                "selection_map": {receptor_id: {"chain": main_ligand_chain, "ligand_resname": ligand_filename}},
                "grid_data": {receptor_id: grid},
                "mode": "Docking",
                "docking_config": {},
            },
        )

        debug = queue_resp.get("debug") or {}
        added = int(debug.get("new_jobs_added") or 0)
        if added != 1:
            raise RuntimeError(f"Expected exactly 1 new queue job, got {added}. queue/build debug={debug}")
        step("Queue build produced 1 job")

        step("Starting docking run")
        start_resp = api("POST", "/api/run/start", {"is_test_mode": False}, timeout=30)
        start_status = str(start_resp.get("status") or "")
        if start_status not in {"running", "done"}:
            raise RuntimeError(f"Unexpected /api/run/start status: {start_resp}")

        final_status = _wait_for_run_end(timeout_sec=TOTAL_TIMEOUT_SECONDS)
        final_state = str(final_status.get("status") or "")
        if final_state != "done":
            tail_log = str(final_status.get("log") or "").splitlines()[-20:]
            raise RuntimeError(
                "Docking run did not finish successfully. "
                f"status={final_state}, returncode={final_status.get('returncode')}, "
                f"log_tail={tail_log}"
            )
        if int(final_status.get("returncode") or 0) != 0:
            raise RuntimeError(f"Run returned non-zero code: {final_status}")
        if int(final_status.get("completed_runs") or 0) < 1:
            raise RuntimeError(f"completed_runs is < 1: {final_status}")

        out_root = str(final_status.get("out_root") or "").strip()
        if not out_root:
            raise RuntimeError("Run finished but out_root is empty.")
        out_root_path = Path(out_root)
        created_out_root = out_root_path
        if not out_root_path.exists():
            raise RuntimeError(f"out_root does not exist on disk: {out_root_path}")

        results_files = list(out_root_path.rglob("results.json"))
        if not results_files:
            raise RuntimeError(f"No results.json produced under out_root={out_root_path}")

        step("E2E docking flow completed successfully")
        step(f"out_root={out_root_path}")
        step(f"results_json={results_files[0]}")
    finally:
        try:
            _drain_queue()
        except Exception:
            pass
        try:
            api("POST", "/api/ligands/active/clear", {})
        except Exception:
            pass
        if loaded_receptor_id:
            try:
                api("POST", "/api/receptors/remove", {"pdb_id": loaded_receptor_id})
            except Exception:
                pass
        if created_ligand_name:
            try:
                api("POST", "/api/ligands/delete", {"name": created_ligand_name})
            except Exception:
                pass
        if created_out_root and created_out_root.exists():
            try:
                shutil.rmtree(created_out_root)
            except Exception:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="DockUP manual-flow E2E docking test")
    parser.add_argument("--base-url", default=os.getenv("DOCKUP_BASE_URL", BASE_URL))
    args = parser.parse_args()
    _CFG["base_url"] = str(args.base_url).rstrip("/")

    started = time.time()
    print("=" * 68)
    print("DockUP E2E (manual-flow mimic): 6CM4 + ethylene + 20A native-ligand grid")
    print("=" * 68)
    print(f"Base URL: {_CFG['base_url']}")
    try:
        run_e2e()
    except Exception as exc:
        elapsed = time.time() - started
        print()
        print(f"E2E FAILED after {elapsed:.1f}s")
        print(str(exc))
        sys.exit(1)

    elapsed = time.time() - started
    print()
    print(f"E2E PASSED in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
