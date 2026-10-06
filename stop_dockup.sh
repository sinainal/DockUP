#!/usr/bin/env bash
pkill -f "docking_app.app:app" 2>/dev/null && echo "DockUP API stopped." || echo "DockUP was not running."
pkill -f "docking_app.mcp_server" 2>/dev/null || true
