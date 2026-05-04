from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .live.client import DEFAULT_BASE_URL, DockUPClient


SERVER_INFO = {"name": "dockup-control", "version": "0.1.0"}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "dockup_state",
        "description": "Read the current DockUP live state from the Control API.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "dockup_queue_prepare",
        "description": "Prepare a DockUP queue from a JSON payload without starting a docking run.",
        "inputSchema": {
            "type": "object",
            "properties": {"payload": {"type": "object", "additionalProperties": True}},
            "required": ["payload"],
            "additionalProperties": False,
        },
    },
    {
        "name": "dockup_run_status",
        "description": "Read the current DockUP run status.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "dockup_control",
        "description": "Call a small set of DockUP Control API actions by name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "assets.inspect",
                        "ligand.list",
                        "ligand.active.set",
                        "gridbox.set_many",
                        "queue.list",
                        "queue.build",
                    ],
                },
                "payload": {"type": "object", "additionalProperties": True},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
]


class DockUPMCPServer:
    def __init__(self, *, base_url: str = DEFAULT_BASE_URL, timeout: float = 60.0) -> None:
        self.client = DockUPClient(base_url=base_url, timeout=timeout)

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = str(request.get("method") or "")
        request_id = request.get("id")
        try:
            if method == "initialize":
                return self._result(
                    request_id,
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": SERVER_INFO,
                    },
                )
            if method == "notifications/initialized":
                return None
            if method == "ping":
                return self._result(request_id, {})
            if method == "tools/list":
                return self._result(request_id, {"tools": TOOLS})
            if method == "tools/call":
                params = request.get("params") if isinstance(request.get("params"), dict) else {}
                return self._result(request_id, self._call_tool(str(params.get("name") or ""), params.get("arguments") or {}))
            return self._error(request_id, -32601, f"Unsupported MCP method: {method}")
        except Exception as exc:
            return self._error(request_id, -32000, f"{type(exc).__name__}: {exc}")

    def _call_tool(self, name: str, arguments: Any) -> dict[str, Any]:
        args = arguments if isinstance(arguments, dict) else {}
        if name == "dockup_state":
            payload = self.client.get_state()
        elif name == "dockup_queue_prepare":
            raw_payload = args.get("payload")
            if not isinstance(raw_payload, dict):
                raise ValueError("dockup_queue_prepare requires object argument: payload")
            payload = self.client.prepare_queue(raw_payload)
        elif name == "dockup_run_status":
            payload = self.client.get_run_status()
        elif name == "dockup_control":
            payload = self._dispatch_control(args)
        else:
            raise ValueError(f"Unknown tool: {name}")
        return {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
            "isError": not bool(payload.get("ok", not payload.get("error"))),
        }

    def _dispatch_control(self, args: dict[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "").strip()
        payload = args.get("payload") if isinstance(args.get("payload"), dict) else {}
        if action == "assets.inspect":
            return self.client.inspect_assets()
        if action == "ligand.list":
            return self.client.list_ligands()
        if action == "ligand.active.set":
            names = payload.get("names") if isinstance(payload.get("names"), list) else []
            return self.client.set_active_ligands([str(item) for item in names], replace=bool(payload.get("replace", True)))
        if action == "gridbox.set_many":
            grid_data = payload.get("grid_data") if isinstance(payload.get("grid_data"), dict) else payload
            return self.client.set_gridboxes(grid_data)
        if action == "queue.list":
            return self.client.list_queue()
        if action == "queue.build":
            return self.client.build_queue(replace_queue=bool(payload.get("replace_queue", True)))
        raise ValueError(f"Unsupported dockup_control action: {action}")

    @staticmethod
    def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _read_message(stdin: Any) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = stdin.buffer.readline()
        if not line:
            return None
        text = line.decode("utf-8").strip()
        if not text:
            break
        if ":" in text:
            key, value = text.split(":", 1)
            headers[key.lower()] = value.strip()
    length = int(headers.get("content-length") or 0)
    if length <= 0:
        return None
    body = stdin.buffer.read(length)
    return json.loads(body.decode("utf-8"))


def _write_message(stdout: Any, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    stdout.buffer.flush()


def serve_stdio(*, base_url: str = DEFAULT_BASE_URL, timeout: float = 60.0) -> None:
    server = DockUPMCPServer(base_url=base_url, timeout=timeout)
    while True:
        request = _read_message(sys.stdin)
        if request is None:
            break
        response = server.handle(request)
        if response is not None:
            _write_message(sys.stdout, response)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DockUP MCP stdio server")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)
    serve_stdio(base_url=args.base_url, timeout=args.timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
