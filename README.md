# DockUP

DockUP is a FastAPI-based molecular docking web application with queue management,
run tracking, report generation, and render pipelines.

## Quick Start

```bash
./setup.sh
./start.sh
```

Then open `http://localhost:8000`.

## MCP Server

DockUP exposes a stdio MCP server through `scripts/dockup_mcp_server.sh`.
The launcher resolves the repository root itself, prefers the local `.venv`, and
falls back to `python3` with `PYTHONPATH` set to the checkout.

Use `mcp/dockup-control.json` as a portable template. For clients that do not
resolve relative paths from the config file, set `cwd` to the absolute DockUP
checkout path or use the absolute path to `scripts/dockup_mcp_server.sh`.

## Notes

- Core dependencies are listed in `requirements/core.txt`.
- Docking toolchain dependencies are listed in `requirements/docking.txt`.
- End-to-end scripts live under `tests/`.
