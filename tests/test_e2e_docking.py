#!/usr/bin/env python3
"""End-to-end docking smoke test.

Performs a complete docking pipeline:
  1. Load receptor 7X2F from RCSB
  2. Prepare an ethylene ligand (.sdf) via SMILES → 3D conversion
  3. Upload ligand to the server
  4. Select ligand + chain for the receptor
  5. Build queue with a basic gridbox
  6. Start run (1 run)
  7. Poll until complete or timeout
  8. Report timing

Usage:
    python tests/test_e2e_docking.py              # server already running on :8000
    python tests/test_e2e_docking.py --base-url http://localhost:9000
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "http://localhost:8000"
TIMEOUT_TOTAL = 600   # 10 min max for entire test
POLL_INTERVAL = 3     # seconds between status polls

_CFG = {"base_url": BASE_URL}


def api(method: str, path: str, body: dict | None = None, timeout: int = 60) -> dict:
    """Simple HTTP helper — returns parsed JSON."""
    url = f"{_CFG['base_url']}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} → {exc.code}: {detail}") from exc


def step(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"  [{ts}] {msg}")


def main() -> None:
    parser = argparse.ArgumentParser(description="E2E docking smoke test")
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()
    _CFG["base_url"] = args.base_url.rstrip("/")

    t0 = time.time()
    timings: dict[str, float] = {}

    print("=" * 60)
    print("  DockUP End-to-End Docking Test")
    print("=" * 60)

    # ── 0. Health check ──────────────────────────────────────
    step("Health check...")
    state = api("GET", "/api/state")
    assert "mode" in state, f"Unexpected /api/state response: {state}"
    step(f"  Server OK — mode={state['mode']}")

    # ── 1. Clear previous state ──────────────────────────────
    step("Clearing previous receptors...")
    for meta in state.get("receptor_meta", []):
        api("POST", "/api/receptors/remove", {"pdb_id": meta["pdb_id"]})
    step("Clearing previous queue...")
    api("POST", "/api/queue/remove_batch", {"indices": list(range(200))})

    # ── 2. Load receptor 7X2F ────────────────────────────────
    step("Loading receptor 7X2F from RCSB...")
    t1 = time.time()
    rec = api("POST", "/api/receptors/load", {"pdb_ids": "7X2F"}, timeout=120)
    timings["receptor_load"] = time.time() - t1
    summary = rec.get("summary", [])
    assert any(r["pdb_id"].upper() == "7X2F" for r in summary), (
        f"7X2F not found in summary: {[r['pdb_id'] for r in summary]}"
    )
    step(f"  Loaded {len(summary)} receptor(s) in {timings['receptor_load']:.1f}s")

    # ── 3. Prepare ethylene ligand ───────────────────────────
    ethylene_smiles = "C=C"
    ethylene_name = "ethylene"
    sdf_path = Path(__file__).parent.parent / "docking_app" / "workspace" / "data" / "ligand" / f"{ethylene_name}.sdf"

    if sdf_path.exists():
        step(f"  Ethylene SDF already exists: {sdf_path.name}")
    else:
        step("Creating ethylene SDF from SMILES via RDKit...")
        t1 = time.time()
        try:
            from rdkit import Chem
            from rdkit.Chem import AllChem
            mol = Chem.MolFromSmiles(ethylene_smiles)
            mol = Chem.AddHs(mol)
            AllChem.EmbedMolecule(mol, randomSeed=42)
            AllChem.MMFFOptimizeMolecule(mol)
            writer = Chem.SDWriter(str(sdf_path))
            writer.write(mol)
            writer.close()
            timings["ligand_prep"] = time.time() - t1
            step(f"  Created {sdf_path.name} in {timings['ligand_prep']:.1f}s")
        except ImportError:
            step("  RDKit not available — creating minimal SDF manually")
            # Minimal ethylene SDF
            sdf_content = (
                "ethylene\n"
                "     RDKit          3D\n\n"
                "  2  1  0  0  0  0  0  0  0  0999 V2000\n"
                "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
                "    1.3370    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
                "  1  2  2  0\n"
                "M  END\n"
                "$$$$\n"
            )
            sdf_path.parent.mkdir(parents=True, exist_ok=True)
            sdf_path.write_text(sdf_content)
            timings["ligand_prep"] = time.time() - t1
            step(f"  Created minimal {sdf_path.name}")

    # ── 4. Verify ligand is listed ───────────────────────────
    step("Checking ligand list...")
    ligs = api("GET", "/api/ligands/list")
    raw_ligs = ligs.get("ligands", [])
    lig_names = [l["name"] if isinstance(l, dict) else str(l) for l in raw_ligs]
    sdf_name = f"{ethylene_name}.sdf"
    assert sdf_name in lig_names, f"{sdf_name} not found in ligand list: {lig_names}"
    step(f"  Found {sdf_name} in {len(lig_names)} ligands")

    # ── 5. Select ligand for 7X2F ────────────────────────────
    step("Selecting ethylene + chain A for 7X2F...")
    api("POST", "/api/ligands/select", {
        "pdb_id": "7X2F",
        "chain": "A",
        "ligand": sdf_name,
    })

    # ── 6. Build queue ───────────────────────────────────────
    step("Building queue (1 run, gridbox 25×25×25 at origin)...")
    t1 = time.time()
    qr = api("POST", "/api/queue/build", {
        "run_count": 1,
        "padding": 0,
        "out_root_name": "e2e_test",
        "out_root_path": "data/dock",
        "selection_map": {"7X2F": {"chain": "A", "ligand_resname": sdf_name}},
        "grid_data": {"7X2F": {"cx": 0, "cy": 0, "cz": 0, "sx": 25, "sy": 25, "sz": 25}},
        "mode": "Docking",
        "docking_config": {
            "vina_exhaustiveness": 8,    # low for speed
        },
    })
    timings["queue_build"] = time.time() - t1
    qcount = qr.get("queue_count", 0)
    debug_info = qr.get("debug", {})
    step(f"  Queue has {qcount} job(s) — build took {timings['queue_build']:.1f}s")
    if debug_info.get("skipped"):
        step(f"  ⚠ Skipped: {debug_info['skipped']}")
    assert qcount > 0, f"Queue is empty! debug={debug_info}"

    # ── 7. Start run ─────────────────────────────────────────
    step("Starting docking run...")
    t1 = time.time()
    start_resp = api("POST", "/api/run/start", {
        "runs": 1,
        "out_root": "data/dock/e2e_test",
    }, timeout=30)
    start_status = start_resp.get("status", "")
    step(f"  Run start response: status={start_status}")

    if start_status == "error":
        detail = start_resp.get("detail", "unknown")
        step(f"  ❌ Run start error: {detail}")
        # Show the command that was attempted
        cmd = start_resp.get("command", "")
        if cmd:
            step(f"  Command: {cmd[:200]}")
        timings["run_start_error"] = time.time() - t1
    else:
        # ── 8. Poll until complete or timeout ────────────────
        step("Polling run status...")
        deadline = time.time() + TIMEOUT_TOTAL
        last_status = ""
        while time.time() < deadline:
            time.sleep(POLL_INTERVAL)
            rs = api("GET", "/api/run/status")
            st = rs.get("status", "idle")
            completed = rs.get("completed_runs", 0)
            total = rs.get("total_runs", 0)
            if st != last_status:
                step(f"  status={st}  completed={completed}/{total}")
                last_status = st
            if st in ("idle", "done", "error"):
                break
        timings["docking_run"] = time.time() - t1
        final_status = api("GET", "/api/run/status")
        step(f"  Final: status={final_status.get('status')} rc={final_status.get('returncode')}")

    # ── 9. Report ────────────────────────────────────────────
    total_time = time.time() - t0
    timings["total"] = total_time

    print()
    print("=" * 60)
    print("  TIMING REPORT")
    print("=" * 60)
    for key, val in timings.items():
        print(f"  {key:.<30s} {val:>7.1f}s")
    print("=" * 60)

    # Check docking output
    dock_out = Path(__file__).parent.parent / "docking_app" / "workspace" / "data" / "dock" / "e2e_test"
    if dock_out.exists():
        files = list(dock_out.rglob("*"))
        step(f"Output directory: {dock_out}")
        step(f"  Total files: {len(files)}")
        for f in sorted(files)[:20]:
            step(f"    {f.relative_to(dock_out)}")
    else:
        step(f"⚠ Output dir not found: {dock_out}")

    print()
    if timings.get("docking_run"):
        print(f"✅ E2E test completed in {total_time:.1f}s (docking: {timings.get('docking_run', 0):.1f}s)")
    elif timings.get("run_start_error"):
        print(f"❌ E2E test failed at run start after {total_time:.1f}s")
        sys.exit(1)
    else:
        print(f"⚠ E2E test completed with warnings in {total_time:.1f}s")


if __name__ == "__main__":
    main()
