from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from ..agent.state_context import docking_state_context, state_system_prompt
from ..config import BASE

EXTENSION_ID = "gemini_agent"
ROOT_DIR = BASE / ".venv" / "dockup_extensions" / EXTENSION_ID
STATE_PATH = ROOT_DIR / "state.json"
DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"
API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
EXTERNAL_KEY_PATHS = (BASE.parent / "gemini_api", BASE.parent / "gemini api")

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


def _seed_api_key() -> str:
    env_key = os.getenv("GEMINI_API_KEY", "").strip()
    if env_key:
        return env_key
    for path in EXTERNAL_KEY_PATHS:
        try:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        except Exception:
            continue
    return ""


def _default_state() -> dict[str, Any]:
    api_key = _seed_api_key()
    return {
        "api_key": api_key,
        "selected_models": _all_model_names() if api_key else [],
        "model": DEFAULT_MODEL,
    }


def _normalize_selected_models(raw: Any) -> list[str]:
    allowed = {item["name"] for item in GEMINI_MODELS}
    values = raw if isinstance(raw, list) else []
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = str(value or "").strip()
        if not name or name in seen or name not in allowed:
            continue
        out.append(name)
        seen.add(name)
    return out


def _normalize_model(value: Any, fallback: str = DEFAULT_MODEL) -> str:
    name = str(value or "").strip()
    allowed = set(_all_model_names())
    if name in allowed:
        return name
    if fallback in allowed:
        return fallback
    return DEFAULT_MODEL


def _read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return _default_state()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _default_state()
    api_key = str(data.get("api_key") or "").strip() or _seed_api_key()
    selected_models = _normalize_selected_models(data.get("selected_models"))
    if api_key and not selected_models:
        selected_models = _all_model_names()
    return {
        "api_key": api_key,
        "selected_models": selected_models,
        "model": _normalize_model(data.get("model"), DEFAULT_MODEL),
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


def _snapshot(state: dict[str, Any]) -> dict[str, Any]:
    api_key = str(state.get("api_key") or "").strip()
    selected_models = _normalize_selected_models(state.get("selected_models"))
    model = _normalize_model(state.get("model"), selected_models[0] if selected_models else DEFAULT_MODEL)
    return {
        "ok": True,
        "api_key_saved": bool(api_key),
        "api_key_present": bool(api_key),
        "selected_models": selected_models,
        "model": model,
        "default_model": DEFAULT_MODEL,
        "models": _model_payload(selected_models),
        "model_count": len(GEMINI_MODELS),
        "error": "",
    }


def status() -> dict[str, Any]:
    state = _read_state()
    _write_state(state)
    return _snapshot(state)


def save(payload: dict[str, Any]) -> dict[str, Any]:
    previous = _read_state()
    api_key = str(payload.get("api_key") or previous.get("api_key") or _seed_api_key() or "").strip()
    selected_models = _normalize_selected_models(payload.get("selected_models"))
    if api_key and not selected_models:
        selected_models = _normalize_selected_models(previous.get("selected_models")) or _all_model_names()
    model = _normalize_model(payload.get("model") or previous.get("model"), selected_models[0] if selected_models else DEFAULT_MODEL)
    state = {
        "api_key": api_key,
        "selected_models": selected_models,
        "model": model,
    }
    _write_state(state)
    return _snapshot(state)


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
