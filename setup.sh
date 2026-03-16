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
TOOLS_DIR="$ROOT_DIR/docking_app/workspace/tools"
P2RANK_DIR="$TOOLS_DIR/p2rank"
P2RANK_JAVA_HOME="$TOOLS_DIR/p2rank_java"
P2RANK_VERSION="2.5.1"
P2RANK_ARCHIVE_URL="https://github.com/rdk/p2rank/releases/download/${P2RANK_VERSION}/p2rank_${P2RANK_VERSION}.tar.gz"
P2RANK_JAVA_MIN_MAJOR=17

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${BLUE}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
header()  { echo -e "\n${BOLD}$*${RESET}"; }

download_file() {
  local url="$1"
  local dest="$2"
  "$VENV_PYTHON" - "$url" "$dest" <<'PY'
import shutil
import sys
import urllib.request

url, dest = sys.argv[1], sys.argv[2]
request = urllib.request.Request(
    url,
    headers={
        "User-Agent": "DockUP-Setup/1.0",
        "Accept": "*/*",
    },
)
with urllib.request.urlopen(request, timeout=120) as response, open(dest, "wb") as handle:
    shutil.copyfileobj(response, handle)
PY
}

detect_vina_asset() {
  local os_name arch_name
  os_name=$(uname -s)
  arch_name=$(uname -m)
  case "${os_name}:${arch_name}" in
    Linux:x86_64) echo "vina_1.2.7_linux_x86_64" ;;
    Linux:aarch64|Linux:arm64) echo "vina_1.2.7_linux_aarch64" ;;
    Darwin:x86_64) echo "vina_1.2.7_mac_x86_64" ;;
    Darwin:arm64) echo "vina_1.2.7_mac_aarch64" ;;
    *) return 1 ;;
  esac
}

install_vina_cli() {
  local target="$VENV_DIR/bin/vina"
  local asset_name download_url tmp_target
  if [ -x "$target" ] && "$target" --version >/dev/null 2>&1; then
    success "AutoDock Vina CLI available in venv"
    return 0
  fi
  if ! asset_name=$(detect_vina_asset); then
    warn "Unsupported platform for automatic Vina CLI install: $(uname -s) $(uname -m)"
    return 1
  fi
  download_url="https://github.com/ccsb-scripps/AutoDock-Vina/releases/download/v1.2.7/${asset_name}"
  tmp_target="${target}.tmp"
  rm -f "$tmp_target"
  info "Installing official AutoDock Vina CLI (${asset_name})..."
  if ! download_file "$download_url" "$tmp_target"; then
    rm -f "$tmp_target"
    warn "Failed to download AutoDock Vina CLI from ${download_url}"
    return 1
  fi
  chmod +x "$tmp_target"
  mv "$tmp_target" "$target"
  if ! "$target" --version >/dev/null 2>&1; then
    warn "Downloaded Vina CLI did not execute correctly"
    return 1
  fi
  success "AutoDock Vina CLI installed"
  return 0
}

shared_library_available() {
  local lib_name="$1"
  "$VENV_PYTHON" - "$lib_name" <<'PY'
import ctypes
import sys

lib_name = sys.argv[1]
try:
    ctypes.CDLL(lib_name)
except OSError:
    raise SystemExit(1)
PY
}

ensure_plip_runtime_system_libs() {
  local os_name
  os_name=$(uname -s)
  if [ "$os_name" != "Linux" ]; then
    return 0
  fi

  local missing_libs=()
  local lib_name
  for lib_name in libXrender.so.1 libSM.so.6 libXext.so.6; do
    if ! shared_library_available "$lib_name"; then
      missing_libs+=("$lib_name")
    fi
  done

  if [ ${#missing_libs[@]} -eq 0 ]; then
    success "PLIP runtime shared libraries available"
    return 0
  fi

  warn "Missing system libraries required by OpenBabel/PLIP: ${missing_libs[*]}"

  if command -v apt-get >/dev/null 2>&1; then
    info "Attempting to install PLIP runtime libraries via apt-get..."
    if [ "${EUID:-$(id -u)}" -eq 0 ]; then
      apt-get update >> "$LOG_FILE" 2>&1 && \
        apt-get install -y libxrender1 libsm6 libxext6 >> "$LOG_FILE" 2>&1 || return 1
    elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
      sudo -n apt-get update >> "$LOG_FILE" 2>&1 && \
        sudo -n apt-get install -y libxrender1 libsm6 libxext6 >> "$LOG_FILE" 2>&1 || return 1
    else
      warn "Install these packages manually and re-run setup: sudo apt-get install -y libxrender1 libsm6 libxext6"
      return 1
    fi
  elif command -v dnf >/dev/null 2>&1; then
    warn "Install these packages manually and re-run setup: sudo dnf install -y libXrender libSM libXext"
    return 1
  elif command -v yum >/dev/null 2>&1; then
    warn "Install these packages manually and re-run setup: sudo yum install -y libXrender libSM libXext"
    return 1
  elif command -v pacman >/dev/null 2>&1; then
    warn "Install these packages manually and re-run setup: sudo pacman -S --needed libxrender libsm libxext"
    return 1
  else
    warn "Unknown Linux package manager. Install libXrender.so.1, libSM.so.6, and libXext.so.6 manually."
    return 1
  fi

  local still_missing=()
  for lib_name in libXrender.so.1 libSM.so.6 libXext.so.6; do
    if ! shared_library_available "$lib_name"; then
      still_missing+=("$lib_name")
    fi
  done
  if [ ${#still_missing[@]} -gt 0 ]; then
    warn "PLIP runtime libraries are still missing after installation attempt: ${still_missing[*]}"
    return 1
  fi

  success "Installed PLIP runtime system libraries"
  return 0
}

verify_plip_runtime() {
  local plip_bin="$VENV_DIR/bin/plip"
  if [ ! -x "$plip_bin" ]; then
    warn "PLIP CLI not found in venv"
    return 1
  fi
  if ! "$plip_bin" -h >> "$LOG_FILE" 2>&1; then
    warn "PLIP CLI failed to start — interaction analysis will not work"
    warn "Check $LOG_FILE for the underlying PLIP/OpenBabel error"
    return 1
  fi
  success "PLIP runtime verified"
  return 0
}

verify_pymol_python() {
  local py_bin="$1"
  if [ -z "$py_bin" ] || ! command -v "$py_bin" >/dev/null 2>&1; then
    return 1
  fi
  "$py_bin" -c "import pymol2" >/dev/null 2>&1
}

resolve_pymol_bin() {
  local py_bin="$1"
  local candidate=""
  if [ -n "$py_bin" ]; then
    candidate="$(dirname "$py_bin")/pymol"
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi
  if command -v pymol >/dev/null 2>&1; then
    command -v pymol
    return 0
  fi
  return 1
}

verify_pymol_binary() {
  local pymol_bin="$1"
  local probe_pml
  if [ -z "$pymol_bin" ] || [ ! -x "$pymol_bin" ]; then
    return 1
  fi
  probe_pml=$(mktemp "$ROOT_DIR/.pymol_probe.XXXXXX.pml")
  printf 'quit\n' > "$probe_pml"
  if "$pymol_bin" -cq "$probe_pml" >> "$LOG_FILE" 2>&1; then
    rm -f "$probe_pml"
    return 0
  fi
  rm -f "$probe_pml"
  return 1
}

ensure_pymol_runtime_system_libs() {
  local os_name
  os_name=$(uname -s)
  if [ "$os_name" != "Linux" ]; then
    return 0
  fi
  if shared_library_available "libGL.so.1"; then
    success "PyMOL runtime shared libraries available"
    return 0
  fi

  warn "Missing system library required by PyMOL: libGL.so.1"
  if command -v apt-get >/dev/null 2>&1; then
    info "Attempting to install PyMOL runtime library via apt-get..."
    if [ "${EUID:-$(id -u)}" -eq 0 ]; then
      apt-get update >> "$LOG_FILE" 2>&1 && \
        apt-get install -y libgl1 >> "$LOG_FILE" 2>&1 || return 1
    elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
      sudo -n apt-get update >> "$LOG_FILE" 2>&1 && \
        sudo -n apt-get install -y libgl1 >> "$LOG_FILE" 2>&1 || return 1
    else
      warn "Install this package manually and re-run setup: sudo apt-get install -y libgl1"
      return 1
    fi
  elif command -v dnf >/dev/null 2>&1; then
    warn "Install this package manually and re-run setup: sudo dnf install -y mesa-libGL"
    return 1
  elif command -v yum >/dev/null 2>&1; then
    warn "Install this package manually and re-run setup: sudo yum install -y mesa-libGL"
    return 1
  elif command -v pacman >/dev/null 2>&1; then
    warn "Install this package manually and re-run setup: sudo pacman -S --needed mesa"
    return 1
  else
    warn "Unknown Linux package manager. Install libGL.so.1 manually."
    return 1
  fi

  if ! shared_library_available "libGL.so.1"; then
    warn "PyMOL runtime library libGL.so.1 is still missing after installation attempt"
    return 1
  fi

  success "Installed PyMOL runtime system library"
  return 0
}

install_pymol_in_venv() {
  if ! ensure_pymol_runtime_system_libs; then
    return 1
  fi
  info "Installing PyMOL into venv..."
  if ! "$PIP" install --quiet pymol-open-source >> "$LOG_FILE" 2>&1; then
    warn "PyMOL install failed — report rendering and PSE scene generation may not work"
    return 1
  fi
  if ! verify_pymol_python "$VENV_PYTHON"; then
    warn "PyMOL Python bindings are unavailable after installation"
    return 1
  fi
  if ! verify_pymol_binary "$VENV_DIR/bin/pymol"; then
    warn "PyMOL binary failed to start after installation"
    return 1
  fi
  success "PyMOL installed in venv"
  return 0
}

parse_java_major_version() {
  local java_exec="$1"
  local version_line major
  if ! version_line=$("$java_exec" -version 2>&1 | head -1); then
    return 1
  fi
  major=$(printf '%s\n' "$version_line" | sed -n 's/.*version "\([0-9][0-9]*\).*/\1/p')
  if [ -z "$major" ]; then
    return 1
  fi
  printf '%s\n' "$major"
}

verify_java_runtime() {
  local java_exec="$1"
  local major
  if [ ! -x "$java_exec" ]; then
    return 1
  fi
  major=$(parse_java_major_version "$java_exec" || true)
  if [ -z "$major" ] || [ "$major" -lt "$P2RANK_JAVA_MIN_MAJOR" ]; then
    return 1
  fi
  return 0
}

detect_temurin_jre_url() {
  local os_name arch_name
  os_name=$(uname -s)
  arch_name=$(uname -m)
  case "${os_name}:${arch_name}" in
    Linux:x86_64) echo "https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jre/hotspot/normal/eclipse" ;;
    Linux:aarch64|Linux:arm64) echo "https://api.adoptium.net/v3/binary/latest/17/ga/linux/aarch64/jre/hotspot/normal/eclipse" ;;
    Darwin:x86_64) echo "https://api.adoptium.net/v3/binary/latest/17/ga/mac/x64/jre/hotspot/normal/eclipse" ;;
    Darwin:arm64) echo "https://api.adoptium.net/v3/binary/latest/17/ga/mac/aarch64/jre/hotspot/normal/eclipse" ;;
    *) return 1 ;;
  esac
}

install_local_p2rank_java() {
  local java_exec="${P2RANK_JAVA_HOME}/bin/java"
  local download_url tmp_dir archive extracted_dir

  mkdir -p "$TOOLS_DIR"

  if [ "$FORCE" -eq 1 ] && [ -d "$P2RANK_JAVA_HOME" ]; then
    rm -rf "$P2RANK_JAVA_HOME"
  fi

  if verify_java_runtime "$java_exec"; then
    ln -sfn "$java_exec" "$VENV_DIR/bin/java"
    success "Local Java runtime for P2Rank available"
    return 0
  fi

  if ! download_url=$(detect_temurin_jre_url); then
    warn "Unsupported platform for automatic P2Rank Java install: $(uname -s) $(uname -m)"
    return 1
  fi

  tmp_dir=$(mktemp -d)
  archive="$tmp_dir/p2rank-java.tar.gz"
  rm -rf "$P2RANK_JAVA_HOME"

  info "Installing local Java runtime for P2Rank..."
  if ! download_file "$download_url" "$archive"; then
    rm -rf "$tmp_dir"
    warn "Failed to download Java runtime for P2Rank"
    return 1
  fi
  if ! tar -xzf "$archive" -C "$tmp_dir" >> "$LOG_FILE" 2>&1; then
    rm -rf "$tmp_dir"
    warn "Failed to extract Java runtime for P2Rank"
    return 1
  fi

  extracted_dir=$(find "$tmp_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)
  if [ -z "$extracted_dir" ] || [ ! -x "$extracted_dir/bin/java" ]; then
    rm -rf "$tmp_dir"
    warn "Extracted Java runtime for P2Rank is invalid"
    return 1
  fi

  mv "$extracted_dir" "$P2RANK_JAVA_HOME"
  ln -sfn "$P2RANK_JAVA_HOME/bin/java" "$VENV_DIR/bin/java"
  rm -rf "$tmp_dir"

  if ! verify_java_runtime "$P2RANK_JAVA_HOME/bin/java"; then
    warn "Installed Java runtime for P2Rank did not verify correctly"
    return 1
  fi

  success "Local Java runtime for P2Rank installed"
  return 0
}

write_p2rank_wrapper() {
  cat > "$VENV_DIR/bin/prank" <<EOF
#!/usr/bin/env bash
exec "$P2RANK_DIR/prank" "\$@"
EOF
  chmod +x "$VENV_DIR/bin/prank"
}

install_p2rank_distribution() {
  local prank_bin="$P2RANK_DIR/prank"
  local tmp_dir archive extracted_dir

  mkdir -p "$TOOLS_DIR"

  if [ "$FORCE" -eq 1 ] && [ -d "$P2RANK_DIR" ]; then
    rm -rf "$P2RANK_DIR"
  fi

  if [ -x "$prank_bin" ]; then
    write_p2rank_wrapper
    success "P2Rank distribution already present"
    return 0
  fi

  tmp_dir=$(mktemp -d)
  archive="$tmp_dir/p2rank.tar.gz"

  info "Installing P2Rank ${P2RANK_VERSION}..."
  if ! download_file "$P2RANK_ARCHIVE_URL" "$archive"; then
    rm -rf "$tmp_dir"
    warn "Failed to download P2Rank distribution"
    return 1
  fi
  if ! tar -xzf "$archive" -C "$tmp_dir" >> "$LOG_FILE" 2>&1; then
    rm -rf "$tmp_dir"
    warn "Failed to extract P2Rank distribution"
    return 1
  fi

  extracted_dir=$(find "$tmp_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)
  if [ -z "$extracted_dir" ] || [ ! -x "$extracted_dir/prank" ]; then
    rm -rf "$tmp_dir"
    warn "Extracted P2Rank distribution is invalid"
    return 1
  fi

  rm -rf "$P2RANK_DIR"
  mv "$extracted_dir" "$P2RANK_DIR"
  write_p2rank_wrapper
  rm -rf "$tmp_dir"

  success "P2Rank distribution installed"
  return 0
}

verify_p2rank_runtime() {
  local prank_bin="$P2RANK_DIR/prank"
  local smoke_dir
  local prediction_files=()

  if [ ! -x "$prank_bin" ]; then
    warn "P2Rank executable not found after install"
    return 1
  fi
  if ! verify_java_runtime "$P2RANK_JAVA_HOME/bin/java"; then
    warn "P2Rank Java runtime is unavailable or too old"
    return 1
  fi

  if ! PATH="$VENV_DIR/bin:$PATH" JAVA_HOME="$P2RANK_JAVA_HOME" "$prank_bin" -v >> "$LOG_FILE" 2>&1; then
    warn "P2Rank version check failed"
    return 1
  fi

  smoke_dir=$(mktemp -d "$ROOT_DIR/.p2rank_smoke.XXXXXX")
  if ! PATH="$VENV_DIR/bin:$PATH" JAVA_HOME="$P2RANK_JAVA_HOME" \
    "$prank_bin" predict -f "$P2RANK_DIR/test_data/1fbl.pdb" -o "$smoke_dir" -visualizations 0 >> "$LOG_FILE" 2>&1; then
    rm -rf "$smoke_dir"
    warn "P2Rank smoke prediction failed"
    return 1
  fi

  shopt -s nullglob
  prediction_files=("$smoke_dir"/*_predictions.csv)
  shopt -u nullglob
  rm -rf "$smoke_dir"
  if [ ${#prediction_files[@]} -eq 0 ]; then
    warn "P2Rank smoke prediction produced no pocket output"
    return 1
  fi

  success "P2Rank runtime verified"
  return 0
}

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
P2RANK_OK=0
if [ "$CORE_ONLY" -eq 0 ]; then
  header "Step 4/6 — Installing docking tools (pdb2pqr, meeko, vina, rdkit, openbabel, P2Rank)"
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

  if ! install_vina_cli; then
    DOCKING_FAILED+=("vina-cli")
  fi

  if ! install_local_p2rank_java; then
    DOCKING_FAILED+=("p2rank-java")
  fi
  if ! install_p2rank_distribution; then
    DOCKING_FAILED+=("p2rank")
  elif verify_p2rank_runtime; then
    P2RANK_OK=1
  else
    DOCKING_FAILED+=("p2rank-runtime")
  fi

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

if ! ensure_plip_runtime_system_libs; then
  warn "PLIP runtime prerequisites are incomplete"
elif ! verify_plip_runtime; then
  warn "PLIP runtime verification failed"
fi

# ── 6. PyMOL (rendering + PSE scenes) ───────────────────────────────────────
header "Step 6/6 — PyMOL (report rendering + PSE scene generation)"

PYMOL_OK=0
PYMOL_BIN=""
PYMOL_PYTHON="$VENV_PYTHON"

# Prefer a working venv-local PyMOL first.
if verify_pymol_python "$VENV_PYTHON"; then
  candidate_bin="$(resolve_pymol_bin "$VENV_PYTHON" || true)"
  if [ -n "$candidate_bin" ] && verify_pymol_binary "$candidate_bin"; then
    PYMOL_BIN="$candidate_bin"
    PYMOL_OK=1
    success "PyMOL available in venv"
  fi
fi

if [ "$PYMOL_OK" -eq 0 ] && [ "$CORE_ONLY" -eq 0 ]; then
  install_pymol_in_venv || true
  if verify_pymol_python "$VENV_PYTHON"; then
    candidate_bin="$(resolve_pymol_bin "$VENV_PYTHON" || true)"
    if [ -n "$candidate_bin" ] && verify_pymol_binary "$candidate_bin"; then
      PYMOL_BIN="$candidate_bin"
      PYMOL_OK=1
      success "PyMOL ready in venv"
    fi
  fi
fi

if [ "$PYMOL_OK" -eq 0 ]; then
  # Fall back to an externally managed Python if the venv install path is unavailable.
  for py_candidate in "${CONDA_PREFIX:-__none__}/bin/python" python3; do
    if [ "$py_candidate" = "__none__/bin/python" ]; then
      continue
    fi
    if verify_pymol_python "$py_candidate"; then
      candidate_bin="$(resolve_pymol_bin "$py_candidate" || true)"
      if [ -n "$candidate_bin" ] && verify_pymol_binary "$candidate_bin"; then
        PYMOL_PYTHON="$py_candidate"
        PYMOL_BIN="$candidate_bin"
        PYMOL_OK=1
        success "PyMOL found via: $py_candidate"
        break
      fi
    fi
  done
fi

if [ "$PYMOL_OK" -eq 0 ] && [ "$WITH_PYMOL" -eq 1 ] && command -v conda &>/dev/null; then
  info "Attempting external PyMOL installation via conda..."
  conda install -c conda-forge pymol-open-source -y --quiet >> "$LOG_FILE" 2>&1 || true
  for py_candidate in "${CONDA_PREFIX:-__none__}/bin/python" python3; do
    if [ "$py_candidate" = "__none__/bin/python" ]; then
      continue
    fi
    if verify_pymol_python "$py_candidate"; then
      candidate_bin="$(resolve_pymol_bin "$py_candidate" || true)"
      if [ -n "$candidate_bin" ] && verify_pymol_binary "$candidate_bin"; then
        PYMOL_PYTHON="$py_candidate"
        PYMOL_BIN="$candidate_bin"
        PYMOL_OK=1
        success "PyMOL installed via conda-forge"
        break
      fi
    fi
  done
fi

if [ "$PYMOL_OK" -eq 0 ]; then
  warn "PyMOL not available. Report render images and PSE scene files will be skipped/fail."
  warn "Manual install hint: conda install -c conda-forge pymol-open-source"
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
DOCKUP_PYMOL_BIN="${PYMOL_BIN:-}"
DOCKUP_VINA="$VENV_DIR/bin/vina"
DOCKUP_PYMOL_OK="${PYMOL_OK}"
DOCKUP_P2RANK_BIN="$P2RANK_DIR/prank"
DOCKUP_P2RANK_JAVA_HOME="$P2RANK_JAVA_HOME"
DOCKUP_P2RANK_OK="${P2RANK_OK}"
ENVFILE

# ── Mark setup as complete ───────────────────────────────────────────────────
PYTHON_VER_FULL=$("$VENV_PYTHON" -c "import sys; print(sys.version)")
cat > "$SETUP_DONE_FILE" <<DONEFILE
# DockUP setup completed — $(date -u '+%Y-%m-%d %H:%M:%S UTC')
# Python: $PYTHON_VER_FULL
# Venv: $VENV_DIR
# Core-only: $CORE_ONLY
# PyMOL: $PYMOL_OK
# P2Rank: $P2RANK_OK
DONEFILE

echo ""
echo -e "${BOLD}============================================================${RESET}"
echo -e "${GREEN}${BOLD}  ✓ DockUP setup complete!${RESET}"
echo -e "${BOLD}============================================================${RESET}"
echo ""
echo -e "  Start the server:  ${BOLD}./start.sh${RESET}"
echo ""
if [ "$PYMOL_OK" -eq 0 ]; then
  echo -e "  ${YELLOW}ℹ  PyMOL not installed — report render images and PSE scenes are unavailable.${RESET}"
  echo -e "  ${YELLOW}   To retry install: ./setup.sh --force --with-pymol${RESET}"
  echo ""
fi
