from __future__ import annotations

import json
import math
from typing import Any


def payload_usage(payload: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    tools = payload.get("tools") if isinstance(payload.get("tools"), list) else []
    options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    try:
        window_tokens = max(1, int(options.get("num_ctx") or 0))
    except (TypeError, ValueError):
        window_tokens = 0
    token_estimate = max(1, math.ceil(len(text) / 4)) if text else 0
    return {
        "payload_chars": len(text),
        "payload_tokens_est": token_estimate,
        "window_tokens": window_tokens,
        "window_percent": min(100, round((token_estimate / window_tokens) * 100)) if window_tokens else 0,
        "message_count": len(messages),
        "tool_count": len(tools),
        "system_message_count": sum(
            1 for row in messages if isinstance(row, dict) and str(row.get("role") or "") == "system"
        ),
    }
