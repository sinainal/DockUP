#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$ROOT_DIR"
PORT="${PORT:-8000}"
REQUESTED_PORT="$PORT"
PORT_SEARCH_LIMIT="${PORT_SEARCH_LIMIT:-50}"
APP_MODULE="docking_app.app:app"
export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"

# ── Setup Check ─────────────────────────────────────────────────────────────
if [ ! -f "$ROOT_DIR/.venv/bin/activate" ] || [ ! -x "$ROOT_DIR/.venv/bin/python" ]; then
  echo "=== Running First-time Setup ==="
  bash "$ROOT_DIR/setup.sh"
elif ! "$ROOT_DIR/.venv/bin/python" -c "import fastapi, uvicorn, matplotlib, docx" 2>/dev/null; then
  echo "=== Core dependencies missing in venv (fastapi/uvicorn/matplotlib/docx) — running setup ==="
  bash "$ROOT_DIR/setup.sh"
elif [ ! -f "$ROOT_DIR/.setup_done" ]; then
  echo "[WARN] .setup_done is missing, but the existing venv looks usable. Skipping setup."
fi

if ! "$ROOT_DIR/.venv/bin/python" -c "import cv2" 2>/dev/null; then
  echo "[WARN] Optional dependency cv2 is missing in the current venv."
  echo "[WARN] OtoFigure image assembly may fail until ./setup.sh succeeds."
fi

# Ensure .env exists
if [ -f "$ROOT_DIR/.env" ]; then
  source "$ROOT_DIR/.env"
else
  # Fallback just in case
  DOCKUP_VENV="$ROOT_DIR/.venv"
fi

# Activate virtual environment
if [ -f "${DOCKUP_VENV:-}/bin/activate" ]; then
  source "${DOCKUP_VENV}/bin/activate"
else
  echo "[ERROR] Virtual environment not found. Please run ./setup.sh --force"
  exit 1
fi
VENV_PYTHON="${DOCKUP_VENV}/bin/python"
VENV_UVICORN="${DOCKUP_VENV}/bin/uvicorn"

port_is_available() {
  local candidate="$1"
  python - "$candidate" <<'PY'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind(("0.0.0.0", port))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

try_release_port() {
  local candidate="$1"
  if command -v lsof >/dev/null 2>&1; then
    local pids
    pids=$(lsof -ti "tcp:${candidate}" || true)
    if [ -n "$pids" ]; then
      kill $pids >/dev/null 2>&1 || true
      sleep 1
    fi
  elif command -v fuser >/dev/null 2>&1; then
    fuser -k "${candidate}/tcp" >/dev/null 2>&1 || true
    sleep 1
  fi
}

find_available_port() {
  local start_port="$1"
  local end_port=$((start_port + PORT_SEARCH_LIMIT))
  local candidate="$start_port"
  while [ "$candidate" -le "$end_port" ]; do
    if port_is_available "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
    candidate=$((candidate + 1))
  done
  return 1
}

# ── Port cleanup ─────────────────────────────────────────────────────────────
try_release_port "$PORT"
if ! port_is_available "$PORT"; then
  if ! PORT=$(find_available_port "$PORT"); then
    echo "[ERROR] No available port found in range ${REQUESTED_PORT}-$((REQUESTED_PORT + PORT_SEARCH_LIMIT))"
    exit 1
  fi
  echo "[WARN] Port ${REQUESTED_PORT} is already in use; falling back to ${PORT}."
fi

echo "=== DockUP Docking Application ==="
echo "    Starting on http://0.0.0.0:${PORT} ..."
echo "    Virtual Env: $VIRTUAL_ENV"
echo ""

if [ -x "$VENV_UVICORN" ]; then
  exec "$VENV_UVICORN" "$APP_MODULE" --host 0.0.0.0 --port "$PORT"
else
  exec "$VENV_PYTHON" -m uvicorn "$APP_MODULE" --host 0.0.0.0 --port "$PORT"
fi
