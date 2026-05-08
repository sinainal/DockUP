from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from ..agent.state_context import docking_state_context, state_system_prompt
from ..config import BASE
from .paths import extension_root, extension_state_path

EXTENSION_ID = "gemini_agent"
ROOT_DIR = extension_root(EXTENSION_ID)
STATE_PATH = extension_state_path(EXTENSION_ID)
DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"
CLI_MODEL = "gemini-cli"
CLI_MODEL_PREFIX = "gemini-cli:"
DEFAULT_CLI_MODEL = f"{CLI_MODEL_PREFIX}{DEFAULT_MODEL}"
API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
EXTERNAL_KEY_PATHS: tuple[Path, ...] = ()

GEMINI_MODELS: tuple[dict[str, str], ...] = (
    {"name": "gemini-2.5-pro", "label": "Gemini 2.5 Pro"},
    {"name": "gemini-2.5-flash", "label": "Gemini 2.5 Flash"},
    {"name": "gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash Lite"},
    {"name": "gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro Preview"},
    {"name": "gemini-3-flash-preview", "label": "Gemini 3 Flash Preview"},
    {"name": "gemini-3.1-flash-lite-preview", "label": "Gemini 3.1 Flash Lite Preview"},
)


def _all_model_names() -> list[str]:
    return [item["name"] for item in GEMINI_MODELS]


def _all_cli_model_names() -> list[str]:
    return [f"{CLI_MODEL_PREFIX}{item['name']}" for item in GEMINI_MODELS]


def _cli_base_model(value: str) -> str:
    raw = str(value or "").strip()
    if raw.startswith(CLI_MODEL_PREFIX):
        raw = raw[len(CLI_MODEL_PREFIX) :]
    if raw == CLI_MODEL:
        raw = DEFAULT_MODEL
    return raw if raw in set(_all_model_names()) else DEFAULT_MODEL


def _node_major_version() -> int | None:
    try:
        completed = subprocess.run(["node", "--version"], check=False, capture_output=True, text=True, timeout=3)
    except Exception:
        return None
    text = (completed.stdout or completed.stderr or "").strip().lstrip("v")
    try:
        return int(text.split(".", 1)[0])
    except (TypeError, ValueError):
        return None


def _normalize_thinking_budget(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if 0 <= parsed <= 32768 else 0


def _detect_gemini_cli() -> dict[str, Any]:
    candidates = [
        os.getenv("GEMINI_CLI", "").strip(),
        "gemini",
        "gemini-cli",
    ]
    seen: set[str] = set()
    # Build environment with nvm node on PATH (lazily, _cli_env may not exist yet during module init)
    try:
        env = _cli_env()
    except Exception:
        env = dict(os.environ)
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        path = shutil.which(candidate, path=env.get("PATH"))
        if not path:
            path = shutil.which(candidate)
        if not path:
            continue
        version = ""
        error = ""
        try:
            completed = subprocess.run(
                [path, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                env=env,
            )
            output = (completed.stdout or completed.stderr or "").strip()
            version = output.splitlines()[0] if output else ""
            if completed.returncode != 0:
                node_major = _node_major_version()
                if "Invalid regular expression flags" in output and node_major is not None and node_major < 20:
                    error = f"Gemini CLI requires Node >=20; current Node is {node_major}. Upgrade Node and reinstall/run gemini again."
                else:
                    error = output or f"Gemini CLI exited with {completed.returncode}."
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        if error:
            return {"available": False, "installed": True, "command": path, "version": version, "error": error}
        return {"available": True, "installed": True, "command": path, "version": version, "error": ""}
    return {"available": False, "installed": False, "command": "", "version": "", "error": "Gemini CLI not found in PATH."}


def _seed_api_key() -> str:
    env_key = os.getenv("GEMINI_API_KEY", "").strip()
    if env_key:
        return env_key
    configured_paths = tuple(
        Path(item).expanduser()
        for item in os.getenv("DOCKUP_GEMINI_API_KEY_FILE", "").split(os.pathsep)
        if item.strip()
    )
    for path in (*EXTERNAL_KEY_PATHS, *configured_paths):
        try:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        except Exception:
            continue
    return ""


def _default_state() -> dict[str, Any]:
    api_key = _seed_api_key()
    cli = _detect_gemini_cli()
    selected_models = _all_model_names() if api_key else []
    if cli["available"]:
        selected_models.extend(_all_cli_model_names())
    return {
        "api_key": api_key,
        "selected_models": selected_models,
        "model": DEFAULT_MODEL,
        "cli_enabled": bool(cli["available"]),
        "cli_command": cli["command"],
        "cli_thinking_budget": 0,
    }


def _normalize_selected_models(raw: Any) -> list[str]:
    allowed = {item["name"] for item in GEMINI_MODELS} | {CLI_MODEL, *_all_cli_model_names()}
    values = raw if isinstance(raw, list) else []
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = str(value or "").strip()
        if name == CLI_MODEL:
            name = DEFAULT_CLI_MODEL
        if not name or name in seen or name not in allowed:
            continue
        out.append(name)
        seen.add(name)
    return out


def _normalize_model(value: Any, fallback: str = DEFAULT_MODEL) -> str:
    name = str(value or "").strip()
    if name == CLI_MODEL:
        name = DEFAULT_CLI_MODEL
    allowed = set(_all_model_names()) | {CLI_MODEL, *_all_cli_model_names()}
    if name in allowed:
        return name
    if fallback in allowed:
        return fallback
    return DEFAULT_MODEL


def _read_state() -> dict[str, Any]:
    cli = _detect_gemini_cli()
    if not STATE_PATH.exists():
        return _default_state()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _default_state()
    api_key = str(data.get("api_key") or "").strip() or _seed_api_key()
    selected_models = _normalize_selected_models(data.get("selected_models"))
    if not cli["available"]:
        selected_models = [name for name in selected_models if name != CLI_MODEL and not name.startswith(CLI_MODEL_PREFIX)]
    if api_key and not selected_models:
        selected_models = _all_model_names()
    cli_enabled = bool(data.get("cli_enabled")) or bool(cli["available"] and CLI_MODEL in selected_models)
    if cli_enabled and cli["available"] and not any(name.startswith(CLI_MODEL_PREFIX) or name == CLI_MODEL for name in selected_models):
        selected_models.extend(_all_cli_model_names())
    return {
        "api_key": api_key,
        "selected_models": selected_models,
        "model": _normalize_model(data.get("model"), DEFAULT_MODEL),
        "cli_enabled": bool(cli_enabled and cli["available"]),
        "cli_command": str(data.get("cli_command") or cli["command"] or "").strip(),
        "cli_thinking_budget": _normalize_thinking_budget(data.get("cli_thinking_budget", 0)),
    }


def _write_state(data: dict[str, Any]) -> None:
    ROOT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _model_payload(selected_models: list[str]) -> list[dict[str, Any]]:
    selected = set(selected_models)
    return [
        {
            "name": item["name"],
            "label": item["label"],
            "selected": item["name"] in selected,
        }
        for item in GEMINI_MODELS
    ]


def _cli_model_payload(selected_models: list[str], cli: dict[str, Any], enabled: bool) -> list[dict[str, Any]]:
    selected = set(selected_models)
    return [
        {
            "name": f"{CLI_MODEL_PREFIX}{item['name']}",
            "label": f"CLI: {item['label']}",
            "selected": f"{CLI_MODEL_PREFIX}{item['name']}" in selected or (item["name"] == DEFAULT_MODEL and CLI_MODEL in selected),
            "available": bool(cli.get("available")),
            "enabled": bool(enabled and cli.get("available")),
            "command": str(cli.get("command") or ""),
            "version": str(cli.get("version") or ""),
            "base_model": item["name"],
        }
        for item in GEMINI_MODELS
    ]


def _snapshot(state: dict[str, Any]) -> dict[str, Any]:
    cli = _detect_gemini_cli()
    api_key = str(state.get("api_key") or "").strip()
    selected_models = _normalize_selected_models(state.get("selected_models"))
    model = _normalize_model(state.get("model"), selected_models[0] if selected_models else DEFAULT_MODEL)
    cli_enabled = bool(state.get("cli_enabled")) and bool(cli["available"])
    return {
        "ok": True,
        "api_key_saved": bool(api_key),
        "api_key_present": bool(api_key),
        "cli_installed": bool(cli.get("installed")),
        "cli_available": bool(cli["available"]),
        "cli_enabled": cli_enabled,
        "cli_command": str(state.get("cli_command") or cli["command"] or ""),
        "cli_version": str(cli.get("version") or ""),
        "cli_thinking_budget": _normalize_thinking_budget(state.get("cli_thinking_budget", 0)),
        "selected_models": selected_models,
        "model": model,
        "default_model": DEFAULT_MODEL,
        "models": _model_payload(selected_models),
        "cli_models": _cli_model_payload(selected_models, cli, cli_enabled),
        "model_count": len(GEMINI_MODELS),
        "error": str(cli.get("error") or "") if cli.get("installed") and not cli.get("available") else ("" if (api_key or cli["available"]) else "Gemini API key not saved and Gemini CLI not found."),
    }


def status() -> dict[str, Any]:
    state = _read_state()
    _write_state(state)
    return _snapshot(state)


def save(payload: dict[str, Any]) -> dict[str, Any]:
    previous = _read_state()
    api_key = str(payload.get("api_key") or previous.get("api_key") or _seed_api_key() or "").strip()
    selected_supplied = "selected_models" in payload
    selected_models = _normalize_selected_models(payload.get("selected_models") if selected_supplied else previous.get("selected_models"))
    cli = _detect_gemini_cli()
    cli_enabled = bool(payload.get("cli_enabled", previous.get("cli_enabled", False))) and bool(cli["available"])
    if cli_enabled and not any(name.startswith(CLI_MODEL_PREFIX) or name == CLI_MODEL for name in selected_models):
        selected_models.extend(_all_cli_model_names())
    if api_key and not selected_models:
        selected_models = _normalize_selected_models(previous.get("selected_models")) or _all_model_names()
    model = _normalize_model(payload.get("model") or previous.get("model"), selected_models[0] if selected_models else DEFAULT_MODEL)
    state = {
        "api_key": api_key,
        "selected_models": selected_models,
        "model": model,
        "cli_enabled": cli_enabled,
        "cli_command": str(payload.get("cli_command") or previous.get("cli_command") or cli["command"] or "").strip(),
        "cli_thinking_budget": _normalize_thinking_budget(payload.get("cli_thinking_budget", previous.get("cli_thinking_budget", 0))),
    }
    _write_state(state)
    return _snapshot(state)


def activate_cli(payload: dict[str, Any]) -> dict[str, Any]:
    previous = _read_state()
    cli = _detect_gemini_cli()
    enabled = bool(payload.get("enabled", True)) and bool(cli["available"])
    selected_models = _normalize_selected_models(previous.get("selected_models"))
    if enabled and not any(name.startswith(CLI_MODEL_PREFIX) or name == CLI_MODEL for name in selected_models):
        selected_models.extend(_all_cli_model_names())
    if not enabled:
        selected_models = [name for name in selected_models if name != CLI_MODEL and not name.startswith(CLI_MODEL_PREFIX)]
    requested_model = str(payload.get("model") or "").strip()
    previous_model = str(previous.get("model") or "").strip()
    cli_fallback = previous_model if previous_model == CLI_MODEL or previous_model.startswith(CLI_MODEL_PREFIX) else DEFAULT_CLI_MODEL
    model = _normalize_model(requested_model or cli_fallback, DEFAULT_CLI_MODEL) if enabled else _normalize_model(previous.get("model"), DEFAULT_MODEL)
    if (model == CLI_MODEL or model.startswith(CLI_MODEL_PREFIX)) and not enabled:
        model = DEFAULT_MODEL
    state = {
        "api_key": str(previous.get("api_key") or "").strip(),
        "selected_models": selected_models,
        "model": model,
        "cli_enabled": enabled,
        "cli_command": str(cli.get("command") or previous.get("cli_command") or "").strip(),
        "cli_thinking_budget": _normalize_thinking_budget(payload.get("cli_thinking_budget", previous.get("cli_thinking_budget", 0))),
    }
    _write_state(state)
    snapshot = _snapshot(state)
    if not cli["available"]:
        snapshot["ok"] = False
        snapshot["error"] = cli["error"]
    return snapshot


def configure_cli_mcp(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    settings_path = Path.home() / ".gemini" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    except Exception:
        settings = {}
    if not isinstance(settings, dict):
        settings = {}
    servers = settings.get("mcpServers") if isinstance(settings.get("mcpServers"), dict) else {}
    launcher = BASE / "scripts" / "dockup_mcp_server.sh"
    python_command = BASE / ".venv" / "bin" / "python"
    if launcher.exists():
        command = str(launcher)
        args = [
            "--base-url",
            str(raw.get("base_url") or "http://127.0.0.1:8000"),
        ]
    else:
        command = str(python_command) if python_command.exists() else "python3"
        args = [
            "-m",
            "docking_app.mcp_server",
            "--base-url",
            str(raw.get("base_url") or "http://127.0.0.1:8000"),
        ]
    servers["dockup-control"] = {
        "command": command,
        "args": args,
        "cwd": str(BASE),
        "timeout": int(raw.get("timeout") or 10000),
    }
    settings["mcpServers"] = servers
    settings_path.write_text(json.dumps(settings, indent=2, sort_keys=True), encoding="utf-8")
    return {"ok": True, "settings_path": str(settings_path), "mcpServers": servers}


def _gemini_contents(message: str, history: list[dict[str, Any]], state_context: dict[str, Any]) -> list[dict[str, Any]]:
    prompt = f"{state_system_prompt()}\n\nCurrent DockUP state JSON:\n{json.dumps(state_context, ensure_ascii=False)}"
    contents: list[dict[str, Any]] = [{"role": "user", "parts": [{"text": prompt}]}]
    for row in history[-8:]:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").strip()
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        contents.append({"role": "model" if role == "assistant" else "user", "parts": [{"text": content}]})
    contents.append({"role": "user", "parts": [{"text": message}]})
    return contents


def _extract_text(data: dict[str, Any]) -> str:
    pieces: list[str] = []
    for candidate in data.get("candidates") or []:
        content = candidate.get("content") if isinstance(candidate, dict) else {}
        for part in (content or {}).get("parts") or []:
            if isinstance(part, dict) and part.get("text"):
                pieces.append(str(part.get("text") or ""))
    return "".join(pieces)


def _usage_metrics(data: dict[str, Any], elapsed: float) -> dict[str, Any]:
    usage = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else {}
    answer_tokens = usage.get("candidatesTokenCount")
    tokens_per_second = None
    if isinstance(answer_tokens, (int, float)) and elapsed > 0:
        tokens_per_second = round(float(answer_tokens) / elapsed, 2)
    return {
        "total_seconds": round(elapsed, 3),
        "prompt_tokens": usage.get("promptTokenCount"),
        "answer_tokens": answer_tokens,
        "tokens_per_second": tokens_per_second,
    }


def _build_request(payload: dict[str, Any]) -> dict[str, Any]:
    state = _read_state()
    model = _normalize_model(payload.get("model") or state.get("model"), state.get("model", DEFAULT_MODEL))
    message = str(payload.get("message") or "").strip()
    history = payload.get("history") if isinstance(payload.get("history"), list) else []
    state_context = docking_state_context()
    return {
        "api_key": str(state.get("api_key") or "").strip(),
        "model": model,
        "message": message,
        "history": history,
        "state_context": state_context,
        "contents": _gemini_contents(message, history, state_context),
    }


def _cli_prompt(message: str, history: list[dict[str, Any]], state_context: dict[str, Any], *, think_mode: str = "auto", thinking_budget: Any = None) -> str:
    mcp_note = (
        "DockUP MCP is configured as the preferred control path. Use MCP tools such as "
        "dockup_state, dockup_assets, dockup_mutate, dockup_queue, and dockup_validate for live actions; "
        "do not mutate DockUP files or STATE directly."
    )
    prompt = f"{state_system_prompt()}\n\n{mcp_note}\n\nCurrent DockUP state JSON:\n{json.dumps(state_context, ensure_ascii=False)}\n\n"
    if think_mode:
        prompt += f"Thinking mode: {think_mode}.\n"
    if thinking_budget not in {None, ""}:
        prompt += f"Requested thinking budget: {thinking_budget} tokens.\n"
    for row in history[-8:]:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "user").strip()
        content = str(row.get("content") or "").strip()
        if content:
            prompt += f"{role}: {content}\n"
    prompt += f"user: {message}\nassistant:"
    return prompt


def _cli_env() -> dict[str, str]:
    """Build an environment for running Gemini CLI with nvm-managed Node."""
    env = dict(os.environ)
    env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
    # Ensure nvm Node >= 20 is on PATH
    nvm_dir = env.get("NVM_DIR") or str(Path.home() / ".nvm")
    nvm_node_bin = Path(nvm_dir) / "versions" / "node"
    if nvm_node_bin.is_dir():
        versions = sorted(nvm_node_bin.iterdir(), reverse=True)
        for v in versions:
            node_bin = v / "bin"
            if node_bin.is_dir():
                try:
                    major = int(v.name.lstrip("v").split(".")[0])
                except (ValueError, IndexError):
                    continue
                if major >= 20:
                    env["PATH"] = f"{node_bin}:{env.get('PATH', '')}"
                    break
    # Avoid GOOGLE_API_KEY / GEMINI_API_KEY conflict
    if env.get("GOOGLE_API_KEY") and env.get("GEMINI_API_KEY"):
        env.pop("GOOGLE_API_KEY", None)
    return env


def _run_cli_once(command: str, prompt: str, model: str = "", *, thinking_budget: Any = None) -> tuple[str, str]:
    base_model = _cli_base_model(model)
    argv = [command, "--skip-trust", "-o", "text"]
    if base_model:
        argv.extend(["-m", base_model])
    argv.extend(["--allowed-mcp-server-names", "dockup-control", "-p", prompt])
    env = _cli_env()
    try:
        completed = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=180, env=env)
    except subprocess.TimeoutExpired:
        return "", "Gemini CLI timed out after 180 seconds."
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}"
    stdout = str(completed.stdout or "").strip()
    stderr = str(completed.stderr or "").strip()
    if completed.returncode == 0 and stdout:
        return stdout, ""
    return "", stderr or stdout or f"Gemini CLI exited with {completed.returncode}."


def stream_cli_ask(payload: dict[str, Any]):
    state = _read_state()
    state_context = docking_state_context()

    def event(row: dict[str, Any]) -> str:
        return json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"

    cli = _detect_gemini_cli()
    command = str(state.get("cli_command") or cli.get("command") or "").strip()
    if not cli["available"] or not command:
        yield event({"type": "error", "error": cli.get("error") or "Gemini CLI not found.", "state_context": state_context})
        return
    if not state.get("cli_enabled"):
        yield event({"type": "error", "error": "Gemini CLI is not enabled in Extensions.", "state_context": state_context})
        return
    message = str(payload.get("message") or "").strip()
    if not message:
        yield event({"type": "error", "error": "Message is empty.", "state_context": state_context})
        return
    history = payload.get("history") if isinstance(payload.get("history"), list) else []
    model = str(payload.get("model") or "").strip()
    think_mode = str(payload.get("think_mode") or "auto")
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    thinking_budget = payload.get("thinking_budget", settings.get("thinking_budget") if isinstance(settings, dict) else None)
    yield event({"type": "start", "model": model or DEFAULT_CLI_MODEL, "provider": "gemini_cli", "think_mode": think_mode})
    started = time.perf_counter()
    answer, error = _run_cli_once(
        command,
        _cli_prompt(message, history, state_context, think_mode=think_mode, thinking_budget=thinking_budget),
        model=model,
        thinking_budget=thinking_budget,
    )
    if error:
        yield event({"type": "error", "error": error, "state_context": state_context})
        return
    yield event({"type": "answer", "delta": answer})
    yield event({"type": "done", "metrics": {"total_seconds": round(time.perf_counter() - started, 3)}, "raw": {}})


def stream_ask(payload: dict[str, Any]):
    request = _build_request(payload)

    def event(row: dict[str, Any]) -> str:
        return json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"

    if not request["api_key"]:
        yield event({"type": "error", "error": "Save a Gemini API key first.", "state_context": request["state_context"]})
        return
    if not request["message"]:
        yield event({"type": "error", "error": "Message is empty.", "state_context": request["state_context"]})
        return

    yield event({"type": "start", "model": request["model"], "provider": "gemini"})
    started = time.perf_counter()
    url = f"{API_BASE_URL}/models/{request['model']}:streamGenerateContent"
    body = {
        "contents": request["contents"],
        "generationConfig": {
            "temperature": 0.2,
            "topP": 0.9,
        },
    }
    try:
        last_data: dict[str, Any] = {}
        emitted = False
        with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0), follow_redirects=True) as client:
            with client.stream("POST", url, params={"key": request["api_key"], "alt": "sse"}, json=body) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    text = line.decode("utf-8") if isinstance(line, bytes) else str(line or "")
                    text = text.strip()
                    if not text or not text.startswith("data:"):
                        continue
                    data = json.loads(text[5:].strip())
                    if isinstance(data, dict):
                        last_data = data
                    delta = _extract_text(data)
                    if delta:
                        emitted = True
                        yield event({"type": "answer", "delta": delta})
        if not emitted:
            answer = "Gemini returned an empty response."
            yield event({"type": "answer", "delta": answer})
        yield event({"type": "done", "metrics": _usage_metrics(last_data, time.perf_counter() - started), "raw": last_data})
    except Exception as exc:
        yield event({"type": "error", "error": f"{type(exc).__name__}: {exc}", "state_context": request["state_context"]})
