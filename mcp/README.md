# DockUP MCP

DockUP's MCP server is launched through `scripts/dockup_mcp_server.sh`.
The script resolves the checkout path, prefers `.venv/bin/python`, falls back to
`python3`, and exports `PYTHONPATH` so the server can run from a source checkout.

## Portable Template

Use `mcp/dockup-control.json` when your MCP client resolves relative paths from
the repository root:

```json
{
  "mcpServers": {
    "dockup-control": {
      "command": "./scripts/dockup_mcp_server.sh",
      "args": ["--base-url", "http://127.0.0.1:8000"],
      "cwd": ".",
      "timeout": 10000
    }
  }
}
```

## Client-Agnostic Registration

For clients that do not resolve relative paths predictably, register with
absolute paths:

```json
{
  "mcpServers": {
    "dockup-control": {
      "command": "/absolute/path/to/DockUP/scripts/dockup_mcp_server.sh",
      "args": ["--base-url", "http://127.0.0.1:8000"],
      "cwd": "/absolute/path/to/DockUP",
      "timeout": 10000
    }
  }
}
```

Set `DOCKUP_PYTHON` if you want a specific interpreter:

```bash
DOCKUP_PYTHON=/path/to/python scripts/dockup_mcp_server.sh --base-url http://127.0.0.1:8000
```
