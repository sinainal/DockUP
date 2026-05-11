from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


def _load_cli_module():
    path = Path(__file__).resolve().parents[1] / "dockup_agent_cli.py"
    spec = importlib.util.spec_from_file_location("dockup_agent_cli", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, payload: dict[str, object]):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_agent_cli_run_uses_ui_run_start_endpoint(monkeypatch, capsys) -> None:
    cli = _load_cli_module()
    seen: list[tuple[str, dict[str, object]]] = []

    def fake_urlopen(request, timeout=30):
        payload = json.loads((request.data or b"{}").decode("utf-8"))
        seen.append((request.full_url, payload))
        return _FakeResponse({"status": "running", "out_root": "/tmp/dock"})

    monkeypatch.setattr(cli, "urlopen", fake_urlopen)

    code = cli.cmd_run(
        SimpleNamespace(
            base_url="http://dockup.local",
            batch_id="42",
            test_mode=False,
            stream=False,
            interval=0.1,
            pretty=False,
        )
    )

    assert code == 0
    assert seen == [("http://dockup.local/api/run/start", {"is_test_mode": False, "batch_id": 42})]
    assert '"status": "running"' in capsys.readouterr().out


def test_agent_cli_state_uses_ui_state_endpoint(monkeypatch, capsys) -> None:
    cli = _load_cli_module()
    seen: list[str] = []

    def fake_urlopen(request, timeout=30):
        seen.append(request.full_url)
        return _FakeResponse({"run_status": "idle", "queue_count": 0})

    monkeypatch.setattr(cli, "urlopen", fake_urlopen)

    code = cli.cmd_state(SimpleNamespace(base_url="http://dockup.local", pretty=False))

    assert code == 0
    assert seen == ["http://dockup.local/api/state"]
    assert '"run_status": "idle"' in capsys.readouterr().out
