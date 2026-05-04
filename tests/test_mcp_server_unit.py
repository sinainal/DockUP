from __future__ import annotations

import json

from docking_app import mcp_server


def test_mcp_lists_core_tools() -> None:
    server = mcp_server.DockUPMCPServer(base_url="http://dockup.local")

    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert response is not None
    tools = response["result"]["tools"]
    assert [tool["name"] for tool in tools] == [
        "dockup_state",
        "dockup_queue_prepare",
        "dockup_run_status",
        "dockup_control",
    ]


def test_mcp_state_tool_returns_control_payload(monkeypatch) -> None:
    class FakeClient:
        def get_state(self):
            return {"ok": True, "action": "state.get", "data": {"queue_count": 2}}

    server = mcp_server.DockUPMCPServer(base_url="http://dockup.local")
    server.client = FakeClient()

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "dockup_state", "arguments": {}},
        }
    )

    assert response is not None
    result = response["result"]
    payload = json.loads(result["content"][0]["text"])
    assert result["isError"] is False
    assert payload["data"]["queue_count"] == 2
