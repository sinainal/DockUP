#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT_DIR"

export PATH="$ROOT_DIR/.venv/bin:$PATH"
export DOCKUP_VINA_GPU_21_THREADS="${DOCKUP_VINA_GPU_21_THREADS:-1000}"
BASE_URL="${DOCKUP_BASE_URL:-http://127.0.0.1:8000}"
SERVER_LOG="${DOCKUP_SERVER_LOG:-/tmp/dockup_server_8000.log}"

api_ready() {
  "$ROOT_DIR/.venv/bin/python" - "$BASE_URL" <<'PY' >/dev/null 2>&1
import sys, urllib.request
with urllib.request.urlopen(sys.argv[1] + "/api/state", timeout=3) as response:
    raise SystemExit(0 if response.status == 200 else 1)
PY
}

if ! api_ready; then
  nohup "$ROOT_DIR/.venv/bin/python" -m uvicorn docking_app.app:app \
    --host 0.0.0.0 --port 8000 > "$SERVER_LOG" 2>&1 &
  for _ in {1..60}; do
    api_ready && break
    sleep 1
  done
fi
api_ready || { echo "DockUP backend is not reachable at $BASE_URL" >&2; exit 1; }

"$ROOT_DIR/.venv/bin/python" - "$BASE_URL" <<'PY'
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

BASE = __import__("sys").argv[1].rstrip("/")
DOCK = Path("docking_app/workspace/data/dock")

GRID_30A_BASE = {
    "7X2F": {"cx": 100.824, "cy": 95.249, "cz": 71.751, "sx": 25, "sy": 25, "sz": 25},
    "6CM4": {"cx": 10.579, "cy": 5.044, "cz": -8.671, "sx": 25, "sy": 25, "sz": 25},
    "3PBL": {"cx": 0.085, "cy": -14.828, "cz": 10.432, "sx": 25, "sy": 25, "sz": 25},
    "5WIU": {"cx": -17.86, "cy": 13.964, "cz": -16.379, "sx": 25, "sy": 25, "sz": 25},
    "8IRV": {"cx": 101.391, "cy": 112.741, "cz": 84.153, "sx": 25, "sy": 25, "sz": 25},
}
CHAINS = {"6CM4": "A", "3PBL": "A", "5WIU": "A", "7X2F": "F", "8IRV": "R"}
NATIVE = {"6CM4": "8NU 2001", "3PBL": "ETQ 1200", "5WIU": "AQD 1201", "7X2F": "LDP 504", "8IRV": "R5F 501"}
RECEPTORS = ["6CM4", "3PBL", "5WIU", "7X2F", "8IRV"]
LIGANDS = ["Ethylene_terephthalate_trimer.sdf", "Styrene_trimer.sdf", "Propylene_trimer.sdf", "Ethylene_trimer.sdf"]

COMMON = {
    "docking_mode": "standard",
    "ligand_binding_mode": "single",
    "pdb2pqr_ph": 7.4,
    "pdb2pqr_ff": "AMBER",
    "pdb2pqr_ffout": "AMBER",
    "pdb2pqr_nodebump": True,
    "pdb2pqr_keep_chain": True,
    "mkrec_allow_bad_res": True,
    "mkrec_default_altloc": "A",
    "vina_exhaustiveness": 32,
    "vina_gpu_threads": 1000,
    "vina_gpu_box_profile": "medium",
}

SETS = [
    {
        "label": "30A Vina-GPU report-native",
        "engine": "vina_gpu_21",
        "redock_root": "dopamine_redock_30A_vinagpu_medium_report_native_5runs_20260607",
        "trimer_root": "dopamine_trimer_30A_vinagpu_medium_report_native_5runs_20260607",
    },
    {
        "label": "30A CPU Vina report-native",
        "engine": "vina",
        "redock_root": "dopamine_redock_30A_vina_report_native_5runs_20260607",
        "trimer_root": "dopamine_trimer_30A_vina_report_native_5runs_20260607",
    },
]

def request(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"} if payload is not None else {},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        return json.load(response)

def get(path: str) -> dict[str, Any]:
    return request("GET", path)

def post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    return request("POST", path, payload)

def result_count(root_name: str) -> int:
    root = DOCK / root_name
    return len(list(root.rglob("results.json"))) if root.exists() else 0

def wait_idle(label: str) -> None:
    last = None
    while True:
        env = get("/api/control/run/status")
        data = env.get("data") or {}
        status = data.get("status")
        msg = (status, data.get("completed_runs"), data.get("total_runs"), data.get("returncode"))
        if msg != last:
            print(f"{label}: status={msg[0]} completed={msg[1]}/{msg[2]} returncode={msg[3]}", flush=True)
            last = msg
        if status != "running":
            if data.get("returncode") not in (None, 0):
                raise SystemExit(f"{label} failed: {json.dumps(data, ensure_ascii=False)}")
            return
        time.sleep(20)

def cfg(engine: str) -> dict[str, Any]:
    return {**COMMON, "docking_engine": engine}

def start_redock(root_name: str, engine: str) -> None:
    payload = {
        "mode": "Redocking",
        "receptors": RECEPTORS,
        "chains": CHAINS,
        "selection_map": {
            pdb: {"chain": CHAINS[pdb], "ligand_resname": NATIVE[pdb], "ligand_resnames": [NATIVE[pdb]], "flex_residues": []}
            for pdb in RECEPTORS
        },
        "grid_data": GRID_30A_BASE,
        "docking_config": cfg(engine),
        "run_count": 5,
        "padding": 5,
        "out_root_path": "data/dock",
        "out_root_name": root_name,
        "replace_queue": True,
        "reset_queue": True,
        "activate_ligands": False,
    }
    print(post("/api/control/queue/prepare", payload).get("message"), flush=True)
    print(post("/api/control/run/start", {"test_mode": False}).get("message"), flush=True)

def start_trimer(root_name: str, engine: str) -> None:
    payload = {
        "mode": "Docking",
        "receptors": RECEPTORS,
        "chains": CHAINS,
        "ligands": LIGANDS,
        "selection_map": {
            pdb: {"chain": CHAINS[pdb], "ligand_resname": "all_set", "ligand_resnames": [], "flex_residues": []}
            for pdb in RECEPTORS
        },
        "grid_data": GRID_30A_BASE,
        "docking_config": cfg(engine),
        "run_count": 5,
        "padding": 5,
        "out_root_path": "data/dock",
        "out_root_name": root_name,
        "replace_queue": True,
        "reset_queue": True,
        "activate_ligands": True,
    }
    print(post("/api/control/queue/prepare", payload).get("message"), flush=True)
    print(post("/api/control/run/start", {"test_mode": False}).get("message"), flush=True)

wait_idle("precheck")
for item in SETS:
    print(f"=== {item['label']} ===", flush=True)
    if result_count(item["redock_root"]) < 25:
        print(f"redock before: {result_count(item['redock_root'])}/25", flush=True)
        start_redock(item["redock_root"], item["engine"])
        wait_idle(item["redock_root"])
    print(f"redock final: {result_count(item['redock_root'])}/25", flush=True)
    if result_count(item["trimer_root"]) < 100:
        print(f"trimer before: {result_count(item['trimer_root'])}/100", flush=True)
        start_trimer(item["trimer_root"], item["engine"])
        wait_idle(item["trimer_root"])
    print(f"trimer final: {result_count(item['trimer_root'])}/100", flush=True)

print("DONE: report-native 30A Vina-GPU and 30A CPU Vina runs finished.", flush=True)
PY
