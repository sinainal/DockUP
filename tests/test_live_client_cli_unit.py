from __future__ import annotations

import json

import httpx

from docking_app.live import DockUPClient
from docking_app import cli


def _transport(routes: dict[tuple[str, str], dict[str, object]], status_code: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        payload = routes.get(key, {"error": f"unexpected {request.method} {request.url.path}"})
        code = status_code if key in routes else 404
        return httpx.Response(code, json=payload)

    return httpx.MockTransport(handler)


def test_live_client_reads_state_from_ui_endpoint() -> None:
    client = DockUPClient(
        "http://dockup.local",
        transport=_transport({("GET", "/api/state"): {"selected_receptor": "6CM4", "queue_count": 2}}),
    )

    payload = client.get_state()

    assert payload["selected_receptor"] == "6CM4"
    assert payload["queue_count"] == 2


def test_live_client_reads_run_status_from_ui_endpoint() -> None:
    client = DockUPClient(
        "http://dockup.local",
        transport=_transport({("GET", "/api/run/status"): {"status": "idle", "total_runs": 0}}),
    )

    payload = client.get_run_status()

    assert payload["status"] == "idle"
    assert payload["total_runs"] == 0


def test_live_client_loads_and_selects_receptor_through_ui_endpoints() -> None:
    seen: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8") or "{}") if request.content else None
        seen.append((request.method, request.url.path, payload))
        if request.url.path == "/api/receptors/load":
            return httpx.Response(200, json={"summary": [{"pdb_id": "6CM4"}], "ignored_ids": []})
        if request.url.path == "/api/receptors/select":
            return httpx.Response(200, json={"selected_receptor": "6CM4"})
        return httpx.Response(404, json={"error": "unexpected"})

    client = DockUPClient("http://dockup.local", transport=httpx.MockTransport(handler))

    assert client.load_receptors("6CM4")["summary"][0]["pdb_id"] == "6CM4"
    assert client.select_receptor("6CM4")["selected_receptor"] == "6CM4"
    assert seen == [
        ("POST", "/api/receptors/load", {"pdb_ids": "6CM4"}),
        ("POST", "/api/receptors/select", {"pdb_id": "6CM4"}),
    ]


def test_live_cli_state_prints_json_envelope(monkeypatch, capsys) -> None:
    class FakeClient:
        def get_state(self) -> dict[str, object]:
            return {"selected_receptor": "6CM4", "queue_count": 1, "run_status": "idle"}

    monkeypatch.setattr(cli, "_live_client", lambda _args: FakeClient())

    code = cli.run_agent_cli(["live", "state", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["ok"] is True
    assert output["action"] == "state.get"
    assert output["data"]["selected_receptor"] == "6CM4"


def test_live_cli_parent_json_flag_is_preserved(monkeypatch, capsys) -> None:
    class FakeClient:
        def get_state(self) -> dict[str, object]:
            return {"selected_receptor": "", "queue_count": 0, "run_status": "idle"}

    monkeypatch.setattr(cli, "_live_client", lambda _args: FakeClient())

    code = cli.run_agent_cli(["live", "--json", "state"])
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["action"] == "state.get"


def test_live_cli_receptor_list_prints_human_summary(monkeypatch, capsys) -> None:
    class FakeClient:
        def list_receptors(self) -> dict[str, object]:
            return {"receptors": [{"pdb_id": "6CM4"}, {"pdb_id": "5MOZ"}]}

    monkeypatch.setattr(cli, "_live_client", lambda _args: FakeClient())

    code = cli.run_agent_cli(["live", "receptor", "list"])

    assert code == 0
    assert capsys.readouterr().out.strip() == "receptors: 2"


def test_live_cli_viewer_show_verifies_receptor_detail(monkeypatch, capsys) -> None:
    class FakeClient:
        def select_receptor(self, pdb_id: str) -> dict[str, object]:
            return {"selected_receptor": pdb_id}

        def get_receptor_detail(self, pdb_id: str, *, chain: str = "") -> dict[str, object]:
            return {
                "pdb_id": pdb_id,
                "pdb_text": "ATOM\nEND\n",
                "chains": ["all", "A"],
                "ligands_by_chain": {"A": ["8NU 2001"]},
                "selected_chain": chain or "all",
            }

    monkeypatch.setattr(cli, "_live_client", lambda _args: FakeClient())

    code = cli.run_agent_cli(["live", "viewer", "show", "6CM4", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["action"] == "viewer.show"
    assert output["data"]["pdb_id"] == "6CM4"
    assert output["data"]["pdb_text_length"] == len("ATOM\nEND\n")
    assert output["ui_hints"]["refresh"] == ["state", "viewer"]
