from __future__ import annotations

import json
import os
import shutil
import threading
import time
import subprocess
import signal
from typing import Any
from pathlib import Path
from urllib.parse import urlparse

from ..agent.ollama_client import chat, normalize_base_url, probe_ollama, running_models, stream_chat, unload_model
from ..agent.state_context import docking_state_context, state_system_prompt
from ..config import BASE

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
NUM_CTX_CHOICES = (1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072)
NUM_BATCH_CHOICES = (64, 128, 256, 512)
KEEP_ALIVE_CHOICES = (-1, 300, 900, 1800, 3600)
NUM_GPU_CHOICES = (-1, 40, 48, 56, 64)
WARMUP_TOKEN_CHOICES = (1, 2, 4, 8)
THINK_MODE_CHOICES = ("auto", "think", "no_think")
PREFERRED_MODEL_PATTERNS = ("qwen36-merged", "qwen36_merged", "merged", "qwen36", "qwen3.6", "35b", "iq3_xs", "iq3-xs")

ROOT_DIR = BASE / ".venv" / "dockup_extensions" / EXTENSION_ID
STATE_PATH = ROOT_DIR / "state.json"

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
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": state_system_prompt()},
        {"role": "system", "content": f"Current DockUP state JSON:\n{json.dumps(state_context, ensure_ascii=False)}"},
    ]
    for row in history[-8:]:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").strip()
        content = str(row.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})
    return {
        "base_url": base_url,
        "model": model,
        "settings": settings,
        "think_mode": think_mode,
        "message": message,
        "messages": messages,
        "state_context": state_context,
    }


def ask(payload: dict[str, Any]) -> dict[str, Any]:
    request = _build_chat_request(payload)
    model = request["model"]
    message = request["message"]
    state_context = request["state_context"]
    if not model:
        return {"ok": False, "error": "Select an Ollama model first.", "state_context": state_context}
    if not message:
        return {"ok": False, "error": "Message is empty.", "state_context": state_context}

    try:
        data = chat(
            base_url=request["base_url"],
            model=model,
            messages=request["messages"],
            keep_alive=request["settings"]["keep_alive"],
            think=_think_flag(request["think_mode"]),
            options=_ollama_options(request["settings"]),
            timeout_seconds=240.0,
        )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "state_context": state_context}
    answer = ""
    thinking = ""
    if isinstance(data.get("message"), dict):
        answer = str(data["message"].get("content") or "").strip()
        thinking = str(data["message"].get("thinking") or "").strip()
    return {"ok": True, "answer": answer, "thinking": thinking, "model": model, "think_mode": request["think_mode"], "state_context": state_context, "raw": data}


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
    in_think_markup = False
    try:
        for data in stream_chat(
            base_url=request["base_url"],
            model=model,
            messages=request["messages"],
            keep_alive=request["settings"]["keep_alive"],
            think=_think_flag(request["think_mode"]),
            options=_ollama_options(request["settings"]),
            timeout_seconds=240.0,
        ):
            message_row = data.get("message") if isinstance(data.get("message"), dict) else {}
            thinking_delta = str(message_row.get("thinking") or "")
            content_delta = str(message_row.get("content") or "")
            if thinking_delta:
                yield event({"type": "thinking", "delta": thinking_delta})
            if content_delta:
                split_rows, in_think_markup = _split_think_markup(content_delta, in_think=in_think_markup)
                for row_type, delta in split_rows:
                    if delta:
                        yield event({"type": row_type, "delta": delta})
            if data.get("done"):
                metrics = {
                    "total_seconds": _duration_seconds(data.get("total_duration")),
                    "load_seconds": _duration_seconds(data.get("load_duration")),
                    "prompt_tokens": data.get("prompt_eval_count"),
                    "answer_tokens": data.get("eval_count"),
                    "tokens_per_second": _tokens_per_second(data.get("eval_count"), data.get("eval_duration")),
                }
                yield event({"type": "done", "metrics": metrics, "raw": data})
                return
    except Exception as exc:
        yield event({"type": "error", "error": f"{type(exc).__name__}: {exc}", "state_context": state_context})
