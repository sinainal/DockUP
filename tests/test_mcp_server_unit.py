from __future__ import annotations

import json
from io import BytesIO

from docking_app import mcp_server


def test_mcp_lists_core_tools() -> None:
    server = mcp_server.DockUPMCPServer(base_url="http://dockup.local")

    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert response is not None
    tools = response["result"]["tools"]
    assert [tool["name"] for tool in tools] == [
        "dockup_state",
        "dockup_assets",
        "dockup_mutate",
        "dockup_queue",
        "dockup_validate",
        "dockup_backend",
        "dockup_report",
    ]


def test_mcp_state_tool_returns_compact_payload(monkeypatch) -> None:
    class FakeClient:
        def get_state(self):
            return {
                "ok": True,
                "action": "state.get",
                "message": "state: receptor=6CM4 queue=2 run=idle",
                "data": {
                    "mode": "Docking",
                    "selected_receptor": "6CM4",
                    "selected_chain": "A",
                    "selected_ligand": "all_set",
                    "active_ligands": ["lig.sdf"],
                    "queue_count": 2,
                    "queue": [{"run_count": 5}, {"run_count": 5}],
                    "runs": 5,
                    "run_status": "idle",
                    "agent_grid_data": {"6CM4": {}},
                },
            }

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
    assert payload["facts"]["queue_count"] == 2
    assert payload["facts"]["total_runs"] == 10
    assert "queue" not in payload["facts"]


def test_mcp_assets_detects_receptor_file_mismatch() -> None:
    class FakeClient:
        def list_receptors(self):
            return {
                "ok": True,
                "data": {
                    "receptors": [
                        {"pdb_id": "8IRV", "name": "6CM4.pdb", "loaded": True},
                        {"pdb_id": "6CM4", "name": "6CM4.pdb", "loaded": True},
                    ]
                },
            }

        def list_ligands(self):
            return {"ok": True, "data": {"ligands": ["lig.sdf"]}}

    server = mcp_server.DockUPMCPServer(base_url="http://dockup.local")
    server.client = FakeClient()

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "dockup_assets", "arguments": {"view": "mismatches"}},
        }
    )

    assert response is not None
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["facts"]["mismatch_count"] == 1
    assert payload["facts"]["mismatches"] == [{"pdb_id": "8IRV", "pdb_file": "6CM4.pdb"}]
    assert payload["warnings"]


def test_mcp_validate_checks_queue_expectations() -> None:
    class FakeClient:
        def get_state(self):
            return {
                "ok": True,
                "data": {
                    "mode": "Docking",
                    "queue_count": 1,
                    "run_status": "idle",
                    "active_ligands": ["lig.sdf"],
                    "queue": [
                        {
                            "job_type": "Docking",
                            "pdb_id": "6CM4",
                            "pdb_file": "/tmp/6CM4.pdb",
                            "ligand_name": "lig.sdf",
                            "run_count": 5,
                            "grid_params": {"sx": 25, "sy": 25, "sz": 25},
                        }
                    ],
                },
            }

        def list_receptors(self):
            return {"ok": True, "data": {"receptors": [{"pdb_id": "6CM4", "name": "6CM4.pdb"}]}}

        def list_ligands(self):
            return {"ok": True, "data": {"ligands": ["lig.sdf"]}}

    server = mcp_server.DockUPMCPServer(base_url="http://dockup.local")
    server.client = FakeClient()

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "dockup_validate",
                "arguments": {
                    "checks": {
                        "queue_count": 1,
                        "total_runs": 5,
                        "run_status": "idle",
                        "job_type": "Docking",
                        "grid_size": [25, 25, 25],
                        "pdb_file_matches_pdb_id": True,
                    }
                },
            },
        }
    )

    assert response is not None
    payload = json.loads(response["result"]["content"][0]["text"])
    assert response["result"]["isError"] is False
    assert payload["ok"] is True
    assert payload["facts"]["passed"] is True


def test_mcp_exposes_short_control_resource() -> None:
    server = mcp_server.DockUPMCPServer(base_url="http://dockup.local")

    listed = server.handle({"jsonrpc": "2.0", "id": 5, "method": "resources/list"})
    assert listed is not None
    uri = listed["result"]["resources"][0]["uri"]
    read = server.handle({"jsonrpc": "2.0", "id": 6, "method": "resources/read", "params": {"uri": uri}})

    assert read is not None
    text = read["result"]["contents"][0]["text"]
    assert "Do not start runs" in text


def test_mcp_backend_status_is_compact(monkeypatch) -> None:
    server = mcp_server.DockUPMCPServer(base_url="http://dockup.local")
    monkeypatch.setattr(server, "_is_local_base_url", lambda: True)
    monkeypatch.setattr(server, "_backend_running", lambda: False)

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "dockup_backend", "arguments": {"action": "status"}},
        }
    )

    assert response is not None
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["facts"]["running"] is False
    assert payload["facts"]["local"] is True


def test_mcp_report_render_passes_dpi_alias() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.render_payload = None

        def trigger_report_render(self, **payload):
            self.render_payload = payload
            return {
                "ok": True,
                "message": "render queued",
                "data": {
                    "status": "queued",
                    "task": "render",
                    "render_images": [{"path": "report_outputs/a.png"}],
                },
            }

    fake = FakeClient()
    server = mcp_server.DockUPMCPServer(base_url="http://dockup.local")
    server.client = fake

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "dockup_report",
                "arguments": {
                    "action": "report.render",
                    "payload": {"source_path": "data/dock/Dopamine_Trimer_30", "render_mode": "otofigure", "dpi": 300},
                },
            },
        }
    )

    assert response is not None
    payload = json.loads(response["result"]["content"][0]["text"])
    assert fake.render_payload["render_dpi"] == 300
    assert payload["facts"]["render_images_count"] == 1


def test_mcp_stdio_supports_ndjson_framing() -> None:
    request = {"jsonrpc": "2.0", "id": 7, "method": "ping"}

    class Stream:
        def __init__(self, initial: bytes = b"") -> None:
            self.buffer = BytesIO(initial)

    incoming = Stream((json.dumps(request) + "\n").encode("utf-8"))
    parsed = mcp_server._read_message(incoming)

    assert parsed == (request, "ndjson")

    outgoing = Stream()
    mcp_server._write_message(outgoing, {"jsonrpc": "2.0", "id": 7, "result": {}}, framing="ndjson")
    outgoing.buffer.seek(0)
    assert json.loads(outgoing.buffer.readline().decode("utf-8"))["id"] == 7
