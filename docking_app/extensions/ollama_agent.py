from __future__ import annotations

import json
import math
import os
import re
import shutil
import threading
import time
import subprocess
import signal
from queue import Queue
from typing import Any
from pathlib import Path
from urllib.parse import urlparse

from ..agent.autonomous_docking import AGENT_STATE, AVAILABLE_FUNCTIONS as DOCKING_FUNCTIONS, TOOLS as DOCKING_TOOLS
from ..agent.ollama_client import chat, normalize_base_url, probe_ollama, running_models, stream_chat, unload_model
from ..agent.agent_runtime import (
    build_agent_working_memory,
    record_attempt,
    verify_tool_effect,
    was_failed_attempt,
)
from ..agent.observe import observer_from_payload
from ..agent.state_context import docking_state_context, state_system_prompt
from ..agent.tools import CONTROL_TOOL_FUNCTIONS
from ..config import BASE
from .paths import extension_root, extension_state_path

EXTENSION_ID = "ollama_agent"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_NUM_CTX = 4096
NUM_CTX_MIN = 1024
NUM_CTX_MAX = 131072
DEFAULT_NUM_BATCH = 128
DEFAULT_KEEP_ALIVE = -1
DEFAULT_NUM_GPU = -1
DEFAULT_WARMUP_TOKENS = 1
DEFAULT_THINK_MODE = "auto"
AGENT_TEMPERATURE = 0.8
NUM_CTX_CHOICES = (1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072)
NUM_BATCH_CHOICES = (64, 128, 256, 512)
KEEP_ALIVE_CHOICES = (-1, 300, 900, 1800, 3600)
NUM_GPU_CHOICES = (-1, 40, 48, 56, 64)
WARMUP_TOKEN_CHOICES = (1, 2, 4, 8)
THINK_MODE_CHOICES = ("auto", "think", "no_think")
PREFERRED_MODEL_PATTERNS = ("qwen36-merged", "qwen36_merged", "merged", "qwen36", "qwen3.6", "35b", "iq3_xs", "iq3-xs")

ROOT_DIR = extension_root(EXTENSION_ID)
STATE_PATH = extension_state_path(EXTENSION_ID)

_LOCK = threading.Lock()
_SERVER_LOCK = threading.Lock()
_SERVER_PROC: subprocess.Popen[str] | None = None
_SERVER_BASE_URL = ""
_WARMUP_JOB: dict[str, Any] = {
    "running": False,
    "message": "",
    "model": "",
    "think_mode": DEFAULT_THINK_MODE,
    "error": "",
    "started_at": None,
    "finished_at": None,
}

_WARMUP_TOKEN = 0


def _normalize_num_ctx(value: Any, default: int = DEFAULT_NUM_CTX) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if NUM_CTX_MIN <= parsed <= NUM_CTX_MAX:
        return parsed
    return default


def _normalize_selected_models(raw: Any, model_names: list[str], fallback: str = "") -> list[str]:
    selected_source = raw if isinstance(raw, list) else []
    selected: list[str] = []
    seen: set[str] = set()
    allowed = set(model_names)
    for value in selected_source:
        name = str(value or "").strip()
        if not name or name in seen or name not in allowed:
            continue
        selected.append(name)
        seen.add(name)
    if not selected:
        if fallback and fallback in allowed:
            return [fallback]
        if model_names:
            return [model_names[0]]
    return selected


def _normalize_choice(value: Any, choices: tuple[int, ...], default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed in choices else default


def _normalize_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum or parsed > maximum:
        return default
    return round(parsed, 3)


def _normalize_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _normalize_think_mode(value: Any, default: str = DEFAULT_THINK_MODE) -> str:
    if isinstance(value, bool):
        return "think" if value else "no_think"
    if value is None:
        return default
    normalized = str(value).strip().lower().replace("-", "_")
    if normalized in THINK_MODE_CHOICES:
        return normalized
    if normalized in {"nothink", "no think"}:
        return "no_think"
    return default


def _default_settings() -> dict[str, Any]:
    return {
        "num_ctx": DEFAULT_NUM_CTX,
        "num_batch": DEFAULT_NUM_BATCH,
        "keep_alive": DEFAULT_KEEP_ALIVE,
        "num_gpu": DEFAULT_NUM_GPU,
        "use_mmap": True,
        "temperature": 0.2,
        "top_p": 0.9,
        "repeat_penalty": 1.05,
        "warmup_tokens": DEFAULT_WARMUP_TOKENS,
    }


def _normalize_settings(data: dict[str, Any] | None = None) -> dict[str, Any]:
    source = data or {}
    defaults = _default_settings()
    return {
        "num_ctx": _normalize_num_ctx(source.get("num_ctx"), defaults["num_ctx"]),
        "num_batch": _normalize_choice(source.get("num_batch"), NUM_BATCH_CHOICES, defaults["num_batch"]),
        "keep_alive": _normalize_choice(source.get("keep_alive"), KEEP_ALIVE_CHOICES, defaults["keep_alive"]),
        "num_gpu": _normalize_choice(source.get("num_gpu"), NUM_GPU_CHOICES, defaults["num_gpu"]),
        "use_mmap": _normalize_bool(source.get("use_mmap"), defaults["use_mmap"]),
        "temperature": _normalize_float(source.get("temperature"), defaults["temperature"], 0.0, 2.0),
        "top_p": _normalize_float(source.get("top_p"), defaults["top_p"], 0.05, 1.0),
        "repeat_penalty": _normalize_float(source.get("repeat_penalty"), defaults["repeat_penalty"], 0.8, 1.4),
        "warmup_tokens": _normalize_choice(source.get("warmup_tokens"), WARMUP_TOKEN_CHOICES, defaults["warmup_tokens"]),
    }


def _settings_from_payload(payload: dict[str, Any], fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = _normalize_settings(fallback)
    raw_settings = payload.get("settings")
    if isinstance(raw_settings, dict):
        merged.update(raw_settings)
    for key in merged:
        if key in payload:
            merged[key] = payload[key]
    return _normalize_settings(merged)


def _think_flag(mode: Any) -> bool | None:
    normalized = _normalize_think_mode(mode)
    if normalized == "auto":
        return None
    return normalized == "think"


def _agent_num_predict(num_ctx: Any) -> int:
    try:
        ctx = int(num_ctx)
    except (TypeError, ValueError):
        ctx = DEFAULT_NUM_CTX
    return max(1024, ctx // 2)


def _ollama_options(settings: dict[str, Any], *, warmup: bool = False) -> dict[str, Any]:
    options: dict[str, Any] = {
        "num_ctx": settings["num_ctx"],
        "num_batch": settings["num_batch"],
        "num_gpu": settings["num_gpu"],
        "use_mmap": settings["use_mmap"],
    }
    if warmup:
        options["num_predict"] = settings["warmup_tokens"]
    else:
        options.update(
            {
                "temperature": settings["temperature"],
                "top_p": settings["top_p"],
                "repeat_penalty": settings["repeat_penalty"],
            }
        )
    return options


def _read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {
            "base_url": DEFAULT_BASE_URL,
            "model": "",
            "settings": _default_settings(),
            "think_mode": DEFAULT_THINK_MODE,
            "selected_models": [],
            "connected": False,
            "auto_start": False,
            "last_error": "",
        }
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {
            "base_url": DEFAULT_BASE_URL,
            "model": "",
            "settings": _default_settings(),
            "think_mode": DEFAULT_THINK_MODE,
            "selected_models": [],
            "connected": False,
            "auto_start": False,
            "last_error": "",
        }
    settings_source = data.get("settings") if isinstance(data.get("settings"), dict) else data
    return {
        "base_url": normalize_base_url(data.get("base_url"), DEFAULT_BASE_URL),
        "model": str(data.get("model") or "").strip(),
        "settings": _normalize_settings(settings_source),
        "think_mode": _normalize_think_mode(data.get("think_mode"), DEFAULT_THINK_MODE),
        "selected_models": [str(item or "").strip() for item in (data.get("selected_models") or []) if str(item or "").strip()],
        "connected": bool(data.get("connected")),
        "auto_start": _normalize_bool(data.get("auto_start"), bool(data.get("connected"))),
        "last_error": str(data.get("last_error") or "").strip(),
    }


def _write_state(data: dict[str, Any]) -> None:
    ROOT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _model_score(name: str) -> int:
    lowered = name.lower()
    score = 0
    if "merged" in lowered:
        score += 80
    for index, pattern in enumerate(PREFERRED_MODEL_PATTERNS):
        if pattern in lowered:
            score += 20 - index
    if "qwen" in lowered:
        score += 6
    if "35b" in lowered:
        score += 5
    if "unsloth" in lowered:
        score += 4
    return score


def _preferred_model(models: list[dict[str, Any]], current: str = "") -> str:
    names = [str(item.get("name") or "").strip() for item in models if str(item.get("name") or "").strip()]
    if current and current in names:
        return current
    if not names:
        return current
    return sorted(names, key=lambda name: (-_model_score(name), name.lower()))[0]


def _snapshot(
    base_url: str | None = None,
    model: str | None = None,
    selected_models: list[str] | None = None,
    *,
    ensure_server: bool = False,
) -> dict[str, Any]:
    saved = _read_state()
    target_base_url = normalize_base_url(base_url or saved.get("base_url"), DEFAULT_BASE_URL)
    server_error = ""
    if ensure_server:
        server_error = _ensure_local_server(target_base_url)
    connected, version, model_rows, error = probe_ollama(target_base_url)
    models = [item.as_dict() for item in model_rows]
    model_names = [str(item.get("name") or "").strip() for item in models if str(item.get("name") or "").strip()]
    selected = str(model if model is not None else saved.get("model") or "").strip()
    selected = _preferred_model(models, selected)
    settings = _normalize_settings(saved.get("settings"))
    think_mode = _normalize_think_mode(saved.get("think_mode"), DEFAULT_THINK_MODE)
    visible_models = _normalize_selected_models(
        selected_models if selected_models is not None else saved.get("selected_models"),
        model_names,
        selected,
    )
    state = {
        "base_url": target_base_url,
        "model": selected,
        "settings": settings,
        "think_mode": think_mode,
        "selected_models": visible_models,
        "connected": connected,
        "auto_start": bool(saved.get("auto_start")),
        "last_error": "" if connected and not server_error else (server_error or error or saved.get("last_error") or ""),
    }
    _write_state(state)
    with _LOCK:
        job = dict(_WARMUP_JOB)
    return {
        "ok": connected,
        "connected": connected,
        "base_url": target_base_url,
        "version": version,
        "model": selected,
        "num_ctx": settings["num_ctx"],
        "agent_num_predict": _agent_num_predict(settings["num_ctx"]),
        "settings": settings,
        "think_mode": think_mode,
        "selected_models": visible_models,
        "think_mode_choices": list(THINK_MODE_CHOICES),
        "num_ctx_choices": list(NUM_CTX_CHOICES),
        "num_ctx_min": NUM_CTX_MIN,
        "num_ctx_max": NUM_CTX_MAX,
        "num_batch_choices": list(NUM_BATCH_CHOICES),
        "keep_alive_choices": list(KEEP_ALIVE_CHOICES),
        "num_gpu_choices": list(NUM_GPU_CHOICES),
        "warmup_token_choices": list(WARMUP_TOKEN_CHOICES),
        "models": models,
        "error": server_error or error,
        "job": job,
        "state_context": docking_state_context(),
    }


def status() -> dict[str, Any]:
    saved = _read_state()
    return _snapshot(ensure_server=bool(saved.get("auto_start")))


def _is_local_base_url(base_url: str) -> bool:
    parsed = urlparse(normalize_base_url(base_url))
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _ollama_host_for_env(base_url: str) -> str:
    parsed = urlparse(normalize_base_url(base_url))
    return parsed.netloc or "localhost:11434"


def _stop_model(base_url: str, model: str) -> str:
    model_name = str(model or "").strip()
    if not model_name or not _is_local_base_url(base_url):
        return ""
    executable = shutil.which("ollama")
    if not executable:
        return "ollama executable not found; previous model could not be stopped"
    env = os.environ.copy()
    env["OLLAMA_HOST"] = _ollama_host_for_env(base_url)
    try:
        completed = subprocess.run(
            [executable, "stop", model_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    if completed.returncode not in {0, 1}:
        return f"ollama stop exited with code {completed.returncode}"
    return ""


def _offload_model(base_url: str, model: str) -> str:
    model_name = str(model or "").strip()
    if not model_name or not _is_local_base_url(base_url):
        return ""
    try:
        unload_model(base_url=base_url, model=model_name, timeout_seconds=60.0)
        return ""
    except Exception as exc:
        stop_error = _stop_model(base_url, model_name)
        if stop_error:
            return f"{type(exc).__name__}: {exc}; {stop_error}"
        return ""


def _running_model_names(base_url: str) -> list[str]:
    if not _is_local_base_url(base_url):
        return []
    try:
        return running_models(base_url, timeout_seconds=5.0)
    except Exception:
        return []


def _offload_running_models(base_url: str, preferred_model: str = "") -> str:
    model_names: list[str] = []
    seen: set[str] = set()
    for name in [preferred_model, *_running_model_names(base_url)]:
        model_name = str(name or "").strip()
        if not model_name or model_name in seen:
            continue
        model_names.append(model_name)
        seen.add(model_name)
    errors = []
    for model_name in model_names:
        error = _offload_model(base_url, model_name)
        if error:
            errors.append(f"{model_name}: {error}")
    return "; ".join(errors)


def _local_ollama_serve_pids() -> list[int]:
    try:
        completed = subprocess.run(
            ["ps", "-eo", "pid=,comm=,args="],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return []
    if completed.returncode != 0:
        return []
    current_pid = os.getpid()
    pids: list[int] = []
    for line in completed.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        command = parts[1]
        args = parts[2]
        if pid == current_pid:
            continue
        if command != "ollama":
            continue
        if "ollama serve" not in args:
            continue
        pids.append(pid)
    return sorted(set(pids))


def _terminate_process_group(pid: int, *, sig: signal.Signals) -> None:
    try:
        os.killpg(os.getpgid(pid), sig)
    except Exception:
        try:
            os.kill(pid, sig)
        except Exception:
            pass


def _wait_until_reachable(base_url: str, timeout_seconds: float = 12.0) -> bool:
    deadline = time.time() + max(1.0, float(timeout_seconds))
    while time.time() < deadline:
        connected, _, _, _ = probe_ollama(base_url, timeout_seconds=2.0)
        if connected:
            return True
        time.sleep(0.35)
    connected, _, _, _ = probe_ollama(base_url, timeout_seconds=2.0)
    return connected


def _path_has_model_manifests(path: Path) -> bool:
    manifests = path / "manifests"
    if not manifests.is_dir():
        return False
    try:
        return any(item.is_file() for item in manifests.rglob("*"))
    except Exception:
        return False


def _candidate_ollama_model_dirs() -> list[Path]:
    candidates: list[Path] = []
    env_value = os.environ.get("OLLAMA_MODELS")
    if env_value:
        candidates.append(Path(env_value).expanduser())
    candidates.extend(
        [
            Path.home() / ".ollama" / "models",
            Path("/usr/share/ollama/.ollama/models"),
            Path("/var/lib/ollama/.ollama/models"),
        ]
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        unique.append(candidate)
        seen.add(key)
    return unique


def _ollama_models_dir_for_env() -> str:
    existing: list[Path] = []
    for candidate in _candidate_ollama_model_dirs():
        try:
            if candidate.is_dir():
                existing.append(candidate)
                if _path_has_model_manifests(candidate):
                    return str(candidate)
        except Exception:
            continue
    return str(existing[0]) if existing else ""


def _wait_until_unreachable(base_url: str, timeout_seconds: float = 8.0) -> bool:
    deadline = time.time() + max(1.0, float(timeout_seconds))
    while time.time() < deadline:
        connected, _, _, _ = probe_ollama(base_url, timeout_seconds=1.0)
        if not connected:
            return True
        time.sleep(0.25)
    connected, _, _, _ = probe_ollama(base_url, timeout_seconds=1.0)
    return not connected


def _ensure_local_server(base_url: str, timeout_seconds: float = 12.0) -> str:
    normalized_base_url = normalize_base_url(base_url, DEFAULT_BASE_URL)
    if not _is_local_base_url(normalized_base_url):
        return ""
    connected, _, _, _ = probe_ollama(normalized_base_url, timeout_seconds=3.0)
    if connected:
        return ""

    executable = shutil.which("ollama")
    if not executable:
        return "ollama executable not found; local server could not be started"

    with _SERVER_LOCK:
        global _SERVER_PROC, _SERVER_BASE_URL
        if _SERVER_PROC and _SERVER_PROC.poll() is None and _SERVER_BASE_URL == normalized_base_url:
            return ""

        env = os.environ.copy()
        env["OLLAMA_HOST"] = _ollama_host_for_env(normalized_base_url)
        models_dir = _ollama_models_dir_for_env()
        if models_dir:
            env["OLLAMA_MODELS"] = models_dir
        try:
            proc = subprocess.Popen(
                [executable, "serve"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"

        _SERVER_PROC = proc
        _SERVER_BASE_URL = normalized_base_url

    deadline = time.time() + max(3.0, float(timeout_seconds))
    while time.time() < deadline:
        connected, _, _, _ = probe_ollama(normalized_base_url, timeout_seconds=2.0)
        if connected:
            return ""
        with _SERVER_LOCK:
            proc = _SERVER_PROC
        if proc is not None and proc.poll() is not None:
            code = proc.returncode
            with _SERVER_LOCK:
                if _SERVER_PROC is proc:
                    _SERVER_PROC = None
                    _SERVER_BASE_URL = ""
            return f"ollama serve exited with code {code}"
        time.sleep(0.5)

    with _SERVER_LOCK:
        proc = _SERVER_PROC
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            if _SERVER_PROC is proc:
                _SERVER_PROC = None
                _SERVER_BASE_URL = ""
    return "ollama serve did not become ready in time"


def _shutdown_local_server(base_url: str | None = None) -> str:
    target_base_url = normalize_base_url(base_url or _read_state().get("base_url"), DEFAULT_BASE_URL)
    if not _is_local_base_url(target_base_url):
        return ""
    errors: list[str] = []
    with _SERVER_LOCK:
        global _SERVER_PROC, _SERVER_BASE_URL
        proc = _SERVER_PROC
        managed_proc = proc if proc and proc.poll() is None and _SERVER_BASE_URL == target_base_url else None

    if managed_proc:
        _terminate_process_group(managed_proc.pid, sig=signal.SIGTERM)
        try:
            managed_proc.wait(timeout=8)
        except Exception:
            _terminate_process_group(managed_proc.pid, sig=signal.SIGKILL)
            try:
                managed_proc.wait(timeout=3)
            except Exception as exc:
                errors.append(f"managed ollama serve did not exit: {type(exc).__name__}: {exc}")

    with _SERVER_LOCK:
        if _SERVER_PROC is managed_proc:
            _SERVER_PROC = None
            _SERVER_BASE_URL = ""

    if not managed_proc:
        return "; ".join(errors)

    if not _wait_until_unreachable(target_base_url, timeout_seconds=2.0):
        errors.append("local ollama serve is still reachable after DockUP-managed shutdown")

    return "; ".join(errors)


def _cleanup_managed_ollama(base_url: str | None = None, *, offload: bool = True) -> dict[str, str]:
    saved = _read_state()
    target_base_url = normalize_base_url(base_url or saved.get("base_url"), DEFAULT_BASE_URL)
    target_model = str(saved.get("model") or "").strip()
    offload_error = ""
    shutdown_error = ""
    if offload:
        offload_error = _offload_running_models(target_base_url, target_model)
    shutdown_error = _shutdown_local_server(target_base_url)
    with _LOCK:
        global _WARMUP_TOKEN
        _WARMUP_TOKEN += 1
        _WARMUP_JOB.update(
            {
                "running": False,
                "message": "Model offloaded and server stopped" if not (offload_error or shutdown_error) else "Cleanup finished with errors",
                "model": target_model,
                "error": "; ".join(filter(None, [offload_error, shutdown_error])),
                "finished_at": time.time(),
            }
        )
    return {
        "offload_error": offload_error,
        "shutdown_error": shutdown_error,
    }


def _warmup_worker(base_url: str, model: str, settings: dict[str, Any], think_mode: str, token: int) -> None:
    with _LOCK:
        _WARMUP_JOB.update(
            {
                "running": True,
                "message": "Loading local model",
                "model": model,
                "think_mode": think_mode,
                "settings": settings,
                "num_ctx": settings["num_ctx"],
                "error": "",
                "started_at": time.time(),
                "finished_at": None,
            }
        )
    try:
        chat(
            base_url=base_url,
            model=model,
            messages=[{"role": "user", "content": "."}],
            keep_alive=settings["keep_alive"],
            think=_think_flag(think_mode),
            options=_ollama_options(settings, warmup=True),
            timeout_seconds=180.0,
        )
        error = ""
        message = "Model is ready"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        message = "Model warmup failed"
    with _LOCK:
        if token != _WARMUP_TOKEN:
            return
        _WARMUP_JOB.update(
            {
                "running": False,
                "message": message,
                "think_mode": think_mode,
                "error": error,
                "finished_at": time.time(),
            }
        )


def connect(payload: dict[str, Any]) -> dict[str, Any]:
    global _WARMUP_TOKEN
    base_url = normalize_base_url(payload.get("base_url"), DEFAULT_BASE_URL)
    requested_model = str(payload.get("model") or "").strip()
    warmup = _normalize_bool(payload.get("warmup", True), True)
    load_model = _normalize_bool(payload.get("load_model"), bool(requested_model))
    previous = _read_state()
    requested_settings = _settings_from_payload(payload, previous.get("settings"))
    requested_think_mode = _normalize_think_mode(payload.get("think_mode"), previous.get("think_mode", DEFAULT_THINK_MODE))
    requested_selected_models = payload.get("selected_models")
    server_error = ""
    if base_url:
        server_error = _ensure_local_server(base_url)
    snapshot = _snapshot(base_url, requested_model, requested_selected_models if isinstance(requested_selected_models, list) else None)
    model = str(snapshot.get("model") or requested_model).strip()
    previous_model = str(previous.get("model") or "").strip()
    stop_error = ""
    if model and previous_model and previous_model != model:
        stop_error = _offload_model(base_url, previous_model)
        with _LOCK:
            _WARMUP_TOKEN += 1
            _WARMUP_JOB.update(
                {
                    "running": False,
                    "message": "Previous model offloaded",
                    "model": previous_model,
                    "error": stop_error,
                    "finished_at": time.time(),
                }
            )
    state = {
        "base_url": base_url,
        "model": model,
        "settings": requested_settings,
        "think_mode": requested_think_mode,
        "selected_models": _normalize_selected_models(
            requested_selected_models if isinstance(requested_selected_models, list) else previous.get("selected_models"),
            [str(item.get("name") or "").strip() for item in snapshot.get("models", []) if str(item.get("name") or "").strip()],
            model,
        ),
        "connected": bool(snapshot.get("connected")),
        "auto_start": True,
        "last_error": str(server_error or snapshot.get("error") or ""),
    }
    _write_state(state)
    if state["connected"] and model and warmup and load_model:
        with _LOCK:
            running_same = (
                bool(_WARMUP_JOB.get("running"))
                and _WARMUP_JOB.get("model") == model
                and _normalize_think_mode(_WARMUP_JOB.get("think_mode"), DEFAULT_THINK_MODE) == requested_think_mode
                and _normalize_settings(_WARMUP_JOB.get("settings")) == requested_settings
            )
        if not running_same:
            with _LOCK:
                _WARMUP_TOKEN += 1
                token = _WARMUP_TOKEN
            thread = threading.Thread(
                target=_warmup_worker,
                args=(base_url, model, requested_settings, requested_think_mode, token),
                daemon=True,
            )
            thread.start()
    result = _snapshot(base_url, model, state.get("selected_models"))
    if server_error:
        result["server_error"] = server_error
    if stop_error:
        result["stop_error"] = stop_error
    return result


def offload(payload: dict[str, Any]) -> dict[str, Any]:
    global _WARMUP_TOKEN
    saved = _read_state()
    base_url = normalize_base_url(payload.get("base_url") or saved.get("base_url"), DEFAULT_BASE_URL)
    model_name = str(payload.get("model") or saved.get("model") or "").strip()
    offload_error = ""
    if model_name and _is_local_base_url(base_url):
        with _LOCK:
            _WARMUP_TOKEN += 1
        offload_error = _offload_model(base_url, model_name)
        with _LOCK:
            _WARMUP_JOB.update(
                {
                    "running": False,
                    "message": "Model offloaded" if not offload_error else "Model offload failed",
                    "model": model_name,
                    "error": offload_error,
                    "finished_at": time.time(),
                }
            )
    result = _snapshot(base_url, model_name or None, saved.get("selected_models") if isinstance(saved.get("selected_models"), list) else None)
    result["offloaded_model"] = model_name if model_name and not offload_error else ""
    result["offload_error"] = offload_error
    return result


def shutdown(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    saved = _read_state()
    base_url = normalize_base_url(payload.get("base_url") or saved.get("base_url"), DEFAULT_BASE_URL)
    cleanup = _cleanup_managed_ollama(base_url, offload=_normalize_bool(payload.get("offload", True), True))
    result = _snapshot(base_url, None, saved.get("selected_models") if isinstance(saved.get("selected_models"), list) else None)
    result["offloaded_model"] = str(saved.get("model") or "").strip() if not (cleanup.get("offload_error") or cleanup.get("shutdown_error")) else ""
    result["offload_error"] = cleanup.get("offload_error", "")
    result["shutdown_error"] = cleanup.get("shutdown_error", "")
    return result


def update_selected_models(payload: dict[str, Any]) -> dict[str, Any]:
    saved = _read_state()
    base_url = normalize_base_url(payload.get("base_url") or saved.get("base_url"), DEFAULT_BASE_URL)
    if saved.get("auto_start"):
        _ensure_local_server(base_url)
    model_rows = probe_ollama(base_url)[2]
    model_names = [item.name for item in model_rows]
    current_model = str(payload.get("model") or saved.get("model") or "").strip()
    selected = _normalize_selected_models(payload.get("selected_models"), model_names, current_model)
    state = {
        "base_url": base_url,
        "model": _preferred_model([item.as_dict() for item in model_rows], current_model),
        "settings": _normalize_settings(payload.get("settings") if isinstance(payload.get("settings"), dict) else saved.get("settings")),
        "think_mode": _normalize_think_mode(payload.get("think_mode"), saved.get("think_mode", DEFAULT_THINK_MODE)),
        "selected_models": selected,
        "connected": bool(payload.get("connected", saved.get("connected"))),
        "auto_start": bool(saved.get("auto_start")),
        "last_error": str(payload.get("last_error") or saved.get("last_error") or ""),
    }
    _write_state(state)
    return _snapshot(state["base_url"], state["model"], selected)


def _build_chat_request(payload: dict[str, Any]) -> dict[str, Any]:
    saved = _read_state()
    base_url = normalize_base_url(payload.get("base_url") or saved.get("base_url"), DEFAULT_BASE_URL)
    model = str(payload.get("model") or saved.get("model") or "").strip()
    settings = _settings_from_payload(payload, saved.get("settings"))
    think_mode = _normalize_think_mode(payload.get("think_mode"), saved.get("think_mode", DEFAULT_THINK_MODE))
    message = str(payload.get("message") or "").strip()
    history = payload.get("history") if isinstance(payload.get("history"), list) else []
    state_context = docking_state_context()
    state_content = build_agent_working_memory(
        user_goal=message,
        state_context=state_context,
        agent_state=AGENT_STATE,
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": state_system_prompt()},
        {"role": "system", "content": f"DockUP working memory:\n{state_content}"},
    ]
    for row in history[-8:]:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").strip()
        content = str(row.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})
    request_preview = _build_ollama_chat_payload(
        {
            "model": model,
            "settings": settings,
            "think_mode": think_mode,
        },
        messages,
    )
    return {
        "base_url": base_url,
        "model": model,
        "settings": settings,
        "think_mode": think_mode,
        "message": message,
        "messages": messages,
        "state_context": state_context,
        "request_usage": _request_usage_from_payload(request_preview),
    }


def _build_ollama_chat_payload(request: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": request["model"],
        "messages": messages,
        "stream": False,
        "tools": DOCKING_TOOLS,
        "options": _tool_options(request["settings"]),
        "keep_alive": request["settings"]["keep_alive"],
    }
    think = _think_flag(request["think_mode"])
    if think is not None:
        payload["think"] = think
    return payload


def _request_usage_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    tools = payload.get("tools") if isinstance(payload.get("tools"), list) else []
    options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    prompt_text = json.dumps(
        {
            "messages": messages,
            "tools": tools,
            "options": options,
            "think": payload.get("think"),
            "keep_alive": payload.get("keep_alive"),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    prompt_chars = len(prompt_text)
    payload_chars = len(payload_text)
    prompt_tokens_est = max(1, math.ceil(prompt_chars / 4)) if prompt_chars else 0
    payload_tokens_est = max(1, math.ceil(payload_chars / 4)) if payload_chars else 0
    try:
        window_tokens = max(1, int((options or {}).get("num_ctx") or DEFAULT_NUM_CTX))
    except (TypeError, ValueError):
        window_tokens = DEFAULT_NUM_CTX
    budget_tokens = _agent_num_predict((options or {}).get("num_ctx"))
    percent = min(100, round((prompt_tokens_est / window_tokens) * 100)) if window_tokens else 0
    return {
        "prompt_chars": prompt_chars,
        "payload_chars": payload_chars,
        "prompt_tokens_est": prompt_tokens_est,
        "payload_tokens_est": payload_tokens_est,
        "window_tokens": window_tokens,
        "budget_tokens": budget_tokens,
        "percent": percent,
        "message_count": len(messages),
        "tool_count": len(tools),
        "system_message_count": sum(1 for row in messages if isinstance(row, dict) and str(row.get("role") or "") == "system"),
        "history_message_count": max(0, len(messages) - 3),
    }


def _tool_options(settings: dict[str, Any]) -> dict[str, Any]:
    options = _ollama_options(settings)
    options["temperature"] = AGENT_TEMPERATURE
    options["num_predict"] = _agent_num_predict(settings.get("num_ctx"))
    return options


def request_usage(payload: dict[str, Any]) -> dict[str, Any]:
    request = _build_chat_request(payload)
    return {
        "ok": True,
        "model": request["model"],
        "think_mode": request["think_mode"],
        "state_context": request["state_context"],
        "request_usage": request.get("request_usage") or {},
    }


def _message_tool_calls(data: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    message = data.get("message") if isinstance(data.get("message"), dict) else {}
    raw_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
    calls: list[dict[str, Any]] = []
    for raw_call in raw_calls:
        function = (raw_call or {}).get("function") if isinstance(raw_call, dict) else {}
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        args = function.get("arguments") or {}
        if isinstance(args, str):
            try:
                parsed_args = json.loads(args)
            except Exception:
                parsed_args = {}
            args = parsed_args if isinstance(parsed_args, dict) else {}
        if not isinstance(args, dict):
            args = {}
        calls.append({"name": name, "arguments": args})
    return calls, message


def _tool_call_label(name: str, args: dict[str, Any]) -> str:
    try:
        raw_args = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        raw_args = "{}"
    return f"{name}({raw_args})"


def _tool_status(name: str, result: dict[str, Any]) -> str:
    if result.get("summary"):
        return str(result.get("summary") or "")
    if name == "get_dockup_state":
        return f"State: {len(result.get('loaded_receptors') or [])} receptor(s), {len(result.get('loaded_ligands') or [])} ligand(s), queue={result.get('queue_jobs', 0)}"
    if name == "fetch_assets":
        failures = (result.get("failed_receptors") or []) + (result.get("failed_ligands") or [])
        suffix = " | retry once with corrected names" if failures else ""
        return f"Assets: {len(result.get('loaded_receptors') or [])} receptor(s), {len(result.get('saved_ligands') or [])} ligand file(s){suffix}"
    if name == "inspect_assets":
        inv = result.get("inventory") if isinstance(result.get("inventory"), dict) else {}
        return f"Inspection: {len(inv.get('receptors') or {})} receptor(s), {len(inv.get('ligands') or [])} ligand(s)"
    if name == "show_in_viewer":
        return result.get("summary") or f"Viewer selected {result.get('selected_receptor') or '-'}"
    if name == "show_residues":
        return result.get("summary") or f"Residues: {len(result.get('residues') or [])}"
    if name == "select_workspace":
        return f"Workspace selected: {len(result.get('selected') or [])} receptor row(s)"
    if name == "set_gridbox":
        return f"Gridbox: {len(result.get('gridboxes') or {})} receptor(s)"
    if name == "set_docking_config":
        cfg = result.get("config") if isinstance(result.get("config"), dict) else {}
        return f"Config: engine={cfg.get('engine') or '-'} mode={cfg.get('mode') or '-'} run_count={cfg.get('run_count') or 1}"
    if name == "build_or_run_queue":
        queue = result.get("queue") if isinstance(result.get("queue"), dict) else {}
        run = result.get("run") if isinstance(result.get("run"), dict) else {}
        mode = "append" if result.get("replace_queue") is False or queue.get("replace_queue") is False else "replace"
        suffix = " | run started" if run.get("started") else ""
        return f"Queue: jobs={queue.get('new_jobs', 0)} batch={queue.get('batch_id') or '-'} mode={mode}{suffix}"
    if name == "delete_ligands":
        return f"Deleted ligands: {len(result.get('deleted') or [])}"
    if name == "delete_receptors":
        return f"Deleted receptors: {len(result.get('deleted') or [])}"
    if name == "delete_queue_batches":
        return f"Deleted queue batches: {len(result.get('deleted_batch_ids') or [])}; queue={result.get('queue_count', 0)}"
    if name == "read_tool_details":
        return f"Read details: {result.get('topic') or 'workflow'}"
    if name == "plan_assets":
        return f"Planned assets: receptors={result.get('receptors') or '-'} ligands={result.get('ligands') or '-'}"
    if name == "download_assets":
        loaded = ", ".join(result.get("loaded_receptors") or []) or "-"
        saved = ", ".join(result.get("saved_ligands") or []) or "-"
        failed = (result.get("failed_receptors") or []) + (result.get("failed_ligands") or [])
        suffix = f" | failed={len(failed)}" if failed else ""
        return f"Assets ready: receptors={loaded} ligands={saved}{suffix}"
    if name == "submit_setup_rows":
        return f"Setup rows selected: {len(result.get('rows') or [])} receptor(s)"
    if name == "make_gridboxes":
        warnings = result.get("warnings") or []
        suffix = f" | warnings={len(warnings)}" if warnings else ""
        return f"Gridboxes computed: {len(result.get('grid_data') or {})} receptor(s){suffix}"
    if name == "submit_batch_config":
        cfg = result.get("docking_config") if isinstance(result.get("docking_config"), dict) else {}
        return (
            f"Config set: run_count={result.get('run_count', 1)} "
            f"padding={result.get('padding', 0)} engine={cfg.get('docking_engine') or '-'} "
            f"mode={cfg.get('docking_mode') or '-'} out={result.get('out_root_name') or '-'}"
        )
    if name == "validate_batch":
        return f"Validated: jobs={result.get('job_count', 0)} run_count-aware total_runs={result.get('total_runs', 0)}"
    if name == "build_queue":
        return f"Queue built: new_jobs={result.get('new_jobs', 0)} batch={result.get('batch_id') or '-'}"
    if name == "run_queue":
        return (
            f"Run queued: jobs={result.get('queue_jobs', result.get('job_count', 0))} "
            f"total_runs={result.get('planned_total_runs', result.get('total_runs', 0))} "
            f"mode={'test/log' if result.get('test_mode') else 'full'}"
        )
    return "Tool finished"


def _tool_context_result(name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Keep model-visible tool results small; full results stay in trace/UI events."""
    compact: dict[str, Any] = {
        "ok": bool(result.get("ok", True)),
        "summary": _tool_status(name, result),
    }
    for key in ("error", "allowed_next_tools", "verification"):
        if key in result:
            compact[key] = result.get(key)
    if name == "get_dockup_state":
        for key in (
            "loaded_receptors",
            "loaded_ligands",
            "selected_receptor",
            "selected_chain",
            "selected_native_ligand",
            "workspace_rows",
            "gridbox_ready",
            "gridbox_count",
            "queue_jobs",
            "run_status",
        ):
            compact[key] = result.get(key)
    elif name == "fetch_assets":
        compact.update(
            {
                "loaded_receptors": (result.get("loaded_receptors") or [])[:8],
                "saved_ligands": (result.get("saved_ligands") or [])[:12],
                "failed_receptors": (result.get("failed_receptors") or [])[:4],
                "failed_ligands": (result.get("failed_ligands") or [])[:4],
                "retry_attempts": (result.get("retry_attempts") or [])[:6],
                "retry_hint": str(result.get("retry_hint") or "").strip(),
            }
        )
    elif name == "inspect_assets":
        inv = result.get("inventory") if isinstance(result.get("inventory"), dict) else {}
        receptors = inv.get("receptors") if isinstance(inv.get("receptors"), dict) else {}
        compact["inventory"] = {
            "receptors": dict(list(receptors.items())[:6]),
            "ligands": (inv.get("ligands") or [])[:12],
        }
    elif name == "show_in_viewer":
        compact["selected_receptor"] = result.get("selected_receptor")
        compact["selected_chain"] = result.get("selected_chain")
        compact["selected_native_ligand"] = result.get("selected_native_ligand")
    elif name == "show_residues":
        compact["receptor"] = result.get("receptor")
        compact["residue"] = result.get("residue")
        compact["residues"] = (result.get("residues") or [])[:32]
        compact["selection"] = result.get("selection")
    elif name == "select_workspace":
        compact["selected"] = (result.get("selected") or [])[:8]
    elif name == "set_gridbox":
        compact["gridboxes"] = dict(list((result.get("gridboxes") or {}).items())[:8])
        compact["warnings"] = (result.get("warnings") or [])[:4]
    elif name == "set_docking_config":
        compact["config"] = result.get("config") if isinstance(result.get("config"), dict) else {}
        compact["validation"] = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    elif name == "build_or_run_queue":
        compact["queue"] = result.get("queue") if isinstance(result.get("queue"), dict) else {}
        compact["replace_queue"] = result.get("replace_queue")
        compact["run"] = result.get("run") if isinstance(result.get("run"), dict) else {}
    elif name == "delete_ligands":
        compact["deleted"] = (result.get("deleted") or [])[:16]
        compact["missing"] = (result.get("missing") or [])[:8]
        compact["active_ligands"] = (result.get("active_ligands") or [])[:16]
    elif name == "delete_receptors":
        compact["deleted"] = (result.get("deleted") or [])[:16]
        compact["missing"] = (result.get("missing") or [])[:8]
        compact["remaining_receptors"] = (result.get("remaining_receptors") or [])[:16]
    elif name == "delete_queue_batches":
        compact["deleted_batch_ids"] = (result.get("deleted_batch_ids") or [])[:16]
        compact["queue_count"] = result.get("queue_count", 0)
    elif name == "read_tool_details":
        compact["topic"] = result.get("topic")
        compact["details"] = result.get("details")
    return compact


def _short_text(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def _fallback_clarification() -> str:
    return "Ilerleyebilmem icin bir detay daha lazim."


def _assistant_history_message(message: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {
        "role": "assistant",
        "content": str(message.get("content") or "").strip(),
    }
    name = str(message.get("name") or "").strip()
    if name:
        sanitized["name"] = name
    tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
    if tool_calls:
        sanitized["tool_calls"] = tool_calls
    return sanitized


def _loop_text_signature(content: str, thinking: str) -> str:
    raw = f"{content} {thinking}".strip().lower()
    raw = re.sub(r"\s+", " ", raw).strip()
    raw = re.sub(r"[^\w\s]+", "", raw)
    return raw


def _record_agent_memory(*, step: int, tool_name: str | None = None, result: dict[str, Any] | None = None, answer: str | None = None) -> None:
    recent = list(AGENT_STATE.get("recent_actions") or [])
    if tool_name:
        tool_result = result or {}
        summary = _short_text(_tool_status(tool_name, tool_result), 220)
        recent.append(
            {
                "step": step,
                "kind": "tool",
                "tool": tool_name,
                "summary": summary,
                "ok": bool(tool_result.get("ok", True)),
            }
        )
        AGENT_STATE["last_tool"] = tool_name
        AGENT_STATE["last_tool_summary"] = summary
        AGENT_STATE["last_error"] = "" if bool(tool_result.get("ok", True)) else _short_text(tool_result.get("error") or summary, 240)
        if tool_name == "get_dockup_state":
            AGENT_STATE["workflow_stage"] = "state_read"
        elif tool_name == "fetch_assets":
            AGENT_STATE["workflow_stage"] = "assets_loaded"
        elif tool_name in {"inspect_assets", "show_in_viewer", "show_residues"}:
            AGENT_STATE["workflow_stage"] = "inspection"
        elif tool_name == "select_workspace":
            AGENT_STATE["workflow_stage"] = "workspace_selected"
        elif tool_name == "set_gridbox":
            AGENT_STATE["workflow_stage"] = "grid_ready"
        elif tool_name == "set_docking_config":
            AGENT_STATE["workflow_stage"] = "configured"
        elif tool_name in {"build_or_run_queue", "build_queue"}:
            AGENT_STATE["workflow_stage"] = "queued"
        elif tool_name == "run_queue":
            AGENT_STATE["workflow_stage"] = "running" if tool_result.get("started") else "queued"
        if not bool(tool_result.get("ok", True)):
            AGENT_STATE["workflow_stage"] = "error"
    elif answer is not None:
        summary = _short_text(answer, 220)
        if summary:
            recent.append(
                {
                    "step": step,
                    "kind": "assistant",
                    "tool": "",
                    "summary": summary,
                    "ok": True,
                }
            )
            AGENT_STATE["last_answer"] = _short_text(answer, 240)

    recent = recent[-6:]
    AGENT_STATE["recent_actions"] = recent
    AGENT_STATE["memory_summary"] = " | ".join(
        f"{str(item.get('tool') or item.get('kind') or '').strip()}: {str(item.get('summary') or '').strip()}" for item in recent[-4:]
    ).strip(" |")


def _reset_docking_tool_state() -> None:
    AGENT_STATE.update(
        {
            "inventory": {},
            "setup_rows": [],
            "grid_data": {},
            "batch_config": {},
            "batch_id": "",
            "recent_actions": [],
            "memory_summary": "",
            "last_tool": "",
            "last_tool_summary": "",
            "last_answer": "",
            "last_error": "",
            "workflow_stage": "idle",
            "attempt_ledger": [],
        }
    )


def _execute_named_tool(
    name: str,
    args: dict[str, Any],
    *,
    test_mode: bool,
    progress_callback=None,
) -> dict[str, Any]:
    clean_args = dict(args or {})
    if name == "run_queue":
        clean_args.setdefault("test_mode", test_mode)
    if name == "build_queue":
        clean_args.setdefault("replace_queue", True)
    if name in {"set_gridbox", "build_or_run_queue", "run_queue"} and progress_callback is not None:
        clean_args.setdefault("progress_callback", progress_callback)
    func = CONTROL_TOOL_FUNCTIONS.get(name) or DOCKING_FUNCTIONS.get(name)
    if not func:
        return {"ok": False, "error": f"Unknown DockUP tool: {name}"}
    try:
        result = func(**clean_args)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if isinstance(result, dict):
        return result
    return {"ok": True, "result": result}


def _execute_named_tool_streaming(
    name: str,
    args: dict[str, Any],
    *,
    test_mode: bool,
    progress_callback=None,
) -> dict[str, Any]:
    tool_kwargs: dict[str, Any] = {"test_mode": test_mode}
    if progress_callback is not None and name in {"set_gridbox", "build_or_run_queue", "run_queue"}:
        tool_kwargs["progress_callback"] = progress_callback
    try:
        return _execute_named_tool(name, args, **tool_kwargs)
    except TypeError as exc:
        if progress_callback is not None and "progress_callback" in str(exc):
            return _execute_named_tool(name, args, test_mode=test_mode)
        raise


def _chat_agent_step(
    request: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    progress_callback=None,
) -> dict[str, Any]:
    if not progress_callback:
        return chat(
            base_url=request["base_url"],
            model=request["model"],
            messages=messages,
            tools=DOCKING_TOOLS,
            keep_alive=request["settings"]["keep_alive"],
            think=_think_flag(request["think_mode"]),
            options=_tool_options(request["settings"]),
            timeout_seconds=240.0,
        )

    content_parts: list[str] = []
    thinking_parts: list[str] = []
    last_tool_calls: list[dict[str, Any]] = []
    final_raw: dict[str, Any] = {}
    in_think_markup = False
    for chunk in stream_chat(
        base_url=request["base_url"],
        model=request["model"],
        messages=messages,
        tools=DOCKING_TOOLS,
        keep_alive=request["settings"]["keep_alive"],
        think=_think_flag(request["think_mode"]),
        options=_tool_options(request["settings"]),
        timeout_seconds=240.0,
    ):
        if isinstance(chunk, dict):
            final_raw = chunk
        message = chunk.get("message") if isinstance(chunk.get("message"), dict) else {}
        thinking_delta = str(message.get("thinking") or "")
        if thinking_delta:
            thinking_parts.append(thinking_delta)
            progress_callback({"type": "thinking", "delta": thinking_delta})
        content_delta = str(message.get("content") or "")
        if content_delta:
            split_rows, in_think_markup = _split_think_markup(content_delta, in_think=in_think_markup)
            for kind, delta in split_rows:
                if kind == "thinking":
                    thinking_parts.append(delta)
                    progress_callback({"type": "thinking", "delta": delta})
                else:
                    content_parts.append(delta)
                    progress_callback({"type": "answer", "delta": delta})
        raw_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
        if raw_calls:
            last_tool_calls = raw_calls
    assembled_message = {
        "role": "assistant",
        "content": "".join(content_parts).strip(),
        "thinking": "".join(thinking_parts).strip(),
    }
    if last_tool_calls:
        assembled_message["tool_calls"] = last_tool_calls
    final_raw["message"] = assembled_message
    return final_raw


def _tool_loop_answer(result: dict[str, Any]) -> str:
    if result.get("answer"):
        return str(result.get("answer") or "")
    trace = result.get("trace") if isinstance(result.get("trace"), list) else []
    last_by_tool: dict[str, dict[str, Any]] = {}
    for row in trace:
        if isinstance(row, dict) and row.get("tool"):
            tool_result = row.get("result") if isinstance(row.get("result"), dict) else {}
            last_by_tool[str(row.get("tool"))] = tool_result
    queue_tool_names = {"submit_batch_config", "validate_batch", "build_queue", "run_queue", "build_or_run_queue"}
    has_queue_work = any(name in last_by_tool for name in queue_tool_names)
    if not has_queue_work:
        failures = [
            _tool_status(str(row.get("tool") or ""), row.get("result") if isinstance(row.get("result"), dict) else {})
            for row in trace
            if isinstance(row, dict)
            and row.get("tool")
            and isinstance(row.get("result"), dict)
            and not row["result"].get("ok", True)
        ]
        return "\n".join(failures)
    validation = last_by_tool.get("validate_batch", {})
    queue_action = last_by_tool.get("build_or_run_queue", {})
    queue_from_action = queue_action.get("queue") if isinstance(queue_action.get("queue"), dict) else {}
    run_from_action = queue_action.get("run") if isinstance(queue_action.get("run"), dict) else {}
    run_result = last_by_tool.get("run_queue", {}) or run_from_action
    queue_result = last_by_tool.get("build_queue", {}) or queue_from_action
    failed_queue = queue_action if queue_action and not queue_action.get("ok", True) else {}
    if not failed_queue and last_by_tool.get("build_queue") and not last_by_tool["build_queue"].get("ok", True):
        failed_queue = last_by_tool["build_queue"]
    if not failed_queue and last_by_tool.get("run_queue") and not last_by_tool["run_queue"].get("ok", True):
        failed_queue = last_by_tool["run_queue"]
    if failed_queue:
        validation_payload = failed_queue.get("validation") if isinstance(failed_queue.get("validation"), dict) else {}
        errors = validation_payload.get("errors") or failed_queue.get("errors") or []
        answer = str(failed_queue.get("summary") or failed_queue.get("error") or "Queue step failed.")
        if errors:
            answer += "\nErrors: " + " | ".join(str(item) for item in errors[:6])
        return answer
    if not validation:
        validation = {
            "job_count": queue_result.get("job_count") or queue_result.get("new_jobs") or run_result.get("queue_jobs") or 0,
            "total_runs": queue_result.get("total_runs") or run_result.get("planned_total_runs") or 0,
        }
    grids = (AGENT_STATE.get("grid_data") or {}) if isinstance(AGENT_STATE.get("grid_data"), dict) else {}
    grid_lines = [
        f"- {pdb_id}: center=({grid.get('cx')}, {grid.get('cy')}, {grid.get('cz')}), size=({grid.get('sx')}, {grid.get('sy')}, {grid.get('sz')})"
        for pdb_id, grid in grids.items()
    ]
    try:
        run_count = max(1, int((AGENT_STATE.get("batch_config") or {}).get("run_count") or 1))
    except (TypeError, ValueError):
        run_count = 1
    job_count = validation.get("job_count", run_result.get("queue_jobs", 0)) or 0
    total_runs = run_result.get("planned_total_runs", validation.get("total_runs", 0)) or 0
    batch_id = run_result.get("batch_id") or queue_result.get("batch_id") or ""
    if not job_count and not total_runs and not batch_id:
        return ""
    answer = (
        f"Job combinations: {job_count}\n"
        + f"Run count per job: {run_count}\n"
        + f"Total planned runs: {total_runs}"
    )
    if batch_id:
        answer += f"\nBatch: {batch_id}"
    if grid_lines:
        answer += "\n\nGridboxes:\n" + "\n".join(grid_lines)
    if run_result.get("test_mode"):
        answer += "\nMode: test/log run; no heavy docking process was started."
    elif run_result.get("started"):
        answer += "\nMode: full run requested."
    return answer


def _run_single_agent_tool_loop(
    payload: dict[str, Any],
    request: dict[str, Any],
    *,
    progress_callback=None,
) -> dict[str, Any]:
    _reset_docking_tool_state()
    messages = list(request["messages"])
    trace: list[dict[str, Any]] = []
    observer = observer_from_payload(payload, request)
    test_mode = _normalize_bool(payload.get("test_mode"), True)
    last_thinking = ""
    thinking_streamed = False
    repeated_calls: dict[str, int] = {}
    repeated_failures: set[str] = set()
    last_content_signature = ""
    repeated_content_count = 0
    last_thinking_signature = ""
    repeated_thinking_count = 0
    step = 0
    def finish(result: dict[str, Any]) -> dict[str, Any]:
        if observer is not None:
            result.setdefault("observer_run_dir", str(observer.run_dir))
            observer.finish(result)
        return result

    while True:
        step += 1
        if observer is not None:
            observer.model_request(step, _build_ollama_chat_payload(request, messages))
        try:
            data = _chat_agent_step(request, messages, progress_callback=progress_callback)
        except Exception as exc:
            return finish({"ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": trace, "agent_state": dict(AGENT_STATE)})
        if observer is not None:
            observer.model_response(step, data if isinstance(data, dict) else {"raw": data})
        calls, message = _message_tool_calls(data)
        content = str(message.get("content") or "").strip()
        thinking = str(message.get("thinking") or "").strip()
        if thinking:
            last_thinking += thinking
            if progress_callback:
                thinking_streamed = True
        assistant_message = _assistant_history_message(message)
        trace.append({"step": step, "assistant": assistant_message})
        messages.append(assistant_message)
        content_signature = _loop_text_signature(content, "")
        thinking_signature = _loop_text_signature("", thinking)
        if content_signature:
            if content_signature == last_content_signature:
                repeated_content_count += 1
            else:
                last_content_signature = content_signature
                repeated_content_count = 1
        else:
            last_content_signature = ""
            repeated_content_count = 0
        if thinking_signature:
            if thinking_signature == last_thinking_signature:
                repeated_thinking_count += 1
            else:
                last_thinking_signature = thinking_signature
                repeated_thinking_count = 1
        else:
            last_thinking_signature = ""
            repeated_thinking_count = 0
        if repeated_content_count >= 3 or repeated_thinking_count >= 3:
            final_answer = content or _tool_loop_answer({"trace": trace}) or _fallback_clarification()
            _record_agent_memory(step=step, answer=final_answer)
            return finish({
                "ok": True,
                "answer": final_answer,
                "thinking": last_thinking,
                "thinking_streamed": thinking_streamed,
                "trace": trace,
                "agent_state": dict(AGENT_STATE),
                "stopped_reason": "repeated_text",
            })
        if not calls:
            final_answer = content or _fallback_clarification()
            _record_agent_memory(step=step, answer=final_answer)
            return finish({
                "ok": True,
                "answer": final_answer,
                "answer_streamed": bool(progress_callback and final_answer),
                "thinking": last_thinking,
                "thinking_streamed": thinking_streamed,
                "trace": trace,
                "agent_state": dict(AGENT_STATE),
                "raw": data,
            })
        for call in calls:
            name = str(call.get("name") or "").strip()
            args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
            call_key = f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)}"
            repeated_calls[call_key] = repeated_calls.get(call_key, 0) + 1
            if repeated_calls[call_key] > 2:
                return finish({
                    "ok": True,
                    "answer": _tool_loop_answer({"trace": trace}),
                    "thinking": last_thinking,
                    "thinking_streamed": thinking_streamed,
                    "trace": trace,
                    "agent_state": dict(AGENT_STATE),
                    "stopped_reason": "repeated_tool_call",
                })
            if progress_callback:
                progress_callback({"type": "tool_call", "tool": name, "arguments": args, "prompt": _tool_call_label(name, args)})
            if observer is not None:
                observer.tool_call(step, name, args)
            before_context = docking_state_context()
            tool_started = time.perf_counter()
            if was_failed_attempt(AGENT_STATE, name, args):
                result = {
                    "ok": False,
                    "error": "This failed attempt was already tried. Choose meaningfully different arguments or ask the user.",
                    "summary": f"{name} skipped: repeated failed attempt.",
                    "allowed_next_tools": ["get_dockup_state", "read_tool_details"],
                }
            else:
                result = _execute_named_tool_streaming(name, args, test_mode=test_mode, progress_callback=progress_callback)
            after_context = docking_state_context()
            verification = verify_tool_effect(name, result, before_context, after_context)
            if isinstance(result, dict):
                result["verification"] = verification
            if observer is not None:
                observer.tool_result(
                    step,
                    name,
                    args,
                    result if isinstance(result, dict) else {"ok": True, "result": result},
                    seconds=round(time.perf_counter() - tool_started, 6),
                    before_context=before_context,
                    after_context=after_context,
                    verification=verification,
                )
            trace.append({"tool": name, "arguments": args, "result": result})
            _record_agent_memory(step=step, tool_name=name, result=result)
            record_attempt(
                AGENT_STATE,
                step=step,
                tool_name=name,
                arguments=args,
                result=result,
                verification=verification,
                summary=_tool_status(name, result),
            )
            if not result.get("ok", True):
                repeated_failures.add(call_key)
            if progress_callback:
                progress_callback({"type": "status", "stage": name, "delta": _tool_status(name, result), "result": result})
            messages.append({"role": "tool", "tool_name": name, "content": json.dumps(_tool_context_result(name, result), ensure_ascii=False)})


def ask(payload: dict[str, Any]) -> dict[str, Any]:
    request = _build_chat_request(payload)
    model = request["model"]
    message = request["message"]
    state_context = request["state_context"]
    if not model:
        return {"ok": False, "error": "Select an Ollama model first.", "state_context": state_context}
    if not message:
        return {"ok": False, "error": "Message is empty.", "state_context": state_context}

    result = _run_single_agent_tool_loop(payload, request)
    if not result.get("ok"):
        return {"ok": False, "error": str(result.get("error") or "DockUP tool workflow failed."), "state_context": state_context, "raw": result}
    answer = _tool_loop_answer(result)
    return {
        "ok": True,
        "answer": answer,
        "thinking": str(result.get("thinking") or ""),
        "model": model,
        "think_mode": request["think_mode"],
        "state_context": docking_state_context(),
        "raw": result,
    }


def autonomous_docking(payload: dict[str, Any]) -> dict[str, Any]:
    request = _build_chat_request(payload)
    if not request["model"]:
        return {"ok": False, "error": "Select an Ollama model first."}
    if not request["message"]:
        return {"ok": False, "error": "Message is empty."}
    return _run_single_agent_tool_loop(
        payload,
        request,
        progress_callback=payload.get("progress_callback") if callable(payload.get("progress_callback")) else None,
    )


def _duration_seconds(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return round(float(value) / 1_000_000_000, 3)


def _tokens_per_second(eval_count: Any, eval_duration: Any) -> float | None:
    if not isinstance(eval_count, (int, float)) or not isinstance(eval_duration, (int, float)):
        return None
    if eval_count <= 0 or eval_duration <= 0:
        return None
    return round(float(eval_count) / (float(eval_duration) / 1_000_000_000), 2)


def _split_think_markup(text: str, *, in_think: bool) -> tuple[list[tuple[str, str]], bool]:
    rows: list[tuple[str, str]] = []
    cursor = 0
    source = str(text or "")
    lowered = source.lower()
    while cursor < len(source):
        if in_think:
            end = lowered.find("</think>", cursor)
            if end < 0:
                if source[cursor:]:
                    rows.append(("thinking", source[cursor:]))
                return rows, True
            if end > cursor:
                rows.append(("thinking", source[cursor:end]))
            cursor = end + len("</think>")
            in_think = False
        else:
            start = lowered.find("<think>", cursor)
            if start < 0:
                if source[cursor:]:
                    rows.append(("answer", source[cursor:]))
                return rows, False
            if start > cursor:
                rows.append(("answer", source[cursor:start]))
            cursor = start + len("<think>")
            in_think = True
    return rows, in_think


def stream_ask(payload: dict[str, Any]):
    request = _build_chat_request(payload)
    model = request["model"]
    message = request["message"]
    state_context = request["state_context"]

    def event(row: dict[str, Any]) -> str:
        return json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"

    if not model:
        yield event({"type": "error", "error": "Select an Ollama model first.", "state_context": state_context})
        return
    if not message:
        yield event({"type": "error", "error": "Message is empty.", "state_context": state_context})
        return

    yield event({"type": "start", "model": model, "think_mode": request["think_mode"]})
    started = time.perf_counter()
    progress_queue: Queue[dict[str, Any]] = Queue()
    result_holder: dict[str, Any] = {}

    def worker() -> None:
        try:
            result_holder["result"] = _run_single_agent_tool_loop(
                payload,
                request,
                progress_callback=progress_queue.put,
            )
        except Exception as exc:
            result_holder["result"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            progress_queue.put({"type": "__done__"})

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    while True:
        item = progress_queue.get()
        if not isinstance(item, dict):
            continue
        if item.get("type") == "__done__":
            break
        if item.get("type") == "tool_call":
            yield event(
                {
                    "type": "tool_call",
                    "tool": str(item.get("tool") or ""),
                    "arguments": item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
                    "prompt": str(item.get("prompt") or ""),
                }
            )
        elif item.get("type") == "thinking":
            yield event({"type": "thinking", "delta": str(item.get("delta") or "")})
        elif item.get("type") == "answer":
            yield event({"type": "answer", "delta": str(item.get("delta") or "")})
        elif item.get("type") == "status":
            yield event(
                {
                    "type": "status",
                    "delta": str(item.get("delta") or ""),
                    "stage": str(item.get("stage") or ""),
                    "result": item.get("result") if isinstance(item.get("result"), dict) else {},
                }
            )
    thread.join(timeout=0.5)
    result = result_holder.get("result") if isinstance(result_holder.get("result"), dict) else {"ok": False, "error": "DockUP tool workflow failed."}
    if not result.get("ok"):
        yield event({"type": "error", "error": str(result.get("error") or "DockUP tool workflow failed."), "raw": result})
        return
    answer = _tool_loop_answer(result)
    if result.get("thinking") and not result.get("thinking_streamed"):
        yield event({"type": "thinking", "delta": str(result.get("thinking") or "")})
    if answer and not (result.get("answer_streamed") and answer == str(result.get("answer") or "")):
        yield event({"type": "answer", "delta": answer})
    yield event(
        {
            "type": "done",
            "metrics": {"total_seconds": round(time.perf_counter() - started, 3)},
            "raw": result,
        }
    )
