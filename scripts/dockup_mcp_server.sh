#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKUP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$DOCKUP_ROOT"

if [ -n "${DOCKUP_PYTHON:-}" ]; then
  PYTHON_BIN="$DOCKUP_PYTHON"
elif [ -x "$DOCKUP_ROOT/.venv/bin/python" ]; then
  PYTHON_BIN="$DOCKUP_ROOT/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

export PYTHONPATH="$DOCKUP_ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON_BIN" -m docking_app.mcp_server "$@"
