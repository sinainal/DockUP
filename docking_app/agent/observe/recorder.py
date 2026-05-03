from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config import BASE
from .metrics import payload_usage


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _slugify(value: Any) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-._")
    return slug or "agent-run"


def _default_root() -> Path:
    return BASE.parent / "agent tests" / "_observer"


@dataclass
class AgentObserver:
    root_dir: Path
    label: str
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    event_index: int = 0
    run_dir: Path = field(init=False)
    steps_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(self.started_at))
        self.run_dir = self.root_dir / f"{stamp}_{_slugify(self.label)}"
        self.steps_dir = self.run_dir / "steps"
        self.steps_dir.mkdir(parents=True, exist_ok=True)
        self.write_json("run.json", {"started_at": self.started_at, "label": self.label, "metadata": self.metadata})

    def write_json(self, relative: str, payload: Any) -> Path:
        path = self.run_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
        return path

    def event(self, kind: str, payload: dict[str, Any]) -> None:
        self.event_index += 1
        row = {
            "index": self.event_index,
            "time": time.time(),
            "kind": kind,
            "payload": payload,
        }
        with (self.run_dir / "events.ndjson").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def model_request(self, step: int, payload: dict[str, Any]) -> None:
        usage = payload_usage(payload)
        self.write_json(f"steps/{step:03d}_model_request.json", {"payload": payload, "usage": usage})
        self.event("model_request", {"step": step, "usage": usage})

    def model_response(self, step: int, payload: dict[str, Any]) -> None:
        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
        content = str(message.get("content") or "")
        thinking = str(message.get("thinking") or "")
        self.write_json(f"steps/{step:03d}_model_response.json", payload)
        self.event(
            "model_response",
            {
                "step": step,
                "content_chars": len(content),
                "thinking_chars": len(thinking),
                "tool_call_count": len(tool_calls),
            },
        )

    def tool_call(self, step: int, tool: str, arguments: dict[str, Any]) -> None:
        self.event("tool_call", {"step": step, "tool": tool, "arguments": arguments})

    def tool_result(
        self,
        step: int,
        tool: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        *,
        seconds: float,
        before_context: dict[str, Any],
        after_context: dict[str, Any],
        verification: str,
    ) -> None:
        ok = bool(result.get("ok", True))
        self.write_json(
            f"steps/{step:03d}_{_slugify(tool)}_result.json",
            {
                "tool": tool,
                "arguments": arguments,
                "result": result,
                "seconds": seconds,
                "before_context": before_context,
                "after_context": after_context,
                "verification": verification,
            },
        )
        self.event(
            "tool_result",
            {
                "step": step,
                "tool": tool,
                "ok": ok,
                "seconds": seconds,
                "summary": str(result.get("summary") or result.get("error") or "")[:500],
                "verification": verification,
            },
        )

    def finish(self, result: dict[str, Any]) -> None:
        trace = result.get("trace") if isinstance(result.get("trace"), list) else []
        tool_rows = [row for row in trace if isinstance(row, dict) and row.get("tool")]
        failed = [
            row
            for row in tool_rows
            if isinstance(row.get("result"), dict) and not bool(row["result"].get("ok", True))
        ]
        summary = {
            "ok": bool(result.get("ok", False)),
            "stopped_reason": str(result.get("stopped_reason") or ""),
            "answer_chars": len(str(result.get("answer") or "")),
            "thinking_chars": len(str(result.get("thinking") or "")),
            "tool_call_count": len(tool_rows),
            "tool_success_count": len(tool_rows) - len(failed),
            "tool_failure_count": len(failed),
            "seconds": round(time.time() - self.started_at, 3),
        }
        self.write_json("summary.json", summary)
        self.event("finish", summary)


def observer_from_payload(payload: dict[str, Any], request: dict[str, Any]) -> AgentObserver | None:
    enabled = (
        _truthy(payload.get("agent_observer"))
        or _truthy(payload.get("observer_enabled"))
        or _truthy(payload.get("debug_trace"))
        or _truthy(os.environ.get("DOCKUP_AGENT_OBSERVER"))
    )
    if not enabled:
        return None
    root = Path(payload.get("observer_output_root") or os.environ.get("DOCKUP_AGENT_OBSERVER_ROOT") or _default_root())
    label = str(payload.get("agent_run_label") or payload.get("case_id") or request.get("message") or "agent-run")
    metadata = {
        "model": request.get("model"),
        "think_mode": request.get("think_mode"),
        "message": request.get("message"),
        "state_context": request.get("state_context"),
    }
    return AgentObserver(root_dir=root.expanduser().resolve(), label=label, metadata=metadata)
