from __future__ import annotations

from fastapi.testclient import TestClient

from docking_app.app import create_app


def test_ollama_status_lists_models_as_cards_payload(monkeypatch, tmp_path) -> None:
    from docking_app.agent.ollama_client import OllamaModel
    from docking_app.extensions import ollama_agent

    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(
        ollama_agent,
        "probe_ollama",
        lambda _base_url: (
            True,
            "0.9.0",
            [
                OllamaModel(name="gemma4-26b-q3:latest", size=13),
                OllamaModel(name="qwen36-35b-iq2-emotion:latest", size=11),
            ],
            None,
        ),
    )

    response = TestClient(create_app()).get("/api/extensions/ollama/status")
    payload = response.json()

    assert response.status_code == 200
    assert payload["connected"] is True
    assert payload["settings"]["num_ctx"] == 4096
    assert payload["settings"]["num_batch"] == 128
    assert payload["settings"]["keep_alive"] == -1
    assert payload["think_mode"] == "auto"
    assert payload["model"] == "qwen36-35b-iq2-emotion:latest"
    assert [row["name"] for row in payload["models"]] == [
        "gemma4-26b-q3:latest",
        "qwen36-35b-iq2-emotion:latest",
    ]
    assert payload["state_context"]["docking_config"]["docking_engine"] in {"vina", "vina_gpu_21"}


def test_ollama_chat_includes_state_context(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    captured = {}
    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    def fake_chat(**kwargs):
        captured["kwargs"] = kwargs
        return {"message": {"thinking": "We should inspect the current run state.", "content": "DockUP is idle and ready."}}

    monkeypatch.setattr(ollama_agent, "chat", fake_chat)
    (tmp_path / "state.json").write_text(
        '{"base_url":"http://localhost:11434","model":"qwen36-35b-iq2-emotion:latest","connected":true,"last_error":""}',
        encoding="utf-8",
    )

    response = TestClient(create_app()).post(
        "/api/extensions/ollama/chat",
        json={
            "message": "What is the current state?",
            "think_mode": "no_think",
            "settings": {"num_ctx": 2048, "num_batch": 64, "temperature": 0.1},
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert "DockUP" in captured["kwargs"]["messages"][0]["content"]
    assert "Current DockUP state JSON" in captured["kwargs"]["messages"][1]["content"]
    assert captured["kwargs"]["keep_alive"] == -1
    assert captured["kwargs"]["think"] is False
    assert captured["kwargs"]["options"]["num_ctx"] == 2048
    assert captured["kwargs"]["options"]["num_batch"] == 64
    assert captured["kwargs"]["options"]["temperature"] == 0.1
    assert payload["think_mode"] == "no_think"
    assert payload["thinking"] == "We should inspect the current run state."


def test_ollama_chat_stream_emits_thinking_answer_and_metrics(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    (tmp_path / "state.json").write_text(
        '{"base_url":"http://localhost:11434","model":"qwen36-merged:latest","connected":true,"last_error":""}',
        encoding="utf-8",
    )

    def fake_stream_chat(**_kwargs):
        yield {"message": {"thinking": "Check state."}}
        yield {"message": {"content": "DockUP"}}
        yield {
            "message": {"content": " ready."},
            "done": True,
            "total_duration": 2_000_000_000,
            "eval_duration": 1_000_000_000,
            "eval_count": 20,
        }

    monkeypatch.setattr(ollama_agent, "stream_chat", fake_stream_chat)

    with TestClient(create_app()).stream(
        "POST",
        "/api/extensions/ollama/chat/stream",
        json={"message": "state?", "think_mode": "think"},
    ) as response:
        body = response.read().decode("utf-8")

    assert response.status_code == 200
    assert '"type":"thinking","delta":"Check state."' in body
    assert '"type":"answer","delta":"DockUP"' in body
    assert '"type":"answer","delta":" ready."' in body
    assert '"total_seconds":2.0' in body
    assert '"tokens_per_second":20.0' in body


def test_ollama_chat_stream_routes_think_markup_to_thinking(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    (tmp_path / "state.json").write_text(
        '{"base_url":"http://localhost:11434","model":"qwen36-merged:latest","connected":true,"last_error":""}',
        encoding="utf-8",
    )

    def fake_stream_chat(**_kwargs):
        yield {"message": {"content": "<think>hidden"}}
        yield {"message": {"content": " trace</think>visible"}}
        yield {"done": True, "eval_count": 1, "eval_duration": 1_000_000_000}

    monkeypatch.setattr(ollama_agent, "stream_chat", fake_stream_chat)

    with TestClient(create_app()).stream(
        "POST",
        "/api/extensions/ollama/chat/stream",
        json={"message": "state?", "think_mode": "no_think"},
    ) as response:
        body = response.read().decode("utf-8")

    assert response.status_code == 200
    assert '"type":"thinking","delta":"hidden"' in body
    assert '"type":"thinking","delta":" trace"' in body
    assert '"type":"answer","delta":"visible"' in body
    assert "<think>" not in body
    assert "</think>" not in body


def test_ollama_connect_offloads_previous_model_and_ensures_server(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    calls = {"ensure": 0, "offload": []}
    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(
        ollama_agent,
        "probe_ollama",
        lambda _base_url, timeout_seconds=4.0: (True, "0.9.0", [], None),
    )
    monkeypatch.setattr(ollama_agent, "_ensure_local_server", lambda _base_url: calls.__setitem__("ensure", calls["ensure"] + 1) or "")
    monkeypatch.setattr(ollama_agent, "_offload_model", lambda _base_url, model: calls["offload"].append(model) or "")
    (tmp_path / "state.json").write_text(
        '{"base_url":"http://localhost:11434","model":"gemma4-26b-q3:latest","connected":true,"last_error":""}',
        encoding="utf-8",
    )

    response = TestClient(create_app()).post(
        "/api/extensions/ollama/connect",
        json={
            "base_url": "localhost:11434",
            "model": "qwen36-35b-iq2-emotion:latest",
            "warmup": False,
            "selected_models": ["qwen36-35b-iq2-emotion:latest"],
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert calls["ensure"] == 1
    assert calls["offload"] == ["gemma4-26b-q3:latest"]
    assert payload["model"] == "qwen36-35b-iq2-emotion:latest"
    assert payload["connected"] is True


def test_ollama_connect_without_model_starts_server_and_lists_models_without_warmup(monkeypatch, tmp_path) -> None:
    from docking_app.agent.ollama_client import OllamaModel
    from docking_app.extensions import ollama_agent

    calls = {"ensure": 0, "chat": 0}
    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(ollama_agent, "_ensure_local_server", lambda _base_url: calls.__setitem__("ensure", calls["ensure"] + 1) or "")
    monkeypatch.setattr(ollama_agent, "chat", lambda **_kwargs: calls.__setitem__("chat", calls["chat"] + 1) or {})
    monkeypatch.setattr(
        ollama_agent,
        "probe_ollama",
        lambda _base_url, timeout_seconds=4.0: (
            True,
            "0.9.0",
            [
                OllamaModel(name="gemma4-26b-q3:latest", size=13),
                OllamaModel(name="qwen36-merged:latest", size=14),
            ],
            None,
        ),
    )

    response = TestClient(create_app()).post(
        "/api/extensions/ollama/connect",
        json={"base_url": "localhost:11434", "warmup": False, "load_model": False},
    )
    payload = response.json()

    assert response.status_code == 200
    assert calls == {"ensure": 1, "chat": 0}
    assert payload["connected"] is True
    assert [row["name"] for row in payload["models"]] == ["gemma4-26b-q3:latest", "qwen36-merged:latest"]
    assert payload["job"]["running"] is False


def test_ollama_status_autostarts_after_previous_connect(monkeypatch, tmp_path) -> None:
    from docking_app.agent.ollama_client import OllamaModel
    from docking_app.extensions import ollama_agent

    calls = {"ensure": 0}
    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(ollama_agent, "_ensure_local_server", lambda _base_url: calls.__setitem__("ensure", calls["ensure"] + 1) or "")
    monkeypatch.setattr(
        ollama_agent,
        "probe_ollama",
        lambda _base_url, timeout_seconds=4.0: (
            True,
            "0.9.0",
            [OllamaModel(name="gemma4-26b-q3:latest", size=13)],
            None,
        ),
    )
    (tmp_path / "state.json").write_text(
        '{"base_url":"http://localhost:11434","model":"gemma4-26b-q3:latest","connected":true,"auto_start":true,"last_error":""}',
        encoding="utf-8",
    )

    response = TestClient(create_app()).get("/api/extensions/ollama/status")
    payload = response.json()

    assert response.status_code == 200
    assert calls["ensure"] == 1
    assert payload["connected"] is True
    assert payload["models"][0]["name"] == "gemma4-26b-q3:latest"


def test_ollama_shutdown_route_offloads_and_stops_server(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    calls = {"cleanup": []}
    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(
        ollama_agent,
        "probe_ollama",
        lambda _base_url, timeout_seconds=4.0: (False, None, [], "not reachable"),
    )
    monkeypatch.setattr(
        ollama_agent,
        "_cleanup_managed_ollama",
        lambda base_url, offload=True: calls["cleanup"].append((base_url, offload)) or {"offload_error": "", "shutdown_error": ""},
    )
    (tmp_path / "state.json").write_text(
        '{"base_url":"http://localhost:11434","model":"gemma4-26b-q3:latest","connected":true,"last_error":""}',
        encoding="utf-8",
    )

    response = TestClient(create_app()).post("/api/extensions/ollama/shutdown", json={"offload": True})
    payload = response.json()

    assert response.status_code == 200
    assert calls["cleanup"] == [("http://localhost:11434", True)]
    assert payload["offloaded_model"] == "gemma4-26b-q3:latest"


def test_ensure_local_server_uses_user_ollama_with_models_dir(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    calls = {"probe": 0}
    captured = {}
    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(ollama_agent, "_SERVER_PROC", None)
    monkeypatch.setattr(ollama_agent, "_SERVER_BASE_URL", "")
    monkeypatch.setattr(ollama_agent.shutil, "which", lambda name: "/usr/bin/ollama" if name == "ollama" else None)
    monkeypatch.setattr(ollama_agent, "_ollama_models_dir_for_env", lambda: "/usr/share/ollama/.ollama/models")

    def fake_probe(_base_url, timeout_seconds=4.0):
        calls["probe"] += 1
        return (calls["probe"] > 1, "0.9.0" if calls["probe"] > 1 else None, [], None)

    class FakeProc:
        pid = 12345
        returncode = None

        def poll(self):
            return None

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs.get("env") or {}
        return FakeProc()

    monkeypatch.setattr(ollama_agent, "probe_ollama", fake_probe)
    monkeypatch.setattr(ollama_agent.subprocess, "Popen", fake_popen)

    assert ollama_agent._ensure_local_server("http://localhost:11434") == ""
    assert captured["args"] == ["/usr/bin/ollama", "serve"]
    assert captured["env"]["OLLAMA_HOST"] == "localhost:11434"
    assert captured["env"]["OLLAMA_MODELS"] == "/usr/share/ollama/.ollama/models"


def test_shutdown_local_server_only_stops_dockup_managed_process(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    calls = {"terminate": 0, "wait": 0, "wait_unreachable": 0}
    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)

    class FakeProc:
        pid = 12345
        returncode = None

        def poll(self):
            return None

        def wait(self, timeout=None):
            calls["wait"] += 1
            self.returncode = 0
            return 0

    proc = FakeProc()
    monkeypatch.setattr(ollama_agent, "_SERVER_PROC", proc)
    monkeypatch.setattr(ollama_agent, "_SERVER_BASE_URL", "http://localhost:11434")

    def fake_terminate(_pid, *, sig):
        calls["terminate"] += 1

    def fake_wait_unreachable(_base_url, timeout_seconds=8.0):
        calls["wait_unreachable"] += 1
        return True

    monkeypatch.setattr(ollama_agent, "_terminate_process_group", fake_terminate)
    monkeypatch.setattr(ollama_agent, "_wait_until_unreachable", fake_wait_unreachable)

    assert ollama_agent._shutdown_local_server("http://localhost:11434") == ""
    assert calls == {"terminate": 1, "wait": 1, "wait_unreachable": 1}


def test_app_shutdown_calls_ollama_shutdown(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    calls = {"count": 0}
    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(ollama_agent, "shutdown", lambda payload=None: calls.__setitem__("count", calls["count"] + 1) or {"ok": True})

    with TestClient(create_app()) as client:
        assert client.get("/").status_code == 200

    assert calls["count"] >= 1
