from __future__ import annotations

import json
from pathlib import Path


def test_agent_observer_records_exact_model_turn(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    monkeypatch.setattr(ollama_agent, "chat", lambda **_kwargs: {"message": {"content": "Done."}})
    request = {
        "base_url": "http://localhost:11434",
        "model": "test-model",
        "settings": ollama_agent._default_settings(),
        "think_mode": "no_think",
        "message": "state?",
        "messages": [{"role": "user", "content": "state?"}],
        "state_context": {"queue_count": 0},
    }
    payload = {
        "message": "state?",
        "test_mode": True,
        "agent_observer": True,
        "observer_output_root": str(tmp_path),
        "agent_run_label": "observer-smoke",
    }

    result = ollama_agent._run_single_agent_tool_loop(payload, request)

    assert result["ok"] is True
    run_dir = Path(result["observer_run_dir"])
    assert (run_dir / "run.json").exists()
    assert (run_dir / "summary.json").exists()
    request_payload = json.loads((run_dir / "steps" / "001_model_request.json").read_text(encoding="utf-8"))
    assert request_payload["payload"]["messages"] == request["messages"]
    assert request_payload["payload"]["model"] == "test-model"
    response_payload = json.loads((run_dir / "steps" / "001_model_response.json").read_text(encoding="utf-8"))
    assert response_payload["message"]["content"] == "Done."


def test_agent_observer_is_disabled_by_default(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    monkeypatch.setattr(ollama_agent, "chat", lambda **_kwargs: {"message": {"content": "Done."}})
    request = {
        "base_url": "http://localhost:11434",
        "model": "test-model",
        "settings": ollama_agent._default_settings(),
        "think_mode": "no_think",
        "message": "state?",
        "messages": [{"role": "user", "content": "state?"}],
        "state_context": {},
    }

    result = ollama_agent._run_single_agent_tool_loop({"message": "state?", "observer_output_root": str(tmp_path)}, request)

    assert result["ok"] is True
    assert "observer_run_dir" not in result
    assert not list(tmp_path.iterdir())
