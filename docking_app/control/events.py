from __future__ import annotations

import time
from collections import deque
from typing import Any


_EVENTS: deque[dict[str, Any]] = deque(maxlen=50)
_EVENT_COUNTER = 0


def publish_control_event(envelope: dict[str, Any]) -> dict[str, Any] | None:
    if not envelope.get("ok"):
        return None
    ui_hints = envelope.get("ui_hints") if isinstance(envelope.get("ui_hints"), dict) else {}
    if not ui_hints:
        return None
    global _EVENT_COUNTER
    _EVENT_COUNTER += 1
    event = {
        "id": _EVENT_COUNTER,
        "created_at": time.time(),
        "action": str(envelope.get("action") or ""),
        "trace_id": str(envelope.get("trace_id") or ""),
        "message": str(envelope.get("message") or ""),
        "data": envelope.get("data") if isinstance(envelope.get("data"), dict) else {},
        "ui_hints": ui_hints,
    }
    _EVENTS.append(event)
    return dict(event)


def latest_event(after_id: int = 0) -> dict[str, Any]:
    after = max(0, int(after_id or 0))
    for event in reversed(_EVENTS):
        event_id = int(event.get("id") or 0)
        if event_id > after:
            return {"ok": True, "event": dict(event), "latest_id": event_id}
    latest_id = int(_EVENTS[-1].get("id") or 0) if _EVENTS else 0
    return {"ok": True, "event": None, "latest_id": latest_id}


def clear_events() -> None:
    _EVENTS.clear()
