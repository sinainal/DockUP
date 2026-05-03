from __future__ import annotations


def test_execute_named_tool_prefers_control_wrapper(monkeypatch) -> None:
    from docking_app.extensions import ollama_agent

    calls: list[tuple[str, dict[str, object]]] = []

    def fake_control_tool(**kwargs):
        calls.append(("control", kwargs))
        return {"ok": True, "summary": "control wrapper used"}

    monkeypatch.setitem(ollama_agent.CONTROL_TOOL_FUNCTIONS, "select_workspace", fake_control_tool)
    monkeypatch.setitem(ollama_agent.DOCKING_FUNCTIONS, "select_workspace", lambda **_kwargs: {"ok": False})

    result = ollama_agent._execute_named_tool("select_workspace", {"receptor": "6CM4"}, test_mode=True)

    assert result["ok"] is True
    assert result["summary"] == "control wrapper used"
    assert calls == [("control", {"receptor": "6CM4"})]


def test_execute_named_tool_keeps_legacy_tool_fallback(monkeypatch) -> None:
    from docking_app.extensions import ollama_agent

    monkeypatch.delitem(ollama_agent.CONTROL_TOOL_FUNCTIONS, "fetch_assets", raising=False)
    monkeypatch.setitem(ollama_agent.DOCKING_FUNCTIONS, "fetch_assets", lambda **kwargs: {"ok": True, "summary": kwargs["ligands"]})

    result = ollama_agent._execute_named_tool("fetch_assets", {"ligands": "aspirin"}, test_mode=True)

    assert result == {"ok": True, "summary": "aspirin"}
