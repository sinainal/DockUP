from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from tests._support.api_client import ApiClient

WATER_NAMES = {"HOH", "WAT", "DOD"}
NON_MAIN_LIGAND_HINTS = {"PEG", "OLA", "EDO", "GOL", "SO4"}


@dataclass
class BasicFlowArtifacts:
    receptor_id: str = ""
    receptor_file_name: str = ""
    ligand_name: str = ""
    ligand_names: list[str] = field(default_factory=list)
    generated_file_name: str = ""
    out_root: Path | None = None


def _parse_pdb_atom(line: str) -> dict[str, Any] | None:
    if not (line.startswith("ATOM") or line.startswith("HETATM")):
        return None
    if len(line) < 54:
        return None
    try:
        return {
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


def choose_native_ligand_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise AssertionError("Receptor ligand list is empty.")
    for row in rows:
        lig_label = str(row.get("ligand") or "").strip()
        resname, _ = _label_parts(lig_label)
        if resname.upper() not in NON_MAIN_LIGAND_HINTS:
            return row
    return rows[0]


def compute_grid_around_native_ligand(
    pdb_text: str,
    ligand_resname: str,
    ligand_resno: str,
    ligand_chain: str,
    *,
    cutoff: float = 5.0,
    fixed_size: float = 20.0,
) -> dict[str, float]:
    atoms = []
    for line in pdb_text.splitlines():
        atom = _parse_pdb_atom(line)
        if atom:
            atoms.append(atom)

    lig_atoms = [
        a
        for a in atoms
        if a["resname"] == ligand_resname and a["resno"] == ligand_resno and a["chain"] == ligand_chain
    ]
    if not lig_atoms:
        raise AssertionError(
            f"Ligand atoms not found: resname={ligand_resname} resno={ligand_resno} chain={ligand_chain}"
        )

    cutoff_sq = cutoff * cutoff
    neighbor_atoms: list[dict[str, Any]] = []
    for atom in atoms:
        if atom["resname"] in WATER_NAMES:
            continue
        if (
            atom["resname"] == ligand_resname
            and atom["resno"] == ligand_resno
            and atom["chain"] == ligand_chain
        ):
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


def wait_run_finished(api: ApiClient, *, timeout_sec: int, interval_sec: float) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        status = api.assert_ok(api.get("/api/run/status"), where="GET /api/run/status")
        current = str(status.get("status") or "idle")
        if current in {"done", "error", "stopped", "idle"}:
            return status
        time.sleep(interval_sec)
    raise TimeoutError(f"Run did not finish within {timeout_sec} seconds.")


def wait_report_idle(api: ApiClient, *, timeout_sec: int, interval_sec: float) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        status = api.assert_ok(api.get("/api/reports/status"), where="GET /api/reports/status")
        if str(status.get("status") or "") == "idle":
            return status
        time.sleep(interval_sec)
    raise TimeoutError(f"Report status did not return idle in {timeout_sec} seconds.")


def clear_queue(api: ApiClient) -> None:
    probe = api.assert_ok(
        api.post(
            "/api/queue/build",
            {
                "run_count": 1,
                "padding": 0.0,
                "selection_map": {},
                "grid_data": {},
                "mode": "Docking",
                "docking_config": {},
                "out_root_path": "data/dock",
                "out_root_name": f"e2e_probe_{int(time.time() * 1000)}",
            },
        ),
        where="POST /api/queue/build (probe)",
    )
    queue = list(probe.get("queue") or [])
    batch_ids = sorted(
        {
            int(item["batch_id"])
            for item in queue
            if isinstance(item, dict) and item.get("batch_id") is not None
        }
    )
    for batch_id in batch_ids:
        api.post("/api/queue/remove_batch", {"batch_id": batch_id})


def clear_loaded_receptors(api: ApiClient) -> None:
    summary = api.assert_ok(api.get("/api/receptors/summary"), where="GET /api/receptors/summary")
    for row in list(summary.get("summary") or []):
        pdb_id = str(row.get("pdb_id") or "").strip()
        if pdb_id:
            api.post("/api/receptors/remove", {"pdb_id": pdb_id})


def cleanup_basic_flow(api: ApiClient, artifacts: BasicFlowArtifacts) -> None:
    if artifacts.out_root is not None:
        try:
            api.post(
                "/api/run/recent/delete",
                {
                    "out_root": str(artifacts.out_root),
                    "purge_files": True,
                },
            )
        except Exception:
            pass
    try:
        clear_queue(api)
    except Exception:
        pass
    try:
        api.post("/api/ligands/active/clear", {})
    except Exception:
        pass
    if artifacts.receptor_id:
        try:
            api.post("/api/receptors/remove", {"pdb_id": artifacts.receptor_id})
        except Exception:
            pass
    if artifacts.receptor_file_name:
        try:
            api.post("/api/receptors/delete", {"name": artifacts.receptor_file_name})
        except Exception:
            pass
    for ligand_name in list(artifacts.ligand_names or []):
        try:
            api.post("/api/ligands/delete", {"name": ligand_name})
        except Exception:
            pass
    if artifacts.ligand_name:
        try:
            api.post("/api/ligands/delete", {"name": artifacts.ligand_name})
        except Exception:
            pass
    if artifacts.generated_file_name:
        try:
            api.post("/ligand-3d/api/files/delete", {"file_names": [artifacts.generated_file_name]})
        except Exception:
            pass
    if artifacts.out_root and artifacts.out_root.exists():
        try:
            shutil.rmtree(artifacts.out_root)
        except Exception:
            pass


def persist_tree_if_requested(src: Path | None, artifacts_dir: Path | None, name: str) -> Path | None:
    if artifacts_dir is None or src is None or not src.exists():
        return None
    target = artifacts_dir / name
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(src, target)
    return target


def provision_single_docking_run(
    api: ApiClient,
    *,
    stamp: int,
    timeout_sec: int,
    interval_sec: float,
) -> BasicFlowArtifacts:
    artifacts = BasicFlowArtifacts()

    api.assert_ok(api.post("/api/mode", {"mode": "Docking"}), where="POST /api/mode")
    status = api.assert_ok(api.get("/api/run/status"), where="GET /api/run/status")
    if str(status.get("status") or "idle") in {"running", "stopping"}:
        api.post("/api/run/stop", {})
        wait_run_finished(api, timeout_sec=120, interval_sec=max(1.0, interval_sec))

    clear_queue(api)
    clear_loaded_receptors(api)
    api.post("/api/ligands/active/clear", {})

    load_resp = api.assert_ok(api.post("/api/receptors/load", {"pdb_ids": "6CM4"}), where="POST /api/receptors/load")
    summary = list(load_resp.get("summary") or [])
    receptor_row = next((row for row in summary if str(row.get("pdb_id") or "").upper() == "6CM4"), None)
    assert receptor_row is not None, f"6CM4 not found in receptor summary: {summary}"
    receptor_id = str(receptor_row.get("pdb_id") or "6CM4")
    artifacts.receptor_id = receptor_id

    lig_rows_resp = api.assert_ok(
        api.get(f"/api/receptors/{receptor_id}/ligands"),
        where="GET /api/receptors/{id}/ligands",
    )
    ligand_rows = list(lig_rows_resp.get("rows") or [])
    native_ligand = choose_native_ligand_row(ligand_rows)
    native_label = str(native_ligand.get("ligand") or "").strip()
    native_parts = [p for p in native_label.split() if p]
    assert len(native_parts) >= 2, f"Unexpected native ligand label: {native_label}"
    native_resname = native_parts[0]
    native_resno = native_parts[1]
    native_chain = str(native_ligand.get("chain") or "all").strip() or "all"

    detail = api.assert_ok(api.get(f"/api/receptors/{receptor_id}"), where="GET /api/receptors/{id}")
    pdb_text = str(detail.get("pdb_text") or "").strip()
    assert pdb_text, "Receptor detail returned empty pdb_text."
    grid = compute_grid_around_native_ligand(
        pdb_text,
        native_resname,
        native_resno,
        native_chain,
        cutoff=5.0,
        fixed_size=20.0,
    )

    convert = api.assert_ok(
        api.post(
            "/ligand-3d/api/convert3d",
            {"smiles": "C=C", "name": "ethylene", "file_stem": f"e2e_eth_{stamp}"},
            timeout=180,
        ),
        where="POST /ligand-3d/api/convert3d",
    )
    converted_name = str(convert.get("name") or "").strip()
    assert converted_name, f"convert3d returned empty name: {convert}"
    artifacts.generated_file_name = converted_name

    add = api.assert_ok(
        api.post("/ligand-3d/api/ligands/add", {"file_names": [converted_name]}),
        where="POST /ligand-3d/api/ligands/add",
    )
    copied = [str(item or "").strip() for item in list(add.get("copied") or []) if str(item or "").strip()]
    assert copied, f"No copied ligands after add: {add}"
    dock_ligand_name = copied[0]
    artifacts.ligand_name = dock_ligand_name

    active = api.assert_ok(
        api.post("/api/ligands/active/add", {"names": [dock_ligand_name]}),
        where="POST /api/ligands/active/add",
    )
    assert dock_ligand_name in set(active.get("active_ligands") or []), f"Ligand not active: {active}"

    api.assert_ok(
        api.post(
            "/api/ligands/select",
            {"pdb_id": receptor_id, "chain": native_chain, "ligand": dock_ligand_name},
        ),
        where="POST /api/ligands/select",
    )

    out_root_name = f"e2e_basic_{stamp}"
    queue = api.assert_ok(
        api.post(
            "/api/queue/build",
            {
                "run_count": 1,
                "padding": 0.0,
                "out_root_name": out_root_name,
                "out_root_path": "data/dock",
                "selection_map": {receptor_id: {"chain": native_chain, "ligand_resname": dock_ligand_name}},
                "grid_data": {receptor_id: grid},
                "mode": "Docking",
                "docking_config": {},
            },
        ),
        where="POST /api/queue/build",
    )
    added = int((queue.get("debug") or {}).get("new_jobs_added") or 0)
    assert added == 1, f"Expected exactly 1 queue job, got {added}. debug={queue.get('debug')}"

    started = api.assert_ok(
        api.post("/api/run/start", {"is_test_mode": False}, timeout=60),
        where="POST /api/run/start",
    )
    assert str(started.get("status") or "") in {"running", "done"}, f"Unexpected run/start response: {started}"

    final_status = wait_run_finished(api, timeout_sec=timeout_sec, interval_sec=interval_sec)
    assert str(final_status.get("status") or "") == "done", f"Run did not finish successfully: {final_status}"
    assert int(final_status.get("returncode") or 0) == 0, f"Non-zero return code: {final_status}"
    assert int(final_status.get("completed_runs") or 0) >= 1, f"No completed runs: {final_status}"

    out_root = str(final_status.get("out_root") or "").strip()
    assert out_root, f"Missing out_root in run status: {final_status}"
    out_root_path = Path(out_root)
    artifacts.out_root = out_root_path
    assert out_root_path.exists(), f"out_root does not exist: {out_root_path}"
    assert list(out_root_path.rglob("results.json")), f"No results.json under out_root: {out_root_path}"
    return artifacts


def provision_multi_ligand_run(
    api: ApiClient,
    *,
    stamp: int,
    timeout_sec: int,
    interval_sec: float,
) -> BasicFlowArtifacts:
    artifacts = BasicFlowArtifacts()
    shared_root = Path(__file__).resolve().parents[3]
    data_root = shared_root / "Multi_Ligand" / "data"
    receptor_source = data_root / "5x72_receptorH.pdb"
    ligand_sources = [
        data_root / "5x72_ligand_p59.sdf",
        data_root / "5x72_ligand_p69.sdf",
    ]
    assert receptor_source.exists(), f"Missing receptor fixture: {receptor_source}"
    for ligand_source in ligand_sources:
        assert ligand_source.exists(), f"Missing ligand fixture: {ligand_source}"

    api.assert_ok(api.post("/api/mode", {"mode": "Multi-Ligand"}), where="POST /api/mode")
    status = api.assert_ok(api.get("/api/run/status"), where="GET /api/run/status")
    if str(status.get("status") or "idle") in {"running", "stopping"}:
        api.post("/api/run/stop", {})
        wait_run_finished(api, timeout_sec=120, interval_sec=max(1.0, interval_sec))

    clear_queue(api)
    clear_loaded_receptors(api)
    api.post("/api/ligands/active/clear", {})

    receptor_id = f"M{stamp % 1000:03d}"
    receptor_filename = f"{receptor_id}.pdb"
    receptor_resp = requests.post(
        api._url("/api/receptors/upload"),
        files=[("files", (receptor_filename, receptor_source.read_bytes(), "chemical/x-pdb"))],
        timeout=60,
    )
    api.assert_ok(receptor_resp, where="POST /api/receptors/upload")
    artifacts.receptor_id = receptor_id
    artifacts.receptor_file_name = receptor_filename

    load_resp = api.assert_ok(
        api.post("/api/receptors/load", {"pdb_ids": receptor_id}),
        where="POST /api/receptors/load",
    )
    summary = list(load_resp.get("summary") or [])
    receptor_row = next((row for row in summary if str(row.get("pdb_id") or "").upper() == receptor_id), None)
    assert receptor_row is not None, f"{receptor_id} not found in receptor summary: {summary}"

    ligand_names: list[str] = []
    ligand_files = []
    for index, ligand_source in enumerate(ligand_sources, start=1):
        ligand_filename = f"mlig_{stamp}_{index}.sdf"
        ligand_files.append(("files", (ligand_filename, ligand_source.read_bytes(), "chemical/x-mdl-sdfile")))
        ligand_names.append(ligand_filename)
    ligand_resp = requests.post(
        api._url("/api/ligands/upload"),
        files=ligand_files,
        timeout=60,
    )
    ligand_upload = api.assert_ok(ligand_resp, where="POST /api/ligands/upload")
    uploaded_ligands = [str(name or "").strip() for name in list(ligand_upload.get("saved") or []) if str(name or "").strip()]
    assert len(uploaded_ligands) == 2, f"Expected two uploaded ligands, got: {ligand_upload}"
    ligand_names = uploaded_ligands
    artifacts.ligand_names = ligand_names
    artifacts.ligand_name = " + ".join(ligand_names)

    active = api.assert_ok(
        api.post("/api/ligands/active/add", {"names": ligand_names}),
        where="POST /api/ligands/active/add",
    )
    assert set(ligand_names).issubset(set(active.get("active_ligands") or [])), f"Ligands not active: {active}"

    api.assert_ok(
        api.post(
            "/api/ligands/select",
            {"pdb_id": receptor_id, "chain": "all", "ligands": ligand_names},
        ),
        where="POST /api/ligands/select",
    )

    out_root_name = f"e2e_multi_{stamp}"
    queue = api.assert_ok(
        api.post(
            "/api/queue/build",
            {
                "run_count": 1,
                "padding": 0.0,
                "out_root_name": out_root_name,
                "out_root_path": "data/dock",
                "selection_map": {
                    receptor_id: {
                        "chain": "all",
                        "ligand_resname": artifacts.ligand_name,
                        "ligand_resnames": ligand_names,
                    }
                },
                "grid_data": {
                    receptor_id: {
                        "cx": -15.0,
                        "cy": 15.0,
                        "cz": 129.0,
                        "sx": 30.0,
                        "sy": 24.0,
                        "sz": 24.0,
                    }
                },
                "mode": "Multi-Ligand",
                "docking_config": {"docking_mode": "standard", "vina_exhaustiveness": 8, "vina_num_modes": 5},
            },
        ),
        where="POST /api/queue/build",
    )
    added = int((queue.get("debug") or {}).get("new_jobs_added") or 0)
    assert added == 1, f"Expected exactly 1 multi-ligand queue job, got {added}. debug={queue.get('debug')}"

    started = api.assert_ok(
        api.post("/api/run/start", {"is_test_mode": False}, timeout=60),
        where="POST /api/run/start",
    )
    assert str(started.get("status") or "") in {"running", "done"}, f"Unexpected run/start response: {started}"

    final_status = wait_run_finished(api, timeout_sec=timeout_sec, interval_sec=interval_sec)
    assert str(final_status.get("status") or "") == "done", f"Run did not finish successfully: {final_status}"
    assert int(final_status.get("returncode") or 0) == 0, f"Non-zero return code: {final_status}"
    assert int(final_status.get("completed_runs") or 0) >= 1, f"No completed runs: {final_status}"

    out_root = str(final_status.get("out_root") or "").strip()
    assert out_root, f"Missing out_root in run status: {final_status}"
    out_root_path = Path(out_root)
    artifacts.out_root = out_root_path
    assert out_root_path.exists(), f"out_root does not exist: {out_root_path}"
    assert list(out_root_path.rglob("results.json")), f"No results.json under out_root: {out_root_path}"
    return artifacts
