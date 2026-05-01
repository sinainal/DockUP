from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

import httpx

_THINK_BLOCK_RE = re.compile(r"<think>\s*.*?</think>", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class OllamaModel:
    name: str
    size: int | None = None
    modified_at: str | None = None
    digest: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size": self.size,
            "modified_at": self.modified_at,
            "digest": self.digest,
        }


def normalize_base_url(value: str | None, default: str = "http://localhost:11434") -> str:
    normalized = str(value or "").strip() or default
    if "://" not in normalized:
        normalized = f"http://{normalized}"
    return normalized.rstrip("/")


def clean_ollama_text(text: str) -> str:
    cleaned = _THINK_BLOCK_RE.sub("", str(text or ""))
    if "<think>" in cleaned.lower():
        cleaned = re.split(r"<think>", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
    for token in ("<|endoftext|>", "<|im_start|>", "<|im_end|>"):
        cleaned = cleaned.replace(token, "")
    return cleaned.strip()


def probe_ollama(base_url: str, *, timeout_seconds: float = 4.0) -> tuple[bool, str | None, list[OllamaModel], str | None]:
    normalized = normalize_base_url(base_url)
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 3.0))
    try:
        with httpx.Client(base_url=normalized, timeout=timeout, follow_redirects=True) as client:
            version_response = client.get("/api/version")
            version_response.raise_for_status()
            version_payload = version_response.json() or {}
            version = str(version_payload.get("version") or version_payload.get("ollama_version") or "").strip() or None

            models: list[OllamaModel] = []
            tags_error: str | None = None
            try:
                tags_response = client.get("/api/tags")
                tags_response.raise_for_status()
                tags_payload = tags_response.json() or {}
                for item in tags_payload.get("models") or []:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or item.get("model") or "").strip()
                    if not name:
                        continue
                    size_raw = item.get("size")
                    size = int(size_raw) if isinstance(size_raw, (int, float)) else None
                    models.append(
                        OllamaModel(
                            name=name,
                            size=size,
                            modified_at=str(item.get("modified_at") or item.get("modifiedAt") or "").strip() or None,
                            digest=str(item.get("digest") or "").strip() or None,
                        )
                    )
            except Exception as exc:
                tags_error = f"{type(exc).__name__}: {exc}"
            models.sort(key=lambda model: model.name.lower())
            return True, version, models, tags_error
    except Exception as exc:
        return False, None, [], f"{type(exc).__name__}: {exc}"


def chat(
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    keep_alive: str | int | float | None = "10m",
    think: bool | str | None = None,
    options: dict[str, Any] | None = None,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if options:
        payload["options"] = options
    if think is not None:
        payload["think"] = think
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    timeout = httpx.Timeout(timeout_seconds, connect=5.0)
    with httpx.Client(base_url=normalize_base_url(base_url), timeout=timeout, follow_redirects=True) as client:
        response = client.post("/api/chat", json=payload)
        response.raise_for_status()
        data = response.json() or {}
    message = data.get("message")
    if isinstance(message, dict):
        message = dict(message)
        thinking = message.get("thinking")
        if thinking is None and isinstance(data.get("thinking"), str):
            thinking = data.get("thinking")
        message["thinking"] = clean_ollama_text(str(thinking or "")) if thinking is not None else ""
        message["content"] = clean_ollama_text(str(message.get("content") or ""))
        data["message"] = message
    return data


def unload_model(
    *,
    base_url: str,
    model: str,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [],
        "stream": False,
        "keep_alive": 0,
    }
    timeout = httpx.Timeout(timeout_seconds, connect=5.0)
    with httpx.Client(base_url=normalize_base_url(base_url), timeout=timeout, follow_redirects=True) as client:
        response = client.post("/api/chat", json=payload)
        response.raise_for_status()
        return response.json() or {}


def stream_chat(
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    keep_alive: str | int | float | None = "10m",
    think: bool | str | None = None,
    options: dict[str, Any] | None = None,
    timeout_seconds: float = 240.0,
):
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if options:
        payload["options"] = options
    if think is not None:
        payload["think"] = think
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive

    timeout = httpx.Timeout(timeout_seconds, connect=5.0)
    with httpx.Client(base_url=normalize_base_url(base_url), timeout=timeout, follow_redirects=True) as client:
        with client.stream("POST", "/api/chat", json=payload) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                data = json.loads(line.decode("utf-8") if isinstance(line, bytes) else line)
                message = data.get("message")
                if isinstance(message, dict):
                    message = dict(message)
                    if "content" in message:
                        message["content"] = str(message.get("content") or "")
                    if "thinking" in message:
                        message["thinking"] = str(message.get("thinking") or "")
                    data["message"] = message
                yield data
