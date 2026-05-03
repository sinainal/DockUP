from __future__ import annotations

from fastapi.testclient import TestClient

from docking_app.agent.state_context import docking_state_context
from docking_app.agent.state_context import state_system_prompt
from docking_app.app import create_app
from docking_app.config import LIGAND_DIR
from docking_app.state import STATE


def _stream_body(rows) -> str:
    return "".join(rows)


def _minimal_5moz_meta() -> dict[str, object]:
    pdb_text = "".join(
        [
            f"HETATM{1:5d}  C1  {'NXL':>3s} {'B'}{308:4d}    {-12.000:8.3f}{21.000:8.3f}{-16.000:8.3f}  1.00 20.00           C\n",
            f"HETATM{2:5d}  C2  {'NXL':>3s} {'B'}{308:4d}    {-14.000:8.3f}{21.500:8.3f}{-16.500:8.3f}  1.00 20.00           C\n",
            "END\n",
        ]
    )
    return {
        "pdb_id": "5MOZ",
        "pdb_file": "",
        "pdb_text": pdb_text,
        "chains": ["all", "B"],
        "ligands_by_chain": {"B": ["NXL 308"], "all": ["NXL 308"]},
        "error": "",
    }


def _minimal_trp_meta() -> dict[str, object]:
    pdb_text = "".join(
        [
            f"ATOM  {1:5d}  N   {'TRP':>3s} {'A'}{12:4d}    {1.000:8.3f}{2.000:8.3f}{3.000:8.3f}  1.00 20.00           N\n",
            f"ATOM  {2:5d}  CA  {'TRP':>3s} {'A'}{12:4d}    {2.000:8.3f}{3.000:8.3f}{4.000:8.3f}  1.00 20.00           C\n",
            f"ATOM  {3:5d}  N   {'ALA':>3s} {'A'}{13:4d}    {5.000:8.3f}{6.000:8.3f}{7.000:8.3f}  1.00 20.00           N\n",
            "END\n",
        ]
    )
    return {
        "pdb_id": "6CM4",
        "pdb_file": "",
        "pdb_text": pdb_text,
        "chains": ["all", "A"],
        "ligands_by_chain": {"all": []},
        "error": "",
    }


def _patch_direct_tool_sequence(monkeypatch, ollama_agent, tool_calls: list[tuple[str, dict[str, object]]], *, job_count: int = 1, total_runs: int = 1, batch_id: str = "42") -> list[tuple[str, dict[str, object], bool]]:
    responses = []
    for name, args in tool_calls:
        responses.append({"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}})
    responses.append({"message": {"content": "Done."}})
    response_iter = iter(responses)
    executed: list[tuple[str, dict[str, object], bool]] = []
    monkeypatch.setattr(ollama_agent, "chat", lambda **_kwargs: next(response_iter))
    stream_response_iter = iter([dict(row) for row in responses])

    def fake_stream_chat(**_kwargs):
        yield next(stream_response_iter)

    monkeypatch.setattr(ollama_agent, "stream_chat", fake_stream_chat)

    def fake_execute(name, args, *, test_mode, progress_callback=None):
        executed.append((name, args, test_mode))
        if name == "build_or_run_queue":
            if progress_callback is not None:
                progress_callback(
                    {
                        "type": "status",
                        "stage": "build_or_run_queue",
                        "delta": f"Queue built; starting real run for batch {batch_id}..." if args.get("action") in {"run_full", "full", "run", "start", "start_run", "real", "real_run", "start_full", "full_run", "production"} else f"Queue action {args.get('action', 'build_test')} ready.",
                    }
                )
                if args.get("action") in {"run_full", "full", "run", "start", "start_run", "real", "real_run", "start_full", "full_run", "production"}:
                    progress_callback({"type": "status", "stage": "run_queue", "delta": f"Run started for batch {batch_id}."})
            return {
                "ok": True,
                "summary": f"Queue action {args.get('action', 'build_test')}: {job_count} job(s), batch {batch_id}",
                "queue": {"batch_id": batch_id, "new_jobs": job_count, "job_count": job_count, "total_runs": total_runs},
                "run": {"started": True, "test_mode": test_mode, "planned_total_runs": total_runs},
            }
        return {"ok": True, "summary": f"{name} ok", "allowed_next_tools": ["build_or_run_queue"]}

    monkeypatch.setattr(ollama_agent, "_execute_named_tool", fake_execute)
    return executed


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


def test_state_system_prompt_advertises_autonomous_docking_tools() -> None:
    prompt = state_system_prompt()

    assert "dockup local ai" in prompt.lower()
    assert "autonomous scientific docking agent" in prompt.lower()
    assert "tool calling is unavailable" not in prompt.lower()
    assert "you cannot run tools" not in prompt.lower()
    assert "do not repeat the same failed attempt" in prompt.lower()
    assert len(prompt) < 900


def test_chat_request_uses_compact_working_memory(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    (tmp_path / "state.json").write_text(
        '{"base_url":"http://localhost:11434","model":"qwen36-merged:latest","connected":true,"last_error":""}',
        encoding="utf-8",
    )

    request = ollama_agent._build_chat_request({"message": "6CM4 aspirin docking başlat"})
    system_memory = request["messages"][1]["content"]

    assert "DockUP working memory:" in system_memory
    assert "Goal:" in system_memory
    assert "Current state:" in system_memory
    assert "Recent attempts:" in system_memory
    assert "Current DockUP state JSON" not in system_memory


def test_agent_state_context_includes_compact_queue_batch_configs() -> None:
    previous_queue = list(STATE.get("queue") or [])
    previous_config = dict(STATE.get("docking_config") or {})
    previous_runs = STATE.get("runs", 1)
    try:
        STATE["docking_config"] = {"docking_engine": "vina", "vina_exhaustiveness": 32}
        STATE["runs"] = 1
        STATE["queue"] = [
            {
                "batch_id": 101,
                "job_type": "Docking",
                "out_root_name": "vina_batch",
                "docking_config": {"docking_engine": "vina", "vina_exhaustiveness": 8},
            },
            {
                "batch_id": 101,
                "job_type": "Docking",
                "out_root_name": "vina_batch",
                "docking_config": {"docking_engine": "vina", "vina_exhaustiveness": 8},
            },
            {
                "batch_id": 202,
                "job_type": "Docking",
                "out_root_name": "gpu_batch",
                "docking_config": {"docking_engine": "vina_gpu_21", "vina_exhaustiveness": 16},
            },
        ]

        context = docking_state_context()

        assert context["queue_count"] == 3
        assert context["queue_job_count"] == 3
        assert context["run_count"] == 1
        assert context["queue_total_runs"] == 3
        assert context["queue_batches"] == [
            {
                "batch_id": "101",
                "job_count": 2,
                "run_count": 1,
                "total_runs": 2,
                "mode": "Docking",
                "out_root_name": "vina_batch",
                "docking_config": {
                    "docking_engine": "vina",
                    "docking_mode": "standard",
                    "ligand_binding_mode": "single",
                    "pdb2pqr_ph": 7.4,
                    "vina_exhaustiveness": 8,
                    "vina_num_modes": None,
                    "vina_energy_range": None,
                    "vina_cpu": None,
                    "vina_seed": None,
                },
            },
            {
                "batch_id": "202",
                "job_count": 1,
                "run_count": 1,
                "total_runs": 1,
                "mode": "Docking",
                "out_root_name": "gpu_batch",
                "docking_config": {
                    "docking_engine": "vina_gpu_21",
                    "docking_mode": "standard",
                    "ligand_binding_mode": "single",
                    "pdb2pqr_ph": 7.4,
                    "vina_exhaustiveness": 16,
                    "vina_num_modes": None,
                    "vina_energy_range": None,
                    "vina_cpu": None,
                    "vina_seed": None,
                },
            },
        ]
    finally:
        STATE["queue"] = previous_queue
        STATE["docking_config"] = previous_config
        STATE["runs"] = previous_runs


def test_agent_state_context_includes_compact_agent_memory() -> None:
    from docking_app.agent.autonomous_docking import AGENT_STATE

    previous_memory = {key: AGENT_STATE.get(key) for key in ("recent_actions", "memory_summary", "last_tool", "last_tool_summary", "last_answer", "last_error", "workflow_stage")}
    try:
        AGENT_STATE["workflow_stage"] = "grid_ready"
        AGENT_STATE["last_tool"] = "set_gridbox"
        AGENT_STATE["last_tool_summary"] = "Gridbox: 1 receptor(s)"
        AGENT_STATE["last_answer"] = "Gridbox placed."
        AGENT_STATE["last_error"] = ""
        AGENT_STATE["memory_summary"] = "set_gridbox: Gridbox: 1 receptor(s)"
        AGENT_STATE["recent_actions"] = [
            {
                "step": 2,
                "kind": "tool",
                "tool": "set_gridbox",
                "summary": "Gridbox: 1 receptor(s)",
                "ok": True,
            }
        ]

        context = docking_state_context()

        assert context["workflow_stage"] == "grid_ready"
        assert "stage=grid_ready" in context["state_summary"]
        assert context["agent_memory"]["workflow_stage"] == "grid_ready"
        assert context["agent_memory"]["last_tool"] == "set_gridbox"
        assert context["agent_memory"]["recent_actions"] == [
            {
                "step": 2,
                "kind": "tool",
                "tool": "set_gridbox",
                "summary": "Gridbox: 1 receptor(s)",
                "ok": True,
            }
        ]
    finally:
        for key, value in previous_memory.items():
            AGENT_STATE[key] = value


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
    assert "DockUP working memory" in captured["kwargs"]["messages"][1]["content"]
    assert "Current DockUP state JSON" not in captured["kwargs"]["messages"][1]["content"]
    assert captured["kwargs"]["keep_alive"] == -1
    assert captured["kwargs"]["think"] is False
    assert captured["kwargs"]["options"]["num_ctx"] == 2048
    assert captured["kwargs"]["options"]["num_batch"] == 64
    assert captured["kwargs"]["options"]["temperature"] == 0.8
    assert captured["kwargs"]["options"]["num_predict"] == 4096
    assert payload["think_mode"] == "no_think"
    assert payload["thinking"] == "We should inspect the current run state."


def test_ollama_chat_strips_thinking_from_followup_history(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    captured = []
    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    (tmp_path / "state.json").write_text(
        '{"base_url":"http://localhost:11434","model":"qwen36-merged:latest","connected":true,"last_error":""}',
        encoding="utf-8",
    )

    responses = iter(
        [
            {
                "message": {
                    "thinking": "Long internal trace that should stay out of history.",
                    "content": "",
                    "tool_calls": [{"function": {"name": "get_dockup_state", "arguments": {}}}],
                }
            },
            {"message": {"content": "Done."}},
        ]
    )

    def fake_chat(**kwargs):
        captured.append(kwargs["messages"])
        return next(responses)

    monkeypatch.setattr(ollama_agent, "chat", fake_chat)
    monkeypatch.setattr(ollama_agent, "_execute_named_tool", lambda name, args, *, test_mode: {"ok": True, "summary": f"{name} ok"})

    response = TestClient(create_app()).post(
        "/api/extensions/ollama/chat",
        json={"message": "state?"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert len(captured) == 2
    assert "thinking" not in captured[1][3]
    assert captured[1][3]["content"] == ""


def test_ollama_chat_stops_on_repeated_text_loop(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    executed = []
    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    (tmp_path / "state.json").write_text(
        '{"base_url":"http://localhost:11434","model":"qwen36-merged:latest","connected":true,"last_error":""}',
        encoding="utf-8",
    )

    repeated_response = {
        "message": {
            "content": "I should inspect the current state.",
            "tool_calls": [{"function": {"name": "get_dockup_state", "arguments": {}}}],
        }
    }
    responses = iter([repeated_response, repeated_response, repeated_response])
    monkeypatch.setattr(ollama_agent, "chat", lambda **_kwargs: next(responses))

    def fake_execute(name, args, *, test_mode):
        executed.append((name, args, test_mode))
        return {"ok": True, "summary": f"{name} ok"}

    monkeypatch.setattr(ollama_agent, "_execute_named_tool", fake_execute)

    response = TestClient(create_app()).post(
        "/api/extensions/ollama/chat",
        json={"message": "state?"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["raw"]["stopped_reason"] == "repeated_text"
    assert len(executed) == 2


def test_ollama_chat_routes_docking_action_to_direct_tools(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    executed = []
    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    (tmp_path / "state.json").write_text(
        '{"base_url":"http://localhost:11434","model":"qwen36-merged:latest","connected":true,"last_error":""}',
        encoding="utf-8",
    )

    responses = iter(
        [
            {"message": {"content": "", "tool_calls": [{"function": {"name": "get_dockup_state", "arguments": {}}}]}},
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "fetch_assets", "arguments": {"receptors": "5MOZ", "ligands": "aspirin"}}}
                    ],
                }
            },
            {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "build_or_run_queue", "arguments": {"action": "build_test"}}}],
                }
            },
            {"message": {"content": "Done."}},
        ]
    )
    monkeypatch.setattr(
        ollama_agent,
        "chat",
        lambda **_kwargs: next(responses),
    )
    def fake_execute(name, args, *, test_mode):
        executed.append((name, args, test_mode))
        if name == "build_or_run_queue":
            return {
                "ok": True,
                "summary": "Queue action build_test: 1 job(s), batch 42",
                "queue": {"batch_id": "42", "new_jobs": 1, "job_count": 1, "total_runs": 1},
                "run": {"started": True, "test_mode": True, "planned_total_runs": 1},
            }
        return {"ok": True, "summary": f"{name} ok", "allowed_next_tools": ["build_or_run_queue"]}

    monkeypatch.setattr(ollama_agent, "_execute_named_tool", fake_execute)

    response = TestClient(create_app()).post(
        "/api/extensions/ollama/chat",
        json={"message": "aspirin ve 5moz dockingi yapar mısın?"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert [row[0] for row in executed] == ["get_dockup_state", "fetch_assets", "build_or_run_queue"]
    assert executed[1][1] == {"receptors": "5MOZ", "ligands": "aspirin"}
    assert payload["answer"] == "Done."
    assert "Docking workflow completed through direct Ollama function calls" not in payload["answer"]


def test_ollama_chat_real_agent_handles_pdb_id_repeated_as_ligand_and_ligand_typo(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    old_state = {key: STATE.get(key) for key in ("receptor_meta", "selection_map", "active_ligands", "queue")}
    aspirin_path = LIGAND_DIR / "aspirin.sdf"
    had_aspirin = aspirin_path.exists()
    old_aspirin = aspirin_path.read_bytes() if had_aspirin else b""
    try:
        STATE["receptor_meta"] = [_minimal_5moz_meta()]
        STATE["selection_map"] = {}
        STATE["active_ligands"] = []
        STATE["queue"] = []
        if not had_aspirin:
            aspirin_path.write_text("aspirin\n  DockUP\n\nM  END\n$$$$\n", encoding="utf-8")

        monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
        (tmp_path / "state.json").write_text(
            '{"base_url":"http://localhost:11434","model":"qwen36-merged:latest","connected":true,"last_error":""}',
            encoding="utf-8",
        )
        responses = iter(
            [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "fetch_assets", "arguments": {"receptors": "5MOZ", "ligands": "Asprin"}}}
                        ],
                    }
                },
                {"message": {"content": "", "tool_calls": [{"function": {"name": "inspect_assets", "arguments": {}}}]}},
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "select_workspace",
                                    "arguments": {
                                        "receptor": "5MOZ",
                                        "chain": "auto",
                                        "native_ligand": "auto",
                                        "dock_ligands": "all",
                                    },
                                }
                            }
                        ],
                    }
                },
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "set_gridbox",
                                    "arguments": {"method": "native_ligand", "size": 20, "padding": 0},
                                }
                            }
                        ],
                    }
                },
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "set_docking_config",
                                    "arguments": {"engine": "vina_gpu_21", "mode": "standard", "run_count": 1},
                                }
                            }
                        ],
                    }
                },
                {
                    "message": {
                        "content": "",
                        "tool_calls": [{"function": {"name": "build_or_run_queue", "arguments": {"action": "build_test"}}}],
                    }
                },
                {"message": {"content": "Done."}},
            ]
        )
        monkeypatch.setattr(
            ollama_agent,
            "chat",
            lambda **_kwargs: next(responses),
        )

        response = TestClient(create_app()).post(
            "/api/extensions/ollama/chat",
            json={"message": "bana 5moz ve asprin dock eder misin"},
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["ok"] is True
        fetch_row = next(row for row in payload["raw"]["trace"] if row.get("tool") == "fetch_assets")
        queue_row = next(row for row in payload["raw"]["trace"] if row.get("tool") == "build_or_run_queue")
        assert fetch_row["result"]["saved_ligands"] == ["aspirin.sdf"]
        assert queue_row["result"]["queue"]["job_count"] == 1
        assert payload["answer"] == "Done."
        assert "Docking workflow completed through direct Ollama function calls" not in payload["answer"]
    finally:
        for key, value in old_state.items():
            STATE[key] = value
        if had_aspirin:
            aspirin_path.write_bytes(old_aspirin)
        elif aspirin_path.exists():
            aspirin_path.unlink()


def test_fetch_assets_supports_ligand_polymer_ranges(monkeypatch) -> None:
    from docking_app.agent import autonomous_docking

    previous_active = list(STATE.get("active_ligands") or [])
    generated = []
    try:
        STATE["active_ligands"] = []
        monkeypatch.setattr(
            autonomous_docking,
            "_fetch_ligand_with_retries",
            lambda name: (f"{name}_monomer.sdf", "", [name, name.replace("_", " ")]),
        )

        def fake_generate(name, count):
            generated.append((name, count))
            return f"{name}_{count}mer.sdf", ""

        monkeypatch.setattr(autonomous_docking, "_generate_oligomer_ligand", fake_generate)

        result = autonomous_docking.fetch_assets(receptors="", ligands="ethylene[1,3,4]")

        assert result["ok"] is True
        assert result["saved_ligands"] == ["ethylene_monomer.sdf", "ethylene_3mer.sdf", "ethylene_4mer.sdf"]
        assert generated == [("ethylene", 3), ("ethylene", 4)]
        assert STATE["active_ligands"] == ["ethylene_monomer.sdf", "ethylene_3mer.sdf", "ethylene_4mer.sdf"]
    finally:
        STATE["active_ligands"] = previous_active


def test_fetch_assets_surfaces_retry_hint_on_failure(monkeypatch) -> None:
    from docking_app.agent import autonomous_docking
    from docking_app.extensions import ollama_agent

    previous_active = list(STATE.get("active_ligands") or [])
    try:
        STATE["active_ligands"] = []
        monkeypatch.setattr(
            autonomous_docking,
            "_fetch_ligand_with_retries",
            lambda name: ("", f"{name}: not found", [name, name.replace("_", " ")]),
        )

        result = autonomous_docking.fetch_assets(receptors="", ligands="Asprin")
        compact = ollama_agent._tool_context_result("fetch_assets", result)

        assert result["ok"] is False
        assert "Retry once" in result["retry_hint"]
        assert "retry once" in result["summary"].lower()
        assert compact["retry_hint"] == result["retry_hint"]
        assert "retry once" in compact["summary"].lower()
    finally:
        STATE["active_ligands"] = previous_active


def test_agent_runtime_skips_repeated_failed_attempt(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    executed: list[tuple[str, dict[str, object], bool]] = []
    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    (tmp_path / "state.json").write_text(
        '{"base_url":"http://localhost:11434","model":"qwen36-merged:latest","connected":true,"last_error":""}',
        encoding="utf-8",
    )
    responses = iter(
        [
            {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "fetch_assets", "arguments": {"receptors": "", "ligands": "asprin"}}}],
                }
            },
            {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "fetch_assets", "arguments": {"receptors": "", "ligands": "asprin"}}}],
                }
            },
            {"message": {"content": "Hangi ligand adını denememi istersin?"}},
        ]
    )
    monkeypatch.setattr(ollama_agent, "chat", lambda **_kwargs: next(responses))

    def fake_execute(name, args, *, test_mode):
        executed.append((name, args, test_mode))
        return {
            "ok": False,
            "summary": "Loaded 0 receptor(s), saved 0 ligand file(s). Failed 0 receptor(s), 1 ligand(s).",
            "error": "asprin: not found",
            "failed_ligands": ["asprin: not found"],
            "retry_attempts": ["asprin"],
        }

    monkeypatch.setattr(ollama_agent, "_execute_named_tool", fake_execute)

    response = TestClient(create_app()).post(
        "/api/extensions/ollama/chat",
        json={"message": "asprin çek"},
    )
    payload = response.json()
    fetch_rows = [row for row in payload["raw"]["trace"] if row.get("tool") == "fetch_assets"]

    assert response.status_code == 200
    assert payload["ok"] is True
    assert len(executed) == 1
    assert len(fetch_rows) == 2
    assert fetch_rows[1]["result"]["summary"] == "fetch_assets skipped: repeated failed attempt."


def test_show_residues_selects_viewer_and_returns_ngl_selection() -> None:
    from docking_app.agent import autonomous_docking

    old_state = {key: STATE.get(key) for key in ("receptor_meta", "selection_map", "selected_receptor", "selected_chain", "selected_ligand")}
    try:
        STATE["receptor_meta"] = [_minimal_trp_meta()]
        STATE["selection_map"] = {"6CM4": {"chain": "all", "ligand_resname": "", "ligand_resnames": []}}
        result = autonomous_docking.show_residues(receptor="6CM4", residue="tryptophan", chain="A")

        assert result["ok"] is True
        assert result["summary"] == "Found 1 TRP residue(s) in 6CM4."
        assert result["selection"] == "12:A"
        assert result["viewer_selection"]["selection"] == "12:A"
        assert STATE["selected_receptor"] == "6CM4"
        assert STATE["selected_chain"] == "A"
    finally:
        for key, value in old_state.items():
            STATE[key] = value


def test_ollama_stream_routes_docking_action_to_direct_tools(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    (tmp_path / "state.json").write_text(
        '{"base_url":"http://localhost:11434","model":"qwen36-merged:latest","connected":true,"last_error":""}',
        encoding="utf-8",
    )
    executed = _patch_direct_tool_sequence(
        monkeypatch,
        ollama_agent,
        [
            ("fetch_assets", {"receptors": "5MOZ,6CM4,7X2F", "ligands": "ethylene;propylene"}),
            ("build_or_run_queue", {"action": "build_test"}),
        ],
        job_count=6,
        total_runs=12,
        batch_id="99",
    )

    body = _stream_body(
        ollama_agent.stream_ask(
            {
                "model": "qwen36-merged:latest",
                "message": "5moz 6cm4 ve 7x2f için ethylene ve propylene dock et bakalım.",
            }
        )
    )

    assert '"type":"tool_call","tool":"fetch_assets"' in body
    assert '"type":"tool_call","tool":"build_or_run_queue"' in body
    assert '"type":"answer","delta":"Done."' in body
    assert "Docking workflow completed through direct Ollama function calls" not in body
    assert '"type":"done"' in body
    assert [row[0] for row in executed] == ["fetch_assets", "build_or_run_queue"]


def test_ollama_stream_shows_direct_tool_progress(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    (tmp_path / "state.json").write_text(
        '{"base_url":"http://localhost:11434","model":"qwen36-merged:latest","connected":true,"last_error":""}',
        encoding="utf-8",
    )
    _patch_direct_tool_sequence(
        monkeypatch,
        ollama_agent,
        [
            ("fetch_assets", {"receptors": "6CM4", "ligands": "ethylene;propylene"}),
            ("set_docking_config", {"engine": "vina_gpu_21", "mode": "standard", "run_count": 1}),
            ("build_or_run_queue", {"action": "build_test"}),
        ],
        job_count=2,
        total_runs=2,
        batch_id="77",
    )

    body = _stream_body(
        ollama_agent.stream_ask(
            {
                "model": "qwen36-merged:latest",
                "message": "6cm4 ile ethylene ve propylene dock eder misin",
            }
        )
    )

    assert '"type":"tool_call","tool":"fetch_assets"' in body
    assert '"type":"status","delta":"fetch_assets ok","stage":"fetch_assets"' in body
    assert '"type":"tool_call","tool":"set_docking_config"' in body
    assert '"type":"status","delta":"set_docking_config ok","stage":"set_docking_config"' in body
    assert '"type":"answer","delta":"Done."' in body
    assert "Docking workflow completed through direct Ollama function calls" not in body
    assert '"type":"done"' in body


def test_ollama_stream_forwards_direct_tool_results(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    (tmp_path / "state.json").write_text(
        '{"base_url":"http://localhost:11434","model":"qwen36-merged:latest","connected":true,"last_error":""}',
        encoding="utf-8",
    )
    _patch_direct_tool_sequence(
        monkeypatch,
        ollama_agent,
        [
            ("get_dockup_state", {}),
            ("fetch_assets", {"receptors": "5MOZ", "ligands": "aspirin"}),
            ("build_or_run_queue", {"action": "build_test"}),
        ],
        job_count=1,
        total_runs=1,
        batch_id="101",
    )

    body = _stream_body(
        ollama_agent.stream_ask(
            {
                "model": "qwen36-merged:latest",
                "message": "5moz aspirin dock et",
            }
        )
    )

    assert '"type":"status","delta":"get_dockup_state ok","stage":"get_dockup_state"' in body
    assert '"type":"status","delta":"fetch_assets ok","stage":"fetch_assets"' in body
    assert '"type":"answer","delta":"Done."' in body
    assert "Docking workflow completed through direct Ollama function calls" not in body


def test_ollama_stream_surfaces_run_start_status(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    (tmp_path / "state.json").write_text(
        '{"base_url":"http://localhost:11434","model":"qwen36-merged:latest","connected":true,"last_error":""}',
        encoding="utf-8",
    )
    _patch_direct_tool_sequence(
        monkeypatch,
        ollama_agent,
        [
            ("fetch_assets", {"receptors": "5MOZ", "ligands": "aspirin"}),
            ("build_or_run_queue", {"action": "run_full"}),
        ],
        job_count=1,
        total_runs=1,
        batch_id="202",
    )

    body = _stream_body(
        ollama_agent.stream_ask(
            {
                "model": "qwen36-merged:latest",
                "message": "5moz ve aspirin için gerçek docking run başlat",
            }
        )
    )

    assert '"type":"tool_call","tool":"build_or_run_queue"' in body
    assert '"type":"status"' in body
    assert '"stage":"build_or_run_queue"' in body
    assert '"stage":"run_queue"' in body
    assert '"Run started for batch 202."' in body


def test_ollama_stream_routes_confirmation_when_history_has_docking_action(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    (tmp_path / "state.json").write_text(
        '{"base_url":"http://localhost:11434","model":"qwen36-merged:latest","connected":true,"last_error":""}',
        encoding="utf-8",
    )
    executed = _patch_direct_tool_sequence(
        monkeypatch,
        ollama_agent,
        [
            ("fetch_assets", {"receptors": "5MOZ", "ligands": "aspirin"}),
            ("build_or_run_queue", {"action": "build_test"}),
        ],
        job_count=1,
        total_runs=1,
        batch_id="100",
    )

    body = _stream_body(
        ollama_agent.stream_ask(
            {
                "model": "qwen36-merged:latest",
                "message": "evet çalıştır",
                "history": [
                    {"role": "user", "content": "5moz için aspirin docking yap"},
                    {"role": "assistant", "content": "Onaylıyor musun?"},
                ],
            }
        )
    )

    assert '"type":"answer","delta":"Done."' in body
    assert "Docking workflow completed through direct Ollama function calls" not in body
    assert executed[0][1] == {"receptors": "5MOZ", "ligands": "aspirin"}


def test_ollama_chat_stream_emits_thinking_answer_and_metrics(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    (tmp_path / "state.json").write_text(
        '{"base_url":"http://localhost:11434","model":"qwen36-merged:latest","connected":true,"last_error":""}',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        ollama_agent,
        "chat",
        lambda **_kwargs: {"message": {"thinking": "Check state.", "content": "DockUP ready."}},
    )
    monkeypatch.setattr(
        ollama_agent,
        "stream_chat",
        lambda **_kwargs: iter([
            {"message": {"thinking": "Check state."}},
            {"message": {"content": "DockUP"}},
            {"message": {"content": " ready."}},
        ]),
    )

    body = _stream_body(
        ollama_agent.stream_ask({"model": "qwen36-merged:latest", "message": "state?", "think_mode": "think"})
    )

    assert '"type":"thinking","delta":"Check state."' in body
    assert '"type":"answer","delta":"DockUP"' in body
    assert '"type":"answer","delta":" ready."' in body
    assert '"total_seconds":' in body


def test_ollama_chat_stream_routes_think_markup_to_thinking(monkeypatch, tmp_path) -> None:
    from docking_app.extensions import ollama_agent

    monkeypatch.setattr(ollama_agent, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(ollama_agent, "ROOT_DIR", tmp_path)
    (tmp_path / "state.json").write_text(
        '{"base_url":"http://localhost:11434","model":"qwen36-merged:latest","connected":true,"last_error":""}',
        encoding="utf-8",
    )

    monkeypatch.setattr(
        ollama_agent,
        "chat",
        lambda **_kwargs: {"message": {"thinking": "hidden trace", "content": "visible"}},
    )
    monkeypatch.setattr(
        ollama_agent,
        "stream_chat",
        lambda **_kwargs: iter([
            {"message": {"content": "<think>hidden"}},
            {"message": {"content": " trace</think>visible"}},
        ]),
    )

    body = _stream_body(
        ollama_agent.stream_ask({"model": "qwen36-merged:latest", "message": "state?", "think_mode": "no_think"})
    )

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
