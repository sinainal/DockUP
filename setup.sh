#!/usr/bin/env bash
# =============================================================================
#  DockUP — Setup Script
#  Installs all dependencies into a local .venv — no system-wide changes.
#
#  Usage:
#    ./setup.sh              → full install (core + docking tools)
#    ./setup.sh --core-only  → web server only (no docking tools)
#    ./setup.sh --force      → force re-install even if already set up
#    ./setup.sh --with-pymol → also attempt PyMOL installation
# =============================================================================
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
VENV_DIR="$ROOT_DIR/.venv"
SETUP_DONE_FILE="$ROOT_DIR/.setup_done"
LOG_FILE="$ROOT_DIR/.setup.log"

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${BLUE}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
header()  { echo -e "\n${BOLD}$*${RESET}"; }

# ── Argument parsing ─────────────────────────────────────────────────────────
CORE_ONLY=0
FORCE=0
WITH_PYMOL=0
for arg in "$@"; do
  case "$arg" in
    --core-only)  CORE_ONLY=1  ;;
    --force)      FORCE=1      ;;
    --with-pymol) WITH_PYMOL=1 ;;
    --help|-h)
      echo "Usage: $0 [--core-only] [--force] [--with-pymol]"
      exit 0
      ;;
    *)
      error "Unknown argument: $arg"
      exit 1
      ;;
  esac
done

echo ""
echo -e "${BOLD}============================================================${RESET}"
echo -e "${BOLD}  DockUP Setup${RESET}"
echo -e "${BOLD}============================================================${RESET}"
echo ""

# ── Check if already set up ──────────────────────────────────────────────────
if [ "$FORCE" -eq 0 ] && [ -f "$SETUP_DONE_FILE" ] && [ -f "$VENV_DIR/bin/activate" ]; then
  # Verify the venv Python still works
  if "$VENV_DIR/bin/python" -c "import fastapi, uvicorn, matplotlib, docx" 2>/dev/null; then
    success "DockUP is already set up (use --force to re-install)"
    echo ""
    exit 0
  else
    warn "Existing venv seems broken — re-running setup..."
    rm -f "$SETUP_DONE_FILE"
  fi
fi

# ── 1. Find a suitable Python ─────────────────────────────────────────────────
header "Step 1/6 — Checking Python"

PYTHON=""
MIN_MINOR=10

for candidate in python3.12 python3.11 python3.10 python3 python; do
  if command -v "$candidate" &>/dev/null; then
    ver=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || true)
    major=$(echo "$ver" | cut -d. -f1)
    minor=$(echo "$ver" | cut -d. -f2)
    if [ "${major:-0}" -ge 3 ] && [ "${minor:-0}" -ge $MIN_MINOR ]; then
      PYTHON=$(command -v "$candidate")
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  error "Python >= 3.${MIN_MINOR} is required but not found."
  error "Install it via: sudo apt install python3.11  (Ubuntu/Debian)"
  error "                brew install python@3.11      (macOS)"
  exit 1
fi

PYTHON_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
success "Found Python $PYTHON_VER → $PYTHON"

# ── 2. Create virtual environment ────────────────────────────────────────────
header "Step 2/6 — Creating virtual environment"

if [ -d "$VENV_DIR" ] && [ "$FORCE" -eq 1 ]; then
  info "Removing existing .venv (--force)"
  rm -rf "$VENV_DIR"
fi

if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON" -m venv "$VENV_DIR"
  success "Created .venv"
else
  success ".venv already exists — skipping creation"
fi

# Use venv's pip exclusively going forward
PIP="$VENV_DIR/bin/pip"
VENV_PYTHON="$VENV_DIR/bin/python"

# Upgrade pip silently
"$PIP" install --upgrade pip --quiet

# ── 3. Install core dependencies ─────────────────────────────────────────────
header "Step 3/6 — Installing core dependencies (web server)"

"$PIP" install \
  --quiet \
  --disable-pip-version-check \
  -r "$ROOT_DIR/requirements/core.txt" \
  2>&1 | tee -a "$LOG_FILE"

success "Core dependencies installed"

# ── 4. Install docking tools ─────────────────────────────────────────────────
if [ "$CORE_ONLY" -eq 0 ]; then
  header "Step 4/6 — Installing docking tools (pdb2pqr, meeko, vina, rdkit, openbabel)"
  info "This may take a few minutes..."

  DOCKING_FAILED=()

  # Install each package separately for better error reporting
  while IFS= read -r pkg || [[ -n "$pkg" ]]; do
    # Skip comments and empty lines
    [[ "$pkg" =~ ^#.*$ || -z "${pkg// }" ]] && continue
    pkg_name=$(echo "$pkg" | cut -d'>' -f1 | cut -d'<' -f1 | cut -d'=' -f1 | cut -d'~' -f1)
    printf "  Installing %-35s " "$pkg_name..."
    if "$PIP" install --quiet --disable-pip-version-check "$pkg" >> "$LOG_FILE" 2>&1; then
      echo -e "${GREEN}✓${RESET}"
    else
      echo -e "${RED}✗ (failed)${RESET}"
      DOCKING_FAILED+=("$pkg_name")
      warn "  Failed to install $pkg_name — check $LOG_FILE for details"
    fi
  done < "$ROOT_DIR/requirements/docking.txt"

  if [ ${#DOCKING_FAILED[@]} -gt 0 ]; then
    warn ""
    warn "Some docking tools failed to install: ${DOCKING_FAILED[*]}"
    warn "You can retry manually: $PIP install ${DOCKING_FAILED[*]}"
    warn "The web server will still start — only docking may fail."
  else
    success "All docking tools installed"
  fi
else
  header "Step 4/6 — Skipping docking tools (--core-only)"
  info "Run without --core-only to install docking tools later."
fi

# ── 5. Install PLIP (vendored) ────────────────────────────────────────────────
header "Step 5/6 — Setting up PLIP (interaction analysis)"

PLIP_DIR="$ROOT_DIR/docking_app/workspace/plip-2.4.0"
if [ -d "$PLIP_DIR" ]; then
  if "$VENV_PYTHON" -c "import plip" 2>/dev/null; then
    success "PLIP already installed in venv"
  else
    info "Installing vendored PLIP..."
    "$PIP" install --quiet --no-deps -e "$PLIP_DIR" >> "$LOG_FILE" 2>&1 && \
      success "PLIP installed from vendor" || \
      warn "PLIP install failed — interaction analysis may not work"
  fi
else
  warn "PLIP vendor directory not found: $PLIP_DIR"
fi

# ── 6. Optional: PyMOL ───────────────────────────────────────────────────────
header "Step 6/6 — PyMOL (optional, for PSE scene generation)"

PYMOL_OK=0

# Check if PyMOL is already accessible from any Python
for py_candidate in "$VENV_PYTHON" "${CONDA_PREFIX:-__none__}/bin/python" python3; do
  if [ "$py_candidate" = "__none__/bin/python" ]; then continue; fi
  if command -v "$py_candidate" &>/dev/null && "$py_candidate" -c "import pymol2" 2>/dev/null; then
    PYMOL_PYTHON="$py_candidate"
    PYMOL_OK=1
    success "PyMOL found via: $py_candidate"
    break
  fi
done

if [ "$PYMOL_OK" -eq 0 ]; then
  if [ "$WITH_PYMOL" -eq 1 ]; then
    info "Attempting PyMOL installation..."

    # Try conda-forge first (most reliable)
    if command -v conda &>/dev/null; then
      info "  Trying: conda install -c conda-forge pymol-open-source"
      conda install -c conda-forge pymol-open-source -y --quiet >> "$LOG_FILE" 2>&1 && \
        PYMOL_OK=1 && success "  PyMOL installed via conda-forge" || true
    fi

    # Pip fallback
    if [ "$PYMOL_OK" -eq 0 ]; then
      info "  Trying: pip install pymol-open-source"
      "$PIP" install --quiet pymol-open-source >> "$LOG_FILE" 2>&1 && \
        PYMOL_OK=1 && success "  PyMOL installed via pip" || \
        warn "  PyMOL pip install failed — PSE scenes will be skipped during docking"
    fi
  else
    warn "PyMOL not found. PSE scene files will not be generated."
    warn "To install PyMOL: ./setup.sh --with-pymol"
    warn "Or manually:      conda install -c conda-forge pymol-open-source"
  fi
fi

# ── Write DOCKUP_PYTHON env cache ───────────────────────────────────────────
DOCKUP_PYTHON_PATH="$VENV_PYTHON"
if [ "$PYMOL_OK" -eq 1 ] && [ "${PYMOL_PYTHON:-}" != "$VENV_PYTHON" ]; then
  DOCKUP_PYTHON_PATH="${PYMOL_PYTHON:-$VENV_PYTHON}"
fi

# ── Write .env file for dock1.sh / run1.sh ──────────────────────────────────
cat > "$ROOT_DIR/.env" <<ENVFILE
# Auto-generated by setup.sh — do not edit manually
DOCKUP_VENV="$VENV_DIR"
DOCKUP_PYTHON="$VENV_PYTHON"
DOCKUP_PYMOL_PYTHON="${DOCKUP_PYTHON_PATH}"
DOCKUP_PYMOL_OK="${PYMOL_OK}"
ENVFILE

# ── Mark setup as complete ───────────────────────────────────────────────────
PYTHON_VER_FULL=$("$VENV_PYTHON" -c "import sys; print(sys.version)")
cat > "$SETUP_DONE_FILE" <<DONEFILE
# DockUP setup completed — $(date -u '+%Y-%m-%d %H:%M:%S UTC')
# Python: $PYTHON_VER_FULL
# Venv: $VENV_DIR
# Core-only: $CORE_ONLY
# PyMOL: $PYMOL_OK
DONEFILE

echo ""
echo -e "${BOLD}============================================================${RESET}"
echo -e "${GREEN}${BOLD}  ✓ DockUP setup complete!${RESET}"
echo -e "${BOLD}============================================================${RESET}"
echo ""
echo -e "  Start the server:  ${BOLD}./start.sh${RESET}"
echo ""
if [ "$PYMOL_OK" -eq 0 ]; then
  echo -e "  ${YELLOW}ℹ  PyMOL not installed — docking works, PSE scenes skipped.${RESET}"
  echo -e "  ${YELLOW}   To install: ./setup.sh --with-pymol${RESET}"
  echo ""
fi
