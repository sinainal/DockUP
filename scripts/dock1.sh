#!/usr/bin/env bash
set -euo pipefail

# Force POSIX decimal separator so printf and arithmetic accept dot decimals on locales using commas
export LC_NUMERIC=C

# ── Environment Discovery ───────────────────────────────────────────────────
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root_dir=$(dirname "$script_dir")

if [ -f "$root_dir/.env" ]; then
  source "$root_dir/.env"
fi

# Determine the best Python to use
DOCKUP_PYTHON="${DOCKUP_PYTHON:-python3}"
DOCKUP_PYMOL_PYTHON="${DOCKUP_PYMOL_PYTHON:-$DOCKUP_PYTHON}"
DOCKUP_VINA="${DOCKUP_VINA:-}"
if [ -x "$DOCKUP_PYTHON" ]; then
  DOCKUP_BIN_DIR=$(dirname "$DOCKUP_PYTHON")
  export PATH="$DOCKUP_BIN_DIR:$PATH"
fi
export PYTHONNOUSERSITE=1

if [ -n "$DOCKUP_VINA" ] && [ -x "$DOCKUP_VINA" ]; then
  VINA_BIN="$DOCKUP_VINA"
elif command -v vina >/dev/null 2>&1; then
  VINA_BIN=$(command -v vina)
else
  echo "Error: vina CLI not found. Re-run ./setup.sh to install AutoDock Vina." >&2
  exit 2
fi

[ $# -ge 3 ] || { echo "Usage: $0 <PDBID> <CHAIN> <LIGAND_RESNAME> [--lig_spec path_to_sdf] [--pdb_file path_to_receptor.pdb] [--grid_pad value|x,y,z] [--grid_file path] [--pdb2pqr_ph value] [--pdb2pqr_ff name] [--pdb2pqr_ffout name] [--pdb2pqr_nodebump 1|0] [--pdb2pqr_keep_chain 1|0] [--mkrec_allow_bad_res 1|0] [--mkrec_default_altloc A] [--vina_exhaustiveness N] [--vina_num_modes N] [--vina_energy_range E] [--vina_cpu N] [--vina_seed N]"; exit 1; }
PDB=$1
CHAIN=$2
LIGAND_RESNAME=$3

# optional flag: --lig_spec /path/to/ligand.sdf (after positional args)
LIG_SPEC=""
PDB_FILE=""
GRID_PADDING=""
GRID_FILE=""
PDB2PQR_PH="7.4"
PDB2PQR_FF="AMBER"
PDB2PQR_FFOUT="AMBER"
PDB2PQR_NODEBUMP="1"
PDB2PQR_KEEP_CHAIN="1"
MKREC_ALLOW_BAD_RES="1"
MKREC_DEFAULT_ALTLOC="A"
VINA_EXHAUSTIVENESS="32"
VINA_NUM_MODES=""
VINA_ENERGY_RANGE=""
VINA_CPU=""
VINA_SEED=""
if [ "$#" -gt 3 ]; then
  # shift first three positional args then parse
  shift 3
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --lig_spec)
        LIG_SPEC="$2"; shift 2;;
      --pdb_file)
        PDB_FILE="$2"; shift 2;;
      --grid_pad)
        if [ "$#" -lt 2 ]; then
          echo "Error: --grid_pad requires a value" >&2
          exit 1
        fi
        GRID_PADDING="$2"; shift 2;;
      --grid_file)
        GRID_FILE="$2"; shift 2;;
      --pdb2pqr_ph)
        PDB2PQR_PH="$2"; shift 2;;
      --pdb2pqr_ff)
        PDB2PQR_FF="$2"; shift 2;;
      --pdb2pqr_ffout)
        PDB2PQR_FFOUT="$2"; shift 2;;
      --pdb2pqr_nodebump)
        PDB2PQR_NODEBUMP="$2"; shift 2;;
      --pdb2pqr_keep_chain)
        PDB2PQR_KEEP_CHAIN="$2"; shift 2;;
      --mkrec_allow_bad_res)
        MKREC_ALLOW_BAD_RES="$2"; shift 2;;
      --mkrec_default_altloc)
        MKREC_DEFAULT_ALTLOC="$2"; shift 2;;
      --vina_exhaustiveness)
        VINA_EXHAUSTIVENESS="$2"; shift 2;;
      --vina_num_modes)
        VINA_NUM_MODES="$2"; shift 2;;
      --vina_energy_range)
        VINA_ENERGY_RANGE="$2"; shift 2;;
      --vina_cpu)
        VINA_CPU="$2"; shift 2;;
      --vina_seed)
        VINA_SEED="$2"; shift 2;;
      *) echo "Warning: unknown argument $1"; shift;;
    esac
  done
fi
GRIDBOX=${PDB}_gridbox.txt
OUTDIR=${PDB}_results            # tüm çıktılar buraya taşınacak

to_bool01() {
  local raw="${1:-}"
  local fallback="${2:-1}"
  case "$(echo "$raw" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on) echo "1" ;;
    0|false|no|off) echo "0" ;;
    "") echo "$fallback" ;;
    *) echo "$fallback" ;;
  esac
}

PDB2PQR_NODEBUMP=$(to_bool01 "$PDB2PQR_NODEBUMP" "1")
PDB2PQR_KEEP_CHAIN=$(to_bool01 "$PDB2PQR_KEEP_CHAIN" "1")
MKREC_ALLOW_BAD_RES=$(to_bool01 "$MKREC_ALLOW_BAD_RES" "1")

if ! [[ "$VINA_EXHAUSTIVENESS" =~ ^[0-9]+$ ]] || [ "$VINA_EXHAUSTIVENESS" -lt 1 ]; then
  echo "Error: --vina_exhaustiveness must be a positive integer" >&2
  exit 2
fi
if [ -n "$VINA_NUM_MODES" ] && { ! [[ "$VINA_NUM_MODES" =~ ^[0-9]+$ ]] || [ "$VINA_NUM_MODES" -lt 1 ]; }; then
  echo "Error: --vina_num_modes must be a positive integer" >&2
  exit 2
fi
if [ -n "$VINA_CPU" ] && { ! [[ "$VINA_CPU" =~ ^[0-9]+$ ]] || [ "$VINA_CPU" -lt 1 ]; }; then
  echo "Error: --vina_cpu must be a positive integer" >&2
  exit 2
fi
if [ -n "$VINA_SEED" ] && ! [[ "$VINA_SEED" =~ ^[0-9]+$ ]]; then
  echo "Error: --vina_seed must be an integer >= 0" >&2
  exit 2
fi

normalize_optional_path() {
  local path="$1"
  if [ -z "$path" ]; then
    return
  fi
  if [[ "$path" == /* ]]; then
    printf '%s' "$path"
  else
    "$DOCKUP_PYTHON" - "$PWD" "$path" <<'PY'
import os, sys
cwd, rel = sys.argv[1], sys.argv[2]
print(os.path.abspath(os.path.join(cwd, rel)))
PY
  fi
}

LIG_SPEC=$(normalize_optional_path "$LIG_SPEC")
PDB_FILE=$(normalize_optional_path "$PDB_FILE")
GRID_FILE=$(normalize_optional_path "$GRID_FILE")

if [ -n "$PDB_FILE" ] && [ ! -f "$PDB_FILE" ]; then
  echo "Error: --pdb_file not found: $PDB_FILE" >&2
  exit 2
fi
if [ -n "$GRID_FILE" ] && [ ! -f "$GRID_FILE" ]; then
  echo "Error: --grid_file not found: $GRID_FILE" >&2
  exit 2
fi

# Ensure OpenBabel can find its plugin directory when used as fallback
if [ -z "${BABEL_LIBDIR:-}" ]; then
  if [ -n "${CONDA_PREFIX:-}" ] && [ -d "$CONDA_PREFIX/lib/openbabel/3.1.1" ]; then
    export BABEL_LIBDIR="$CONDA_PREFIX/lib/openbabel/3.1.1"
  elif [ -d /usr/lib/x86_64-linux-gnu/openbabel/3.1.1 ]; then
    export BABEL_LIBDIR=/usr/lib/x86_64-linux-gnu/openbabel/3.1.1
  fi
fi

MK_PREP_REC="mk_prepare_receptor.py"
MK_PREP_LIG="mk_prepare_ligand.py"
if [ -n "${DOCKUP_BIN_DIR:-}" ]; then
  if [ -x "$DOCKUP_BIN_DIR/mk_prepare_receptor.py" ]; then
    MK_PREP_REC="$DOCKUP_BIN_DIR/mk_prepare_receptor.py"
  fi
  if [ -x "$DOCKUP_BIN_DIR/mk_prepare_ligand.py" ]; then
    MK_PREP_LIG="$DOCKUP_BIN_DIR/mk_prepare_ligand.py"
  fi
fi

#──────────────── 1. PyMOL – chain/ligand split (fallback without PyMOL) ─────────────────
if "$DOCKUP_PYMOL_PYTHON" - "$PDB" "$CHAIN" "$LIGAND_RESNAME" "${PDB_FILE:-}" <<'PY'
import sys
from pathlib import Path

try:
    import pymol2
except ImportError:
    raise SystemExit(90)

pid = sys.argv[1]
chain = sys.argv[2]
lig_resname = sys.argv[3]
pdb_path = sys.argv[4]
object_name = 'receptor_obj'

use_chain = chain.lower() != "all"

with pymol2.PyMOL() as pm:
    if pdb_path:
        path = Path(pdb_path)
        if not path.is_file():
            raise SystemExit(f"Error: provided PDB file not found: {path}")
        pm.cmd.load(str(path), object_name)
        print(f"Using local PDB file: {path}")
    else:
        pm.cmd.fetch(pid, name=object_name)
    pm.cmd.remove('solvent')
    if use_chain:
        pm.cmd.remove(f'not chain {chain}')
    pm.cmd.remove('inorganic')
    if use_chain:
        pm.cmd.select('lig', f'resn {lig_resname} and chain {chain}')
    else:
        pm.cmd.select('lig', f'resn {lig_resname}')
    if pm.cmd.count_atoms('lig') == 0:
        if use_chain:
            pm.cmd.select('lig', f'organic and chain {chain}')
        else:
            pm.cmd.select('lig', 'organic')
    pm.cmd.save(f'{pid}_ligand.sdf', 'lig')
    pm.cmd.remove('lig')
    pm.cmd.save(f'{pid}_rec_raw.pdb', 'all')
PY
then
  :
else
  pymol_rc=$?
  if [ "$pymol_rc" -ne 90 ]; then
    exit "$pymol_rc"
  fi
  echo "Warning: pymol2 not available. Falling back to plain PDB parsing." >&2
  "$DOCKUP_PYTHON" - "$PDB" "$CHAIN" "$LIGAND_RESNAME" "${PDB_FILE:-}" "${LIG_SPEC:-}" <<'PY'
import sys
import urllib.request
from pathlib import Path

pid = sys.argv[1]
chain = sys.argv[2]
lig_resname = (sys.argv[3] or "").strip()
pdb_path = (sys.argv[4] or "").strip()
lig_spec = (sys.argv[5] or "").strip()
use_chain = chain.lower() != "all"
water_res = {"HOH", "WAT", "DOD"}


def _load_pdb_text() -> str:
    if pdb_path:
        path = Path(pdb_path)
        if not path.is_file():
            raise SystemExit(f"Error: provided PDB file not found: {path}")
        return path.read_text(encoding="utf-8", errors="ignore")
    url = f"https://files.rcsb.org/download/{pid.upper()}.pdb"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="ignore")


pdb_text = _load_pdb_text()
selected_lines: list[str] = []
for raw in pdb_text.splitlines():
    if not (raw.startswith("ATOM") or raw.startswith("HETATM")):
        continue
    atom_chain = (raw[21].strip() or "_") if len(raw) > 21 else "_"
    if use_chain and atom_chain != chain:
        continue
    selected_lines.append(raw)

if not selected_lines:
    raise SystemExit(f"Error: no atoms found for {pid} chain={chain}")

receptor_lines: list[str] = []
ligand_lines: list[str] = []
for line in selected_lines:
    resname = line[17:20].strip() if len(line) >= 20 else ""
    if resname in water_res:
        continue
    if line.startswith("HETATM"):
        if lig_resname and resname == lig_resname:
            ligand_lines.append(line)
        continue
    receptor_lines.append(line)

if not receptor_lines:
    raise SystemExit("Error: receptor extraction failed in fallback mode.")

Path(f"{pid}_rec_raw.pdb").write_text("\n".join(receptor_lines) + "\nEND\n", encoding="utf-8")
print(f"Fallback receptor saved: {pid}_rec_raw.pdb")

if lig_spec:
    # External ligand spec will be copied by shell code right after this block.
    raise SystemExit(0)

if not ligand_lines:
    ligand_lines = [
        line
        for line in selected_lines
        if line.startswith("HETATM")
        and (line[17:20].strip() if len(line) >= 20 else "") not in water_res
    ]
if not ligand_lines:
    raise SystemExit("Error: cannot extract native ligand without PyMOL and no --lig_spec.")

ligand_pdb = Path(f"{pid}_ligand_from_pdb.pdb")
ligand_pdb.write_text("\n".join(ligand_lines) + "\nEND\n", encoding="utf-8")

try:
    from rdkit import Chem

    mol = Chem.MolFromPDBFile(str(ligand_pdb), removeHs=False, sanitize=False)
    if mol is None:
        raise RuntimeError("RDKit could not parse extracted ligand PDB block.")
    Chem.MolToMolFile(mol, f"{pid}_ligand.sdf")
except Exception as exc:
    raise SystemExit(f"Error: ligand conversion fallback failed: {exc}")

print(f"Fallback ligand saved: {pid}_ligand.sdf")
PY
fi
# If user provided external ligand spec, copy it to expected filename
if [ -n "$LIG_SPEC" ]; then
  if [ -f "$LIG_SPEC" ]; then
    cp -f "$LIG_SPEC" "${PDB}_ligand.sdf"
    echo "Using external ligand spec: $LIG_SPEC -> ${PDB}_ligand.sdf"
  else
    echo "Error: --lig_spec file not found: $LIG_SPEC"; exit 2
  fi
fi

#──────────────── 2. Receptor → PQR (pdb2pqr) ───────────────────────
pdb2pqr_cmd=(pdb2pqr30 --ff "$PDB2PQR_FF" --ffout "$PDB2PQR_FFOUT" --with-ph "$PDB2PQR_PH")
if [ "$PDB2PQR_NODEBUMP" = "1" ]; then
  pdb2pqr_cmd+=(--nodebump)
fi
if [ "$PDB2PQR_KEEP_CHAIN" = "1" ]; then
  pdb2pqr_cmd+=(--keep-chain)
fi
pdb2pqr_cmd+=("${PDB}_rec_raw.pdb" "${PDB}_rec.pqr")
if ! "${pdb2pqr_cmd[@]}"; then
  echo "Warning: pdb2pqr30 failed; continuing with raw PDB as fallback" >&2
  cp "${PDB}_rec_raw.pdb" "${PDB}_rec.pqr"
fi


#──────────────── 3. PQR → PDB (OpenBabel) opsiyonel ──────────────────────────
#obabel -ipqr "${PDB}_rec.pqr" -opdb -O "${PDB}_rec_pre.pdb" >/dev/null

#──────────────── 4. GridBox (sabit dosya veya autogrid) ──────────────────
if [ -n "$GRID_FILE" ]; then
  cp -f "$GRID_FILE" "$GRIDBOX"
  # also lowercase copy for compatibility
  GRIDBOX_LOWER=$(echo "$GRIDBOX" | tr '[:upper:]' '[:lower:]')
  cp -f "$GRID_FILE" "$GRIDBOX_LOWER"
  echo "--- $GRIDBOX (provided) ---"
  cat "$GRIDBOX"
else
  autogrid_cmd=("$DOCKUP_PYTHON" autogrid.py -p "$PDB" -l "$LIGAND_RESNAME" -c "$CHAIN")
  if [ -n "$PDB_FILE" ]; then
    autogrid_cmd+=(-r "$PDB_FILE")
  fi
  "${autogrid_cmd[@]}"
  [ -f "$GRIDBOX" ] || { echo "✗ $GRIDBOX not created"; exit 2; }
  echo "--- $GRIDBOX ---"
  cat "$GRIDBOX"
fi
if [ -n "$GRID_PADDING" ]; then
  if ! PAD_VALUES=$("$DOCKUP_PYTHON" - "$GRID_PADDING" <<'PY'
import sys

raw = sys.argv[1].strip()
if not raw:
    raise SystemExit(1)

raw = raw.replace(',', ' ')
parts = [p for p in raw.split() if p]
if not parts:
    raise SystemExit(1)

if len(parts) == 1:
    parts = parts * 3
elif len(parts) == 2:
    parts.append(parts[-1])
elif len(parts) > 3:
    parts = parts[:3]

try:
    vals = [float(p) for p in parts]
except ValueError:
    raise SystemExit(1)

print(*vals)
PY
  ); then
    echo "Error: invalid --grid_pad value '$GRID_PADDING'" >&2
    exit 2
  fi
  read PAD_X PAD_Y PAD_Z <<<"$PAD_VALUES"

  if ! UPDATED_SIZES=$("$DOCKUP_PYTHON" - "$GRIDBOX" "$PAD_X" "$PAD_Y" "$PAD_Z" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
pad_x, pad_y, pad_z = map(float, sys.argv[2:5])
lines = path.read_text().splitlines()

def adjust(lines, key, delta):
    for idx, line in enumerate(lines):
        if line.strip().startswith(f"{key}"):
            parts = line.split('=')
            if len(parts) != 2:
                raise ValueError(f"Cannot parse {key}")
            current = float(parts[1].strip())
            updated = current + delta
            lines[idx] = f"{key} = {updated:.3f}"
            return updated
    raise ValueError(f"{key} not found in grid file")

sx = adjust(lines, 'size_x', pad_x)
sy = adjust(lines, 'size_y', pad_y)
sz = adjust(lines, 'size_z', pad_z)

path.write_text('\n'.join(lines) + '\n')
print(sx, sy, sz)
PY
  ); then
    echo "Error: failed to apply grid padding" >&2
    exit 2
  fi
  read NEW_SX NEW_SY NEW_SZ <<<"$UPDATED_SIZES"
  printf 'Applied grid padding (Angstrom): x=%.3f y=%.3f z=%.3f\n' "$PAD_X" "$PAD_Y" "$PAD_Z"
  printf 'Updated grid box size (Angstrom): x=%.3f y=%.3f z=%.3f\n' "$NEW_SX" "$NEW_SY" "$NEW_SZ"
  echo "--- $GRIDBOX (padded) ---"
  cat "$GRIDBOX"
fi
read CX CY CZ <<<$(awk -F '=' '/center_[xyz]/{gsub(/[[:space:]]/,"",$2);printf "%s ",$2}' "$GRIDBOX")
read SX SY SZ <<<$(awk -F '=' '/size_[xyz]/{gsub(/[[:space:]]/,"",$2);printf "%s ",$2}'  "$GRIDBOX")

#──────────────── 5. Receptor PDBQT (tek adım) ──────────────────────
mk_prepare_rec_cmd=(
  "$MK_PREP_REC"
  --read_pdb "${PDB}_rec_raw.pdb"
  --write_pdbqt "${PDB}_receptor.pdbqt"
  --box_center "$CX" "$CY" "$CZ"
  --box_size "$SX" "$SY" "$SZ"
)
if [ "$MKREC_ALLOW_BAD_RES" = "1" ]; then
  mk_prepare_rec_cmd+=(--allow_bad_res)
fi
if [ -n "$MKREC_DEFAULT_ALTLOC" ]; then
  mk_prepare_rec_cmd+=(--default_altloc "$MKREC_DEFAULT_ALTLOC")
fi
if ! "${mk_prepare_rec_cmd[@]}"; then
  echo "Warning: mk_prepare_receptor.py failed; attempting OpenBabel fallback" >&2
  if command -v obabel >/dev/null 2>&1; then
    if ! obabel -ipdb "${PDB}_rec_raw.pdb" -opdbqt -O "${PDB}_receptor.pdbqt" --partialcharge gasteiger -p "$PDB2PQR_PH"; then
      echo "Error: OpenBabel receptor preparation fallback failed" >&2
      exit 1
    fi
  else
    echo "Error: mk_prepare_receptor.py failed and obabel not available" >&2
    exit 1
  fi
fi

#──────────────── 6. Ligand: RDKit + mk_prepare_ligand.py ───────────
"$DOCKUP_PYTHON" - <<PY
import sys
from rdkit import Chem
from rdkit.Chem import AllChem, rdPartialCharges, rdForceFieldHelpers

mol = Chem.MolFromMolFile("${PDB}_ligand.sdf", removeHs=False)
if mol is None:
    print('Error: RDKit failed to read "${PDB}_ligand.sdf"')
    sys.exit(1)
try:
    mol = Chem.AddHs(mol, addCoords=True)
except Exception as e:
    print(f'Warning: AddHs failed: {e}')

try:
    rdPartialCharges.ComputeGasteigerCharges(mol)
except Exception as e:
    print(f'Warning: ComputeGasteigerCharges failed: {e}')

# Try UFF, then MMFF; if both fail, continue without optimization
optimized = False
try:
    rdForceFieldHelpers.UFFOptimizeMolecule(mol)
    optimized = True
except Exception as e1:
    try:
        if AllChem.MMFFHasAllMoleculeParams(mol):
            AllChem.MMFFOptimizeMolecule(mol)
            optimized = True
        else:
            print('MMFF parameters not available; skipping MMFF optimization')
    except Exception as e2:
        print(f'Warning: optimization failed (UFF error: {e1}; MMFF error: {e2})')

Chem.MolToMolFile(mol, "${PDB}_ligand_fixed.sdf")
print(f'RDKit ligand processing done (optimized={optimized})')
PY

if ! "$MK_PREP_LIG" -i "${PDB}_ligand_fixed.sdf" -o "${PDB}_ligand.pdbqt"; then
  echo "Warning: mk_prepare_ligand.py failed; attempting OpenBabel fallback" >&2
  if command -v obabel >/dev/null 2>&1; then
    if ! obabel -isdf "${PDB}_ligand_fixed.sdf" -opdbqt -O "${PDB}_ligand.pdbqt" --partialcharge gasteiger; then
      echo "Error: OpenBabel ligand preparation fallback failed" >&2
      exit 1
    fi
  else
    echo "Error: mk_prepare_ligand.py failed and obabel not available" >&2
    exit 1
  fi
fi

#──────────────── 7. Docking – AutoDock Vina ────────────────────────
vina_cmd=(
  "$VINA_BIN"
  --receptor "${PDB}_receptor.pdbqt"
  --ligand "${PDB}_ligand.pdbqt"
  --config "$GRIDBOX"
  --exhaustiveness "$VINA_EXHAUSTIVENESS"
  --out "${PDB}_out_vina.pdbqt"
)
if [ -n "$VINA_NUM_MODES" ]; then
  vina_cmd+=(--num_modes "$VINA_NUM_MODES")
fi
if [ -n "$VINA_ENERGY_RANGE" ]; then
  vina_cmd+=(--energy_range "$VINA_ENERGY_RANGE")
fi
if [ -n "$VINA_CPU" ]; then
  vina_cmd+=(--cpu "$VINA_CPU")
fi
if [ -n "$VINA_SEED" ]; then
  vina_cmd+=(--seed "$VINA_SEED")
fi
"${vina_cmd[@]}"

#──────────────── 8. Çıktıları klasöre taşı ─────────────────────────
mkdir -p "$OUTDIR"
mv "${PDB}_receptor.pdbqt"      "$OUTDIR/"
mv "${PDB}_ligand.pdbqt"   "$OUTDIR/"
mv "${PDB}_out_vina.pdbqt" "$OUTDIR/"
mv "$GRIDBOX"              "$OUTDIR/"

echo "✅ Docking tamamlandı → $OUTDIR/"
exit 0
