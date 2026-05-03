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
        transport=_transport({("GET", "/api/control/state"): {"ok": True, "action": "state.get", "data": {"selected_receptor": "6CM4", "queue_count": 2}}}),
    )

    payload = client.get_state()

    assert payload["data"]["selected_receptor"] == "6CM4"
    assert payload["data"]["queue_count"] == 2


def test_live_client_reads_run_status_from_ui_endpoint() -> None:
    client = DockUPClient(
        "http://dockup.local",
        transport=_transport({("GET", "/api/control/run/status"): {"ok": True, "action": "run.status", "data": {"status": "idle", "total_runs": 0}}}),
    )

    payload = client.get_run_status()

    assert payload["data"]["status"] == "idle"
    assert payload["data"]["total_runs"] == 0


def test_live_client_calls_report_page_endpoints() -> None:
    seen: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8") or "{}") if request.content else None
        seen.append((request.method, request.url.path, payload))
        if request.url.path == "/api/reports/list":
            assert request.url.params["root_path"] == "data/dock"
            return httpx.Response(200, json={"source_path": "data/dock/run_a", "receptors": []})
        if request.url.path == "/api/reports/render":
            return httpx.Response(200, json={"status": "started", "expected_time": 12})
        if request.url.path == "/api/reports/status":
            return httpx.Response(200, json={"status": "running", "task": "render"})
        return httpx.Response(404, json={"error": "unexpected"})

    client = DockUPClient("http://dockup.local", transport=httpx.MockTransport(handler))

    assert client.list_reports(root_path="data/dock")["source_path"] == "data/dock/run_a"
    assert client.trigger_report_render(root_path="data/dock", source_path="run_a", render_mode="classic")["status"] == "started"
    assert client.get_report_status()["task"] == "render"
    assert seen == [
        ("GET", "/api/reports/list", None),
        ("POST", "/api/reports/render", {"root_path": "data/dock", "source_path": "run_a", "render_mode": "classic"}),
        ("GET", "/api/reports/status", None),
    ]


def test_live_client_loads_and_selects_receptor_through_ui_endpoints() -> None:
    seen: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8") or "{}") if request.content else None
        seen.append((request.method, request.url.path, payload))
        if request.url.path == "/api/control/receptors/load":
            return httpx.Response(200, json={"ok": True, "action": "receptor.load", "data": {"summary": [{"pdb_id": "6CM4"}], "ignored_ids": []}})
        if request.url.path == "/api/control/receptors/select":
            return httpx.Response(200, json={"ok": True, "action": "receptor.select", "data": {"selected_receptor": "6CM4"}})
        return httpx.Response(404, json={"error": "unexpected"})

    client = DockUPClient("http://dockup.local", transport=httpx.MockTransport(handler))

    assert client.load_receptors("6CM4")["data"]["summary"][0]["pdb_id"] == "6CM4"
    assert client.select_receptor("6CM4")["data"]["selected_receptor"] == "6CM4"
    assert seen == [
        ("POST", "/api/control/receptors/load", {"pdb_ids": "6CM4"}),
        ("POST", "/api/control/receptors/select", {"pdb_id": "6CM4"}),
    ]


def test_live_client_covers_assets_viewer_and_results_control_endpoints() -> None:
    seen: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8") or "{}") if request.content else None
        seen.append((request.method, request.url.path, payload))
        if request.url.path == "/api/control/assets/inspect":
            return httpx.Response(200, json={"ok": True, "action": "assets.inspect", "data": {"inventory": {}}})
        if request.url.path == "/api/control/viewer/residues":
            return httpx.Response(200, json={"ok": True, "action": "viewer.residues", "data": {"summary": "Found 8 TRP residue(s)."}})
        if request.url.path == "/api/control/results/folders":
            return httpx.Response(200, json={"ok": True, "action": "results.folders", "data": {"folders": []}})
        if request.url.path == "/api/control/results/scan":
            return httpx.Response(200, json={"ok": True, "action": "results.scan", "data": {"results": []}})
        if request.url.path == "/api/control/results/detail":
            return httpx.Response(200, json={"ok": True, "action": "results.detail", "data": {"result": {"folder_name": "run1"}}})
        return httpx.Response(404, json={"error": "unexpected"})

    client = DockUPClient("http://dockup.local", transport=httpx.MockTransport(handler))

    assert client.inspect_assets()["action"] == "assets.inspect"
    assert client.show_residues("6CM4", residue="TRP", chain="all")["action"] == "viewer.residues"
    assert client.list_result_folders()["action"] == "results.folders"
    assert client.scan_results(root_path="data/dock")["action"] == "results.scan"
    assert client.get_result_detail(result_dir="/tmp/run1")["action"] == "results.detail"
    assert seen == [
        ("GET", "/api/control/assets/inspect", None),
        ("POST", "/api/control/viewer/residues", {"pdb_id": "6CM4", "residue": "TRP", "chain": "all"}),
        ("GET", "/api/control/results/folders", None),
        ("POST", "/api/control/results/scan", {"root_path": "data/dock"}),
        ("POST", "/api/control/results/detail", {"result_dir": "/tmp/run1"}),
    ]


def test_live_cli_state_prints_json_envelope(monkeypatch, capsys) -> None:
    class FakeClient:
        def get_state(self) -> dict[str, object]:
            return {"ok": True, "action": "state.get", "data": {"selected_receptor": "6CM4", "queue_count": 1, "run_status": "idle"}}

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
            return {"ok": True, "action": "state.get", "data": {"selected_receptor": "", "queue_count": 0, "run_status": "idle"}}

    monkeypatch.setattr(cli, "_live_client", lambda _args: FakeClient())

    code = cli.run_agent_cli(["live", "--json", "state"])
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["action"] == "state.get"


def test_live_cli_receptor_list_prints_human_summary(monkeypatch, capsys) -> None:
    class FakeClient:
        def list_receptors(self) -> dict[str, object]:
            return {"ok": True, "action": "receptor.list", "message": "receptors: 2", "data": {"receptors": [{"pdb_id": "6CM4"}, {"pdb_id": "5MOZ"}]}}

    monkeypatch.setattr(cli, "_live_client", lambda _args: FakeClient())

    code = cli.run_agent_cli(["live", "receptor", "list"])

    assert code == 0
    assert capsys.readouterr().out.strip() == "receptors: 2"


def test_live_cli_viewer_show_verifies_receptor_detail(monkeypatch, capsys) -> None:
    class FakeClient:
        def show_viewer(self, pdb_id: str, *, chain: str = "") -> dict[str, object]:
            return {
                "ok": True,
                "action": "viewer.show",
                "message": "viewer ready: 6CM4 (9 pdb chars)",
                "data": {
                    "pdb_id": pdb_id,
                    "pdb_text_length": len("ATOM\nEND\n"),
                    "chains": ["all", "A"],
                    "ligands_by_chain": {"A": ["8NU 2001"]},
                    "selected_chain": chain or "all",
                },
                "ui_hints": {"refresh": ["state", "viewer"]},
            }

    monkeypatch.setattr(cli, "_live_client", lambda _args: FakeClient())

    code = cli.run_agent_cli(["live", "viewer", "show", "6CM4", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["action"] == "viewer.show"
    assert output["data"]["pdb_id"] == "6CM4"
    assert output["data"]["pdb_text_length"] == len("ATOM\nEND\n")
    assert output["ui_hints"]["refresh"] == ["state", "viewer"]


def test_live_cli_assets_viewer_residues_and_results_commands(monkeypatch, capsys) -> None:
    class FakeClient:
        def inspect_assets(self) -> dict[str, object]:
            return {"ok": True, "action": "assets.inspect", "data": {"inventory": {"receptors": {"6CM4": {}}, "ligands": []}}}

        def show_residues(self, pdb_id: str = "", *, residue: str = "TRP", chain: str = "all") -> dict[str, object]:
            return {
                "ok": True,
                "action": "viewer.residues",
                "data": {"summary": "Found 8 TRP residue(s).", "receptor": pdb_id, "residue": residue, "chain": chain, "residues": []},
            }

        def list_result_folders(self) -> dict[str, object]:
            return {"ok": True, "action": "results.folders", "data": {"folders": [{"path": "data/dock"}]}}

        def scan_results(self, *, root_path: str = "data/dock") -> dict[str, object]:
            return {"ok": True, "action": "results.scan", "data": {"root_path": root_path, "results": []}}

        def get_result_detail(self, *, result_dir: str) -> dict[str, object]:
            return {"ok": True, "action": "results.detail", "data": {"result": {"folder_name": result_dir}}}

    monkeypatch.setattr(cli, "_live_client", lambda _args: FakeClient())

    code = cli.run_agent_cli(["live", "assets", "inspect", "--json"])
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["action"] == "assets.inspect"

    code = cli.run_agent_cli(["live", "viewer", "residues", "6CM4", "--residue", "tryptophan", "--json"])
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["action"] == "viewer.residues"
    assert output["data"]["residue"] == "tryptophan"

    code = cli.run_agent_cli(["live", "results", "folders", "--json"])
    assert json.loads(capsys.readouterr().out)["action"] == "results.folders"
    assert code == 0

    code = cli.run_agent_cli(["live", "results", "scan", "--root", "data/dock", "--json"])
    assert json.loads(capsys.readouterr().out)["action"] == "results.scan"
    assert code == 0

    code = cli.run_agent_cli(["live", "results", "detail", "/tmp/run1", "--json"])
    assert json.loads(capsys.readouterr().out)["action"] == "results.detail"
    assert code == 0


def test_live_cli_ligand_commands_use_control_envelopes(monkeypatch, capsys) -> None:
    class FakeClient:
        def list_ligands(self) -> dict[str, object]:
            return {"ok": True, "action": "ligand.list", "message": "ligands: 1", "data": {"ligands": ["dopamine.sdf"]}}

    monkeypatch.setattr(cli, "_live_client", lambda _args: FakeClient())

    code = cli.run_agent_cli(["live", "ligand", "list", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert code == 0
    assert output["action"] == "ligand.list"
    assert output["data"]["ligands"] == ["dopamine.sdf"]


def test_live_cli_queue_and_run_commands_use_control_envelopes(monkeypatch, capsys) -> None:
    class FakeClient:
        def build_queue(self, *, replace_queue: bool = True) -> dict[str, object]:
            return {
                "ok": True,
                "action": "queue.build",
                "message": "queue built: 2 job(s)",
                "data": {"queue_count": 2, "replace_queue": replace_queue},
            }

        def start_run(self, *, test_mode: bool = False, batch_id: int | None = None) -> dict[str, object]:
            return {
                "ok": True,
                "action": "run.start",
                "message": "run status: running",
                "data": {"status": "running", "test_mode": test_mode, "batch_id": batch_id},
            }

    monkeypatch.setattr(cli, "_live_client", lambda _args: FakeClient())

    code = cli.run_agent_cli(["live", "queue", "build", "--append", "--json"])
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["action"] == "queue.build"
    assert output["data"]["replace_queue"] is False

    code = cli.run_agent_cli(["live", "run", "start", "--test-mode", "--json"])
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["action"] == "run.start"
    assert output["data"]["test_mode"] is True


def test_live_cli_report_list_and_status_wrap_ui_payloads(monkeypatch, capsys) -> None:
    class FakeClient:
        def list_reports(self, *, root_path: str = "", source_path: str = "", output_path: str = "", linked_path: str = "") -> dict[str, object]:
            return {
                "root_path": root_path,
                "source_path": source_path or "data/dock/run_a",
                "output_path": output_path or "data/dock/run_a/report_outputs",
                "receptors": [{"id": "6CM4", "ready": True}],
                "images": [{"path": "plot.png"}],
            }

        def get_report_status(self) -> dict[str, object]:
            return {"status": "idle", "task": "", "progress": 0, "total": 0}

    monkeypatch.setattr(cli, "_live_client", lambda _args: FakeClient())

    code = cli.run_agent_cli(["live", "report", "list", "--root", "data/dock", "--json"])
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["action"] == "report.list"
    assert output["data"]["source_path"] == "data/dock/run_a"

    code = cli.run_agent_cli(["live", "report", "status", "--json"])
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["action"] == "report.status"
    assert output["data"]["status"] == "idle"


def test_live_cli_report_render_and_compile_payloads(monkeypatch, capsys) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeClient:
        def trigger_report_render(self, **payload: object) -> dict[str, object]:
            calls.append(("render", dict(payload)))
            return {"status": "started", "expected_time": 12}

        def compile_report(self, **payload: object) -> dict[str, object]:
            calls.append(("compile", dict(payload)))
            return {"status": "completed", "doc_path": "data/dock/run_a/report.docx"}

    monkeypatch.setattr(cli, "_live_client", lambda _args: FakeClient())

    code = cli.run_agent_cli(
        [
            "live",
            "report",
            "render",
            "--source",
            "data/dock/run_a",
            "--mode",
            "otofigure",
            "--receptors",
            "6CM4",
            "5MOZ",
            "--run-by-receptor-json",
            '{"6CM4":"run1"}',
            "--far-ratio",
            "5",
            "--close-padding",
            "0.33",
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["action"] == "report.render"
    assert calls[0][0] == "render"
    assert calls[0][1]["source_path"] == "data/dock/run_a"
    assert calls[0][1]["render_mode"] == "otofigure"
    assert calls[0][1]["receptors"] == ["6CM4", "5MOZ"]
    assert calls[0][1]["otofigure_far_ratio"] == 5
    assert calls[0][1]["otofigure_close_padding"] == 0.33
    assert calls[0][1]["run_by_receptor"] == {"6CM4": "run1"}

    code = cli.run_agent_cli(
        [
            "live",
            "report",
            "compile",
            "--source",
            "data/dock/run_a",
            "--images",
            "plot.png",
            "--captions-json",
            '{"plot.png":"Affinity plot"}',
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["action"] == "report.compile"
    assert calls[1][0] == "compile"
    assert calls[1][1]["selected_images"] == ["plot.png"]
    assert calls[1][1]["figure_captions"] == {"plot.png": "Affinity plot"}
