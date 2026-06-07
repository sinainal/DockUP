#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT_DIR"

export PATH="$ROOT_DIR/.venv/bin:$PATH"
export DOCKUP_PYTHON="${DOCKUP_PYTHON:-$ROOT_DIR/.venv/bin/python}"
export DOCKUP_VINA_GPU_21_THREADS="${DOCKUP_VINA_GPU_21_THREADS:-1000}"

BASE_URL="${DOCKUP_BASE_URL:-http://127.0.0.1:8000}"
OUT_BASE="${DOCKUP_LOCAL_DOCS_OUT:-$ROOT_DIR/../local_docs/dopamine/exp_results}"
GRID_DIR="$OUT_BASE/grids_20A_report_native"
mkdir -p "$GRID_DIR"

"$DOCKUP_PYTHON" - "$BASE_URL" <<'PY'
import json
import sys
import time
import urllib.request

base = sys.argv[1].rstrip("/")
while True:
    try:
        with urllib.request.urlopen(base + "/api/control/run/status", timeout=10) as response:
            data = (json.load(response).get("data") or {})
        status = data.get("status")
        print(f"backend run status: {status} {data.get('completed_runs')}/{data.get('total_runs')}", flush=True)
        if status != "running":
            break
    except Exception as exc:
        print(f"backend status check skipped: {exc}", flush=True)
        break
    time.sleep(30)
PY

"$DOCKUP_PYTHON" - "$GRID_DIR" <<'PY'
from pathlib import Path
import sys

grid_dir = Path(sys.argv[1])
grid_dir.mkdir(parents=True, exist_ok=True)
grids = {
    "7X2F": (100.824, 95.249, 71.751),
    "6CM4": (10.579, 5.044, -8.671),
    "3PBL": (0.085, -14.828, 10.432),
    "5WIU": (-17.86, 13.964, -16.379),
    "8IRV": (101.391, 112.741, 84.153),
}
for pdb, (cx, cy, cz) in grids.items():
    (grid_dir / f"{pdb}_20A_gridbox.txt").write_text(
        f"center_x = {cx:.3f}\n"
        f"center_y = {cy:.3f}\n"
        f"center_z = {cz:.3f}\n"
        "size_x = 20.000\n"
        "size_y = 20.000\n"
        "size_z = 20.000\n",
        encoding="utf-8",
    )
PY

run_set() {
  local engine="$1"
  local root_name="$2"
  local out_root="$OUT_BASE/$root_name"
  local prepared_root="$out_root/_prepared"
  mkdir -p "$out_root"

  declare -A chains=( ["7X2F"]="F" ["6CM4"]="A" ["3PBL"]="A" ["5WIU"]="A" ["8IRV"]="R" )
  declare -A ligands=( ["7X2F"]="LDP 504" ["6CM4"]="8NU 2001" ["3PBL"]="ETQ 1200" ["5WIU"]="AQD 1201" ["8IRV"]="R5F 501" )
  local pdbs=( "7X2F" "6CM4" "3PBL" "5WIU" "8IRV" )

  echo "=== $engine -> $out_root ==="
  for pdb in "${pdbs[@]}"; do
    for run_id in 1 2 3 4 5; do
      echo "RUN $engine $pdb ${ligands[$pdb]} run$run_id"
      bash scripts/run1.sh "$pdb" "${chains[$pdb]}" "${ligands[$pdb]}" \
        --run_id "$run_id" \
        --pdb_file "$ROOT_DIR/docking_app/workspace/data/receptor/$pdb.pdb" \
        --grid_file "$GRID_DIR/${pdb}_20A_gridbox.txt" \
        --docking_engine "$engine" \
        --pdb2pqr_ph 7.4 \
        --pdb2pqr_ff AMBER \
        --pdb2pqr_ffout AMBER \
        --pdb2pqr_nodebump 1 \
        --pdb2pqr_keep_chain 1 \
        --mkrec_allow_bad_res 1 \
        --mkrec_default_altloc A \
        --vina_exhaustiveness 32 \
        --vina_gpu_threads 1000 \
        --vina_gpu_box_profile medium \
        --out_root "$out_root" \
        --prepared_root "$prepared_root"
    done
  done
}

run_set "vina" "dopamine_redock_20A_vina_report_native_5runs_20260607"
run_set "vina_gpu_21" "dopamine_redock_20A_vinagpu_medium_report_native_5runs_20260607"

"$DOCKUP_PYTHON" - "$OUT_BASE" <<'PY'
import csv
import json
import statistics
import sys
from pathlib import Path

base = Path(sys.argv[1])
roots = [
    "dopamine_redock_20A_vina_report_native_5runs_20260607",
    "dopamine_redock_20A_vinagpu_medium_report_native_5runs_20260607",
]
rows = []
for root_name in roots:
    root = base / root_name
    for result in sorted(root.rglob("results.json")):
        rel = result.relative_to(root).parts
        if len(rel) < 4:
            continue
        data = json.loads(result.read_text(encoding="utf-8"))
        entry = next(iter(data.values()))
        rows.append({
            "set": root_name,
            "pdb": rel[0],
            "ligand": rel[1],
            "run": rel[2],
            "affinity": entry.get("best_affinity"),
            "rmsd": entry.get("rmsd"),
        })
with (base / "redock_20A_vina_vs_vinagpu_rows.csv").open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, ["set", "pdb", "ligand", "run", "affinity", "rmsd"])
    writer.writeheader()
    writer.writerows(rows)
groups = {}
for row in rows:
    groups.setdefault((row["set"], row["pdb"], row["ligand"]), []).append(row)
summary = []
for (set_name, pdb, ligand), items in sorted(groups.items()):
    aff = [float(item["affinity"]) for item in items if item["affinity"] is not None]
    rmsd = [float(item["rmsd"]) for item in items if item["rmsd"] is not None]
    summary.append({
        "set": set_name,
        "pdb": pdb,
        "ligand": ligand,
        "n": len(items),
        "mean_affinity": statistics.mean(aff) if aff else None,
        "best_affinity": min(aff) if aff else None,
        "mean_rmsd": statistics.mean(rmsd) if rmsd else None,
        "best_rmsd": min(rmsd) if rmsd else None,
        "rmsd_n": len(rmsd),
    })
with (base / "redock_20A_vina_vs_vinagpu_summary.csv").open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, ["set", "pdb", "ligand", "n", "mean_affinity", "best_affinity", "mean_rmsd", "best_rmsd", "rmsd_n"])
    writer.writeheader()
    writer.writerows(summary)
print("Wrote", base / "redock_20A_vina_vs_vinagpu_summary.csv")
PY
