#!/usr/bin/env bash
set -euo pipefail
[ $# -ge 3 ] || { echo "Usage: $0 <PDBID> <CHAIN> <LIGAND_RESNAME> [--lig_spec /path/to_ligand.sdf] [--pdb_file /path/to/receptor.pdb] [--grid_pad value|x,y,z] [--grid_file path_to_gridbox.txt] [--run_id N] [--out_root /path/to/output_root] [--pdb2pqr_ph value] [--pdb2pqr_ff name] [--pdb2pqr_ffout name] [--pdb2pqr_nodebump 1|0] [--pdb2pqr_keep_chain 1|0] [--mkrec_allow_bad_res 1|0] [--mkrec_default_altloc A] [--vina_exhaustiveness N] [--vina_num_modes N] [--vina_energy_range E] [--vina_cpu N] [--vina_seed N]"; exit 1; }

# ── Environment Discovery ───────────────────────────────────────────────────
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root_dir=$(dirname "$script_dir")

if [ -f "$root_dir/.env" ]; then
  source "$root_dir/.env"
fi

# Determine the best Python to use
DOCKUP_PYTHON="${DOCKUP_PYTHON:-python3}"
DOCKUP_PYMOL_PYTHON="${DOCKUP_PYMOL_PYTHON:-$DOCKUP_PYTHON}"
export DOCKUP_PYTHON
export DOCKUP_PYMOL_PYTHON

# Inputs
PDB=$1
CHAIN=$2
LIGAND=$3
RUN_ID="1"
OUT_ROOT=""
OUTDIR=""
LOG=""
BEST_AFF_VAL=""
RMSD_VAL=""

# Normalize optional path arguments so they remain valid after switching to tmpdir
shifted_args=("$@")

normalize_dir() {
  local path="$1"
  if [ -z "$path" ]; then
    return
  fi
  if [[ "$path" == /* ]]; then
    printf '%s' "$path"
  else
    "$DOCKUP_PYTHON" - "$script_dir" "$path" <<'PY'
import os, sys
base, rel = sys.argv[1], sys.argv[2]
print(os.path.abspath(os.path.join(base, rel)))
PY
  fi
}
normalize_path() {
  local path="$1"
  if [ -z "$path" ]; then
    return
  fi
  if [[ "$path" == /* ]]; then
    printf '%s' "$path"
  else
    "$DOCKUP_PYTHON" - "$script_dir" "$path" <<'PY'
import os, sys
base, rel = sys.argv[1], sys.argv[2]
print(os.path.abspath(os.path.join(base, rel)))
PY
  fi
}

for idx in "${!shifted_args[@]}"; do
  case "${shifted_args[$idx]}" in
    --lig_spec|--pdb_file|--grid_file)
      next=$((idx + 1))
      if [ "$next" -lt "${#shifted_args[@]}" ]; then
        resolved=$(normalize_path "${shifted_args[$next]}")
        shifted_args[$next]="$resolved"
      fi
      ;;
  esac
done

# Extract internal options (not forwarded to dock1.sh)
dock_args=()
LIG_SPEC_PATH=""
idx=0
while [ "$idx" -lt "${#shifted_args[@]}" ]; do
  arg="${shifted_args[$idx]}"
  case "$arg" in
    --run_id|--run-id)
      RUN_ID="${shifted_args[$((idx + 1))]:-1}"
      idx=$((idx + 2))
      continue
      ;;
    --out_root|--out-root)
      OUT_ROOT="${shifted_args[$((idx + 1))]:-}"
      idx=$((idx + 2))
      continue
      ;;
    --lig_spec|--lig-spec)
      LIG_SPEC_PATH="${shifted_args[$((idx + 1))]:-}"
      ;;
  esac
  dock_args+=("$arg")
  idx=$((idx + 1))
done

# Determine ligand name for folder
# If --lig_spec was provided, use its filename (without extension)
# Otherwise use LIGAND (3rd argument, typically native ligand resname)
if [ -n "$LIG_SPEC_PATH" ]; then
  # Extract filename without path and extension
  LIGAND_FILENAME=$(basename "$LIG_SPEC_PATH")
  LIGAND_SUFFIX="${LIGAND_FILENAME%.sdf}"
  LIGAND_SUFFIX="${LIGAND_SUFFIX%.mol2}"
  LIGAND_SUFFIX="${LIGAND_SUFFIX%.pdb}"
  # Convert to uppercase-friendly name (e.g., styrene-dimer -> Styrene-dimer)
  LIGAND_SUFFIX="${LIGAND_SUFFIX// /_}"
  LIGAND_SUFFIX="${LIGAND_SUFFIX//-/_}"
else
  # Use 3rd argument (redocking with native ligand)
  LIGAND_SUFFIX="${LIGAND%.sdf}"
  LIGAND_SUFFIX="${LIGAND_SUFFIX// /_}"
  LIGAND_SUFFIX="${LIGAND_SUFFIX:-Native}"
fi

if [ -n "$OUT_ROOT" ]; then
  OUT_ROOT=$(normalize_dir "$OUT_ROOT")
  OUTDIR="${OUT_ROOT}/${PDB}/${LIGAND_SUFFIX}/run${RUN_ID}"
else
  OUTDIR="${script_dir}/${PDB}/${LIGAND_SUFFIX}/run${RUN_ID}"
fi
LOG="$OUTDIR/${PDB}_${LIGAND_SUFFIX}_run${RUN_ID}.log"

mkdir -p "$OUTDIR"
# create tmp working dir under OUTDIR so nothing is written to project root
tmpdir=$(mktemp -d "$OUTDIR/.run_tmp.XXXXXX")
trap 'rm -rf "$tmpdir"' EXIT

# copy minimal helpers so dock1.sh can find them when run from tmpdir
cp -a "$script_dir/autogrid.py" "$tmpdir/" 2>/dev/null || true
cp -a "$script_dir/boron-silicon-atom_par.dat" "$tmpdir/" 2>/dev/null || true
cp -a "$script_dir/dock1.sh" "$tmpdir/" 2>/dev/null || true
chmod +x "$tmpdir/dock1.sh" 2>/dev/null || true

# run dock1.sh with CWD=tmpdir, but write log into OUTDIR
pushd "$tmpdir" >/dev/null
bash "$tmpdir/dock1.sh" "${dock_args[@]}" 2>&1 | tee "$LOG"
popd >/dev/null

# move produced files into OUTDIR (merge if already exist)
shopt -s dotglob
for f in "$tmpdir"/*; do
  [ -e "$f" ] || continue
  base=$(basename "$f")
  if [ -d "$f" ]; then
    mkdir -p "$OUTDIR/$base"
    shopt -s dotglob
    for g in "$f"/*; do
      [ -e "$g" ] || continue
      mv -f "$g" "$OUTDIR/$base/"
    done
    shopt -u dotglob
    rmdir "$f" 2>/dev/null || true
  else
    mv -f "$f" "$OUTDIR/"
  fi
done
shopt -u dotglob

# locate vina output once (used for pose extraction + RMSD)
vina_out=$(find "$OUTDIR" -maxdepth 2 -name "${PDB}_out_vina.pdbqt" -print -quit || true)

# prepare docked pose PDB (for PLIP/interaction map)
pose_pdb="$OUTDIR/${PDB}_pose.pdb"
if [ -n "$vina_out" ] && [ -f "$vina_out" ]; then
  "$DOCKUP_PYTHON" - "$vina_out" "$pose_pdb" <<'PY' >/dev/null 2>&1 || true
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])

atoms = []
with src.open() as handle:
    for line in handle:
        if line.startswith('MODEL'):
            continue
        if line.startswith('ENDMDL'):
            break
        if line.startswith(('ATOM', 'HETATM')):
            atoms.append(line)

if not atoms:
    raise SystemExit(0)

def element_from_line(line: str) -> str:
    elem = line[76:78].strip()
    if elem:
        return elem
    name = line[12:16].strip()
    return (name[0] if name else "C").upper()

out_lines = []
serial = 1
for line in atoms:
    name = line[12:16].strip()
    x = float(line[30:38])
    y = float(line[38:46])
    z = float(line[46:54])
    elem = element_from_line(line)
    out_lines.append(
        f"HETATM{serial:5d} {name:<4} UNL L   1    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00           {elem:>2}"
    )
    serial += 1

dst.write_text("\n".join(out_lines) + "\nEND\n")
PY
fi

# build receptor+ligand complex for PLIP
complex_pdb="$OUTDIR/${PDB}_complex.pdb"
if [ -f "$OUTDIR/${PDB}_rec_raw.pdb" ] && [ -f "$pose_pdb" ]; then
  "$DOCKUP_PYTHON" - "$OUTDIR/${PDB}_rec_raw.pdb" "$pose_pdb" "$complex_pdb" <<'PY' >/dev/null 2>&1 || true
import sys
from pathlib import Path

rec = Path(sys.argv[1])
lig = Path(sys.argv[2])
out = Path(sys.argv[3])

def read_atoms(path):
    lines = []
    with path.open() as handle:
        for line in handle:
            if line.startswith(("ATOM", "HETATM")):
                lines.append(line.rstrip())
    return lines

rec_lines = read_atoms(rec)
lig_lines = read_atoms(lig)
if not rec_lines or not lig_lines:
    raise SystemExit(0)

out.write_text("\n".join(rec_lines + lig_lines) + "\nEND\n")
PY
fi

# run PLIP + interaction map generation (best-effort)
if [ -f "$complex_pdb" ]; then
  if command -v plip >/dev/null 2>&1; then
    plip_dir="$OUTDIR/plip"
    mkdir -p "$plip_dir"
    plip -f "$complex_pdb" -o "$plip_dir" -x -q --name report >/dev/null 2>&1 || true
    if [ -f "$plip_dir/report.xml" ]; then
      "$DOCKUP_PYTHON" "$script_dir/build_interaction_map.py" \
        --report "$plip_dir/report.xml" \
        --complex "$complex_pdb" \
        --pose "$pose_pdb" \
        --receptor "$OUTDIR/${PDB}_rec_raw.pdb" \
        --output "$OUTDIR" >/dev/null 2>&1 || true
    fi
  else
    echo "Warning: plip not found; skipping interaction map generation"
  fi
fi

# extract best affinity (mode 1) from the run log
BEST_AFF=""
if [ -f "$LOG" ]; then
  # Find separator line (e.g. -----+------------+----------+----------) and take the first data line after it
  sep_line=$(grep -nE '^[-]+\+[-+]+$' "$LOG" | cut -d: -f1 | head -n1 || true)
  if [ -n "$sep_line" ]; then
    # find first line after separator that starts with an integer (mode number)
    line_no=$(awk -v s="$sep_line" 'NR>s && $0 ~ /^[[:space:]]*[0-9]+[[:space:]]+/ {print NR; exit}' "$LOG" || true)
    if [ -n "$line_no" ]; then
      BEST_AFF=$(sed -n "${line_no}p" "$LOG" | awk '{print $2}' || true)
    fi
  else
    # fallback: look for 'mode' header then mode 1 line
    BEST_AFF=$(awk 'BEGIN{found=0} /^\s*mode\b/ {found=1; next} found && /^\s*1[[:space:]]+/ {print $2; exit}' "$LOG" || true)
  fi
fi

if [ -n "$BEST_AFF" ]; then
  BEST_AFF_VAL="$BEST_AFF"
fi

# If this was a redocking (no external --lig_spec passed), compute RMSD
REDOCK=true
for a in "${shifted_args[@]}"; do
  if [ "$a" = "--lig_spec" ]; then
    REDOCK=false
    break
  fi
done
if [ "$REDOCK" = true ]; then
  # prepare RMSD dir inside OUTDIR
  rmsd_dir="$OUTDIR/rmsd_tmp"
  mkdir -p "$rmsd_dir"
  # convert crystal ligand SDF to ligand.pdb (use fixed if available)
  if [ -f "$OUTDIR/${PDB}_ligand_fixed.sdf" ]; then
    ref_sdf="$OUTDIR/${PDB}_ligand_fixed.sdf"
  elif [ -f "$OUTDIR/${PDB}_ligand.sdf" ]; then
    ref_sdf="$OUTDIR/${PDB}_ligand.sdf"
  else
    ref_sdf=""
  fi
  if [ -n "$ref_sdf" ]; then
    "$DOCKUP_PYTHON" - <<PY > /dev/null 2>&1 || true
from rdkit import Chem
mol = Chem.MolFromMolFile(r"$ref_sdf", removeHs=False)
if mol is None:
    raise SystemExit(1)
Chem.MolToPDBFile(mol, r"$rmsd_dir/ligand.pdb")
PY
  fi

  # locate vina output (typically inside ${PDB}_results/) and convert first model to PDB
  if [ -n "$vina_out" ] && [ -f "$vina_out" ]; then
    "$DOCKUP_PYTHON" - "$vina_out" "$rmsd_dir/docked.pdb" <<'PY' >/dev/null 2>&1 || true
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])

inside = False
atom_lines = []
with src.open() as handle:
    for line in handle:
        if line.startswith('MODEL'):
            if inside:
                break
            inside = True
            continue
        if not inside:
            continue
        if line.startswith('ENDMDL'):
            break
        if line.startswith(('ATOM', 'HETATM')):
            atom_lines.append(line[:66] + '\n')

if atom_lines:
    dst.write_text(''.join(atom_lines) + 'END\n')
PY
  fi

  # run rmsd calculation if files exist
  if [ -f "$rmsd_dir/ligand.pdb" ] && [ -f "$rmsd_dir/docked.pdb" ]; then
    # copy utility script rmsd_rdkit.py into rmsd_dir
    cp -a "$script_dir/rmsd_rdkit.py" "$rmsd_dir/" 2>/dev/null || true
    cp -a "$script_dir/CalcLigRMSD.py" "$rmsd_dir/" 2>/dev/null || true
    pushd "$rmsd_dir" >/dev/null
    rmsd_val=""
    # ensure ligand.pdb and docked.pdb exist
    if [ -f ligand.pdb ] && [ -f docked.pdb ]; then
      "$DOCKUP_PYTHON" rmsd_rdkit.py >/dev/null 2>&1 || true
    fi
    # read rmsd_results.txt
    if [ -f rmsd_results.txt ]; then
      rmsd_val=$(awk 'NR==2{print $2}' rmsd_results.txt || true)
    fi
    popd >/dev/null
    if [ -n "$rmsd_val" ]; then
      RMSD_VAL="$rmsd_val"
    fi
  fi
  # create PyMOL scene with receptor, reference ligand, and docked pose
  if [ -f "$OUTDIR/${PDB}_rec_raw.pdb" ] && [ -f "$rmsd_dir/ligand.pdb" ] && [ -f "$rmsd_dir/docked.pdb" ]; then
    pse_dir="$OUTDIR/${PDB}_results"
    mkdir -p "$pse_dir"
    PSE_REC="$OUTDIR/${PDB}_rec_raw.pdb" \
    PSE_REF="$rmsd_dir/ligand.pdb" \
    PSE_DOCK="$rmsd_dir/docked.pdb" \
    PSE_OUT="$pse_dir/${PDB}_poses.pse" \
    "$DOCKUP_PYMOL_PYTHON" - <<'PY' >/dev/null 2>&1 || true
import os
from pathlib import Path

try:
    import pymol2
except ImportError:
    raise SystemExit(0)

rec = Path(os.environ['PSE_REC'])
ref = Path(os.environ['PSE_REF'])
docked = Path(os.environ['PSE_DOCK'])
out_pse = Path(os.environ['PSE_OUT'])

if not (rec.is_file() and ref.is_file() and docked.is_file()):
    raise SystemExit(0)

with pymol2.PyMOL() as pm:
    cmd = pm.cmd
    cmd.load(str(rec), 'receptor')
    cmd.load(str(ref), 'ligand_ref')
    cmd.load(str(docked), 'ligand_dock')
    cmd.hide('everything', 'all')
    cmd.show('cartoon', 'receptor')
    cmd.set_color('receptor_gray', [0.75, 0.75, 0.75])
    cmd.color('receptor_gray', 'receptor')
    cmd.show('sticks', 'ligand_ref or ligand_dock')
    cmd.set_color('ligand_ref_c', [0.0, 0.8, 1.0])
    cmd.set_color('ligand_dock_c', [1.0, 0.6, 0.0])
    cmd.color('ligand_ref_c', 'ligand_ref')
    cmd.color('ligand_dock_c', 'ligand_dock')
    cmd.set('stick_radius', 0.2, 'ligand_ref or ligand_dock')
    cmd.zoom('ligand_ref or ligand_dock')
    cmd.save(str(out_pse))
print('Saved PyMOL scene to', out_pse)
PY
    if [ -f "$pse_dir/${PDB}_poses.pse" ]; then
      echo "Saved PyMOL scene: $pse_dir/${PDB}_poses.pse"
    fi
  fi
  rm -rf "$rmsd_dir"
fi

rm -f "$OUTDIR/stats.json"

BEST_AFF_ENV="$BEST_AFF_VAL" RMSD_ENV="$RMSD_VAL" OUTDIR_ENV="$OUTDIR" "$DOCKUP_PYTHON" - <<'PY'
import json, os

outdir = os.environ['OUTDIR_ENV']
best = os.environ.get('BEST_AFF_ENV') or None
rmsd = os.environ.get('RMSD_ENV') or None

def to_float(value):
    if value in (None, '', 'null'):
        return None
    try:
        return float(value)
    except ValueError:
        return None

result_entry = {
    'best_affinity': to_float(best),
    'rmsd': to_float(rmsd),
}

results = {os.path.basename(outdir): result_entry}

with open(os.path.join(outdir, 'results.json'), 'w') as wf:
    json.dump(results, wf, indent=2)

print('Wrote results.json with', results)
PY

echo "Run complete. Results in: $OUTDIR | Log: $LOG"
if [ -n "$BEST_AFF_VAL" ]; then
  echo "Best affinity (mode 1): $BEST_AFF_VAL"
fi
if [ -n "$RMSD_VAL" ]; then
  echo "Best-pose RMSD: $RMSD_VAL"
fi
