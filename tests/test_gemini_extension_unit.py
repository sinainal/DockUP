from __future__ import annotations

import json

from fastapi.testclient import TestClient

from docking_app.app import create_app


def test_gemini_status_exposes_supported_models(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import gemini_agent

    monkeypatch.setattr(gemini_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(gemini_agent, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(gemini_agent, "EXTERNAL_KEY_PATHS", ())
    monkeypatch.setattr(
        gemini_agent,
        "_detect_gemini_cli",
        lambda: {"available": False, "installed": False, "command": "", "version": "", "error": "Gemini CLI not found in PATH."},
    )

    response = TestClient(create_app()).get("/api/extensions/gemini/status")
    payload = response.json()

    assert response.status_code == 200
    assert payload["api_key_saved"] is False
    assert payload["selected_models"] == []
    assert [row["name"] for row in payload["models"]] == [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview",
    ]


def test_gemini_save_persists_key_and_visibility(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import gemini_agent

    monkeypatch.setattr(gemini_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(gemini_agent, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(gemini_agent, "EXTERNAL_KEY_PATHS", ())
    monkeypatch.setattr(
        gemini_agent,
        "_detect_gemini_cli",
        lambda: {"available": False, "installed": False, "command": "", "version": "", "error": "Gemini CLI not found in PATH."},
    )

    response = TestClient(create_app()).post(
        "/api/extensions/gemini/save",
        json={
            "api_key": "gemini-secret",
            "selected_models": ["gemini-2.5-pro", "gemini-3.1-flash-lite-preview"],
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["api_key_saved"] is True
    assert payload["selected_models"] == ["gemini-2.5-pro", "gemini-3.1-flash-lite-preview"]
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["api_key"] == "gemini-secret"
    assert saved["selected_models"] == ["gemini-2.5-pro", "gemini-3.1-flash-lite-preview"]


def test_gemini_save_defaults_to_all_models_after_key(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import gemini_agent

    monkeypatch.setattr(gemini_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(gemini_agent, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(gemini_agent, "EXTERNAL_KEY_PATHS", ())
    monkeypatch.setattr(
        gemini_agent,
        "_detect_gemini_cli",
        lambda: {"available": False, "installed": False, "command": "", "version": "", "error": "Gemini CLI not found in PATH."},
    )

    response = TestClient(create_app()).post("/api/extensions/gemini/save", json={"api_key": "gemini-secret"})
    payload = response.json()

    assert response.status_code == 200
    assert payload["api_key_saved"] is True
    assert payload["selected_models"] == [row["name"] for row in payload["models"]]


def test_gemini_cli_activate_persists_visible_model(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import gemini_agent

    monkeypatch.setattr(gemini_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(gemini_agent, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(gemini_agent, "EXTERNAL_KEY_PATHS", ())
    monkeypatch.setattr(
        gemini_agent,
        "_detect_gemini_cli",
        lambda: {"available": True, "command": "/usr/bin/gemini", "version": "1.0.0", "error": ""},
    )

    response = TestClient(create_app()).post("/api/extensions/gemini/cli", json={"enabled": True})
    payload = response.json()

    assert response.status_code == 200
    assert payload["cli_available"] is True
    assert payload["cli_enabled"] is True
    assert "gemini-cli:gemini-3.1-flash-lite-preview" in payload["selected_models"]
    assert payload["model"] == "gemini-cli:gemini-3.1-flash-lite-preview"
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["cli_enabled"] is True
    assert "gemini-cli:gemini-3.1-flash-lite-preview" in saved["selected_models"]


def test_gemini_cli_stream_uses_detected_command(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import gemini_agent

    monkeypatch.setattr(gemini_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(gemini_agent, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(gemini_agent, "EXTERNAL_KEY_PATHS", ())
    monkeypatch.setattr(
        gemini_agent,
        "_detect_gemini_cli",
        lambda: {"available": True, "command": "/usr/bin/gemini", "version": "1.0.0", "error": ""},
    )
    (tmp_path / "state.json").write_text(
        json.dumps({"api_key": "", "selected_models": ["gemini-cli:gemini-2.5-flash"], "model": "gemini-cli:gemini-2.5-flash", "cli_enabled": True, "cli_command": "/usr/bin/gemini"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(gemini_agent, "_run_cli_once", lambda command, prompt, model="", thinking_budget=None: ("Gemini CLI ok", ""))

    response = TestClient(create_app()).post(
        "/api/extensions/gemini-cli/chat/stream",
        json={"model": "gemini-cli:gemini-2.5-flash", "message": "state?", "thinking_budget": 1024},
    )
    body = response.text

    assert response.status_code == 200
    assert '"provider":"gemini_cli"' in body
    assert "Gemini CLI ok" in body


def test_ollama_visible_models_route_updates_selection(monkeypatch, tmp_path) -> None:
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
                OllamaModel(name="qwen36-merged:latest", size=13),
                OllamaModel(name="gemma4-26b-q3:latest", size=11),
            ],
            None,
        ),
    )

    response = TestClient(create_app()).post(
        "/api/extensions/ollama/models",
        json={
            "selected_models": ["gemma4-26b-q3:latest"],
            "model": "qwen36-merged:latest",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["selected_models"] == ["gemma4-26b-q3:latest"]
    assert payload["model"] == "qwen36-merged:latest"


def test_ollama_num_ctx_accepts_large_manual_values() -> None:
    from docking_app.extensions import ollama_agent

    assert ollama_agent._normalize_num_ctx(65536) == 65536
    assert ollama_agent._normalize_num_ctx(128000) == 128000
    assert ollama_agent._normalize_num_ctx(131072) == 131072
    assert ollama_agent._normalize_num_ctx(131073) == ollama_agent.DEFAULT_NUM_CTX
