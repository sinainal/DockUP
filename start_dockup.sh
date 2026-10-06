#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
"$SCRIPT_DIR/.venv/bin/python" -m uvicorn docking_app.app:app --host 127.0.0.1 --port 8000 &
echo "DockUP API started in background (port 8000, PID $!)."
