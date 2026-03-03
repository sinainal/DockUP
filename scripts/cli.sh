#!/usr/bin/env bash
# Unified docking CLI (batch + plots). Uses existing run1.sh/dock1.sh + plot/report scripts.
# Examples:
#   ./cli.sh run --pdb 7X2F --chain A --ligand LDP
#   ./cli.sh run --pdb 6CM4 --chain A --ligand 8NU --lig_spec ligand.sdf --pdb_file receptor.pdb
#   ./cli.sh batch --manifest jobs.tsv
#   ./cli.sh report --out-dir final_results   # regenerate plots/reports from processed runs (non-PyMOL)

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)

usage() {
  cat <<'EOF'
Usage:
  cli.sh run   --pdb <ID> --chain <CHAIN> --ligand <RESNAME> [--lig_spec file] [--pdb_file file] [--grid_pad val|x,y,z] [--grid_file file] [--runs N] [--out-root dir]
  cli.sh batch --manifest <tsv_with_cols:pdb chain ligand [lig_spec] [pdb_file] [grid_pad] [grid_file]> [--runs N] [--out-root dir]
  cli.sh report [--out-dir dir]          # regenerate plots (non-PyMOL) using code/run_all_outputs.py

Notes:
  - Assumes dock env provides: vina, pdb2pqr30, mk_prepare_receptor.py, mk_prepare_ligand.py, rdkit, pymol2 (optional for autogrid/PyMOL scenes), requests/bs4.
  - run1.sh is used as the core runner (wraps dock1.sh + RMSD + results.json).
  - report step uses code/run_all_outputs.py (PyMOL not invoked).
EOF
  exit 1
}

check_deps() {
  local missing=()
  for bin in vina pdb2pqr30 python; do
    command -v "$bin" >/dev/null 2>&1 || missing+=("$bin")
  done
  if [ ${#missing[@]} -ne 0 ]; then
    echo "Missing dependencies: ${missing[*]}" >&2
    exit 2
  fi
}

run_single() {
  local pdb="" chain="" ligand=""
  local lig_spec="" pdb_file="" grid_pad="" grid_file="" runs="1" out_root=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --pdb) pdb="$2"; shift 2;;
      --chain) chain="$2"; shift 2;;
      --ligand) ligand="$2"; shift 2;;
      --lig_spec) lig_spec="$2"; shift 2;;
      --pdb_file) pdb_file="$2"; shift 2;;
      --grid_pad) grid_pad="$2"; shift 2;;
      --grid_file) grid_file="$2"; shift 2;;
      --runs) runs="$2"; shift 2;;
      --out-root) out_root="$2"; shift 2;;
      *) echo "Unknown arg: $1"; usage;;
    esac
  done
  [ -n "$pdb" ] && [ -n "$chain" ] && [ -n "$ligand" ] || usage

  for ((run_id=1; run_id<=runs; run_id++)); do
    local args=("$pdb" "$chain" "$ligand" --run_id "$run_id")
    [ -n "$lig_spec" ] && args+=(--lig_spec "$lig_spec")
    [ -n "$pdb_file" ] && args+=(--pdb_file "$pdb_file")
    [ -n "$grid_pad" ] && args+=(--grid_pad "$grid_pad")
    [ -n "$grid_file" ] && args+=(--grid_file "$grid_file")
    [ -n "$out_root" ] && args+=(--out_root "$out_root")
    bash "$SCRIPT_DIR/run1.sh" "${args[@]}"
  done
}

run_batch() {
  local manifest="$1"
  local runs="${2:-1}"
  local out_root="${3:-}"
  [ -f "$manifest" ] || { echo "Manifest not found: $manifest" >&2; exit 2; }
  # Expected TSV columns: pdb chain ligand [lig_spec] [pdb_file] [grid_pad] [grid_file]
  while IFS=$'\t' read -r pdb chain ligand lig_spec pdb_file grid_pad grid_file; do
    # skip blanks/comments
    if [[ -z "$pdb" || "$pdb" =~ ^# ]]; then
      continue
    fi
    echo "=== Running $pdb/$chain/$ligand ==="
    run_single --pdb "$pdb" --chain "$chain" --ligand "$ligand" \
      ${lig_spec:+--lig_spec "$lig_spec"} \
      ${pdb_file:+--pdb_file "$pdb_file"} \
      ${grid_pad:+--grid_pad "$grid_pad"} \
      ${grid_file:+--grid_file "$grid_file"} \
      --runs "$runs" \
      ${out_root:+--out-root "$out_root"}
  done < "$manifest"
}

run_report() {
  local out_dir="final"
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --out-dir) out_dir="$2"; shift 2;;
      *) echo "Unknown arg: $1"; usage;;
    esac
  done
  python "$ROOT_DIR/code/run_all_outputs.py" --out-dir "$out_dir"
}

main() {
  [ "$#" -ge 1 ] || usage
  local cmd="$1"; shift
  case "$cmd" in
    run)
      check_deps
      run_single "$@"
      ;;
    batch)
      check_deps
      [ "$#" -ge 2 ] && [ "$1" = "--manifest" ] || usage
      local manifest_path="$2"
      shift 2
      local runs="1"
      local out_root=""
      while [ "$#" -gt 0 ]; do
        case "$1" in
          --runs) runs="$2"; shift 2;;
          --out-root) out_root="$2"; shift 2;;
          *) echo "Unknown arg: $1"; usage;;
        esac
      done
      run_batch "$manifest_path" "$runs" "$out_root"
      ;;
    report)
      run_report "$@"
      ;;
    *)
      usage
      ;;
  esac
}

main "$@"
