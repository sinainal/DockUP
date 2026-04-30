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
