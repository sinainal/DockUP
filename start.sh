#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$ROOT_DIR"
PORT="${PORT:-8000}"
APP_MODULE="docking_app.app:app"
export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"

# ── Setup Check ─────────────────────────────────────────────────────────────
if [ ! -f "$ROOT_DIR/.setup_done" ] || [ ! -f "$ROOT_DIR/.venv/bin/activate" ]; then
  echo "=== Running First-time Setup ==="
  bash "$ROOT_DIR/setup.sh"
elif ! "$ROOT_DIR/.venv/bin/python" -c "import fastapi, uvicorn, matplotlib, docx" 2>/dev/null; then
  echo "=== Core dependencies missing in venv (matplotlib/docx) — running setup ==="
  bash "$ROOT_DIR/setup.sh"
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

# ── Port cleanup ─────────────────────────────────────────────────────────────
if command -v lsof >/dev/null 2>&1; then
  pids=$(lsof -ti "tcp:${PORT}" || true)
  if [ -n "$pids" ]; then
    kill $pids || true
    sleep 1
  fi
elif command -v fuser >/dev/null 2>&1; then
  fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true
  sleep 1
fi

echo "=== DockUP Docking Application ==="
echo "    Starting on http://0.0.0.0:${PORT} ..."
echo "    Virtual Env: $VIRTUAL_ENV"
echo ""

if command -v uvicorn >/dev/null 2>&1; then
  exec uvicorn "$APP_MODULE" --host 0.0.0.0 --port "$PORT"
else
  exec python -m uvicorn "$APP_MODULE" --host 0.0.0.0 --port "$PORT"
fi
