from __future__ import annotations

import json
import re
from typing import Any


AGENT_SYSTEM_PROMPT = """You are DockUP Local AI, an autonomous scientific docking agent.

Understand the user's goal, choose useful DockUP tools, inspect each result, and continue until the goal is complete or one critical input is missing.
Use the compact state, recent attempts, and tool results as working memory.
Do not repeat the same failed attempt. If evidence clearly suggests a different fix, try it once; otherwise ask one short question.
For simple requests, do exactly the requested action and stop. For full docking requests, complete missing prerequisites and build or start the run as requested.
Keep final answers concise. Do not expose hidden reasoning."""


def _short_text(value: Any, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[\s,;]+", ";", text)
    text = re.sub(r"[^a-z0-9_.:+\\[\\]-]+", "", text)
    return text.strip(";")


def normalize_attempt_signature(tool_name: str, arguments: dict[str, Any]) -> str:
    clean_args = dict(arguments or {})
    if tool_name == "fetch_assets":
        clean_args = {
            "receptors": _normalize_text(clean_args.get("receptors")),
            "ligands": _normalize_text(clean_args.get("ligands")),
        }
    elif tool_name in {"delete_ligands", "delete_receptors"}:
        clean_args = {"target": _normalize_text(clean_args.get("target"))}
    elif tool_name == "delete_queue_batches":
        clean_args = {"batch_id": _normalize_text(clean_args.get("batch_id"))}
    try:
        encoded = json.dumps(clean_args, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        encoded = "{}"
    return f"{tool_name}:{encoded}"


def recent_attempts(agent_state: dict[str, Any], limit: int = 6) -> list[dict[str, Any]]:
    rows = agent_state.get("attempt_ledger")
    if not isinstance(rows, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for row in rows[-limit:]:
        if not isinstance(row, dict):
            continue
        cleaned.append(
            {
                "tool": str(row.get("tool") or ""),
                "summary": _short_text(row.get("summary"), 160),
                "ok": bool(row.get("ok", True)),
                "verification": _short_text(row.get("verification"), 160),
            }
        )
    return cleaned


def was_failed_attempt(agent_state: dict[str, Any], tool_name: str, arguments: dict[str, Any]) -> bool:
    signature = normalize_attempt_signature(tool_name, arguments)
    rows = agent_state.get("attempt_ledger")
    if not isinstance(rows, list):
        return False
    return any(isinstance(row, dict) and row.get("signature") == signature and row.get("ok") is False for row in rows[-12:])


def _context_value(context: dict[str, Any], key: str, default: Any = None) -> Any:
    if not isinstance(context, dict):
        return default
    return context.get(key, default)


def verify_tool_effect(tool_name: str, result: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> str:
    ok = bool(result.get("ok", True))
    if tool_name == "fetch_assets":
        loaded = result.get("loaded_receptors") or []
        saved = result.get("saved_ligands") or []
        failed = (result.get("failed_receptors") or []) + (result.get("failed_ligands") or [])
        if loaded or saved:
            return f"State changed: loaded {len(loaded)} receptor(s), saved {len(saved)} ligand(s)."
        if failed:
            return "No new asset was added; retry only with a meaningfully corrected name."
        return "No new asset was added."
    if tool_name == "select_workspace":
        before_receptor = _context_value(before, "selected_receptor", "")
        after_receptor = _context_value(after, "selected_receptor", "")
        rows = len(result.get("selected") or [])
        return f"Workspace rows={rows}; selected receptor {before_receptor or '-'} -> {after_receptor or '-'}."
    if tool_name == "set_gridbox":
        before_count = int(_context_value(before, "gridbox_count", 0) or 0)
        after_count = int(_context_value(after, "gridbox_count", 0) or 0)
        return f"Gridbox count {before_count} -> {after_count}."
    if tool_name == "set_docking_config":
        has_config = bool(_context_value(after, "docking_config", {}) or {})
        return "Docking config is present." if has_config else "Docking config is still missing."
    if tool_name == "build_or_run_queue":
        before_queue = int(_context_value(before, "queue_count", 0) or 0)
        after_queue = int(_context_value(after, "queue_count", 0) or 0)
        run = result.get("run") if isinstance(result.get("run"), dict) else {}
        if run.get("started"):
            return f"Queue count {before_queue} -> {after_queue}; real run was submitted."
        return f"Queue count {before_queue} -> {after_queue}."
    if tool_name in {"delete_ligands", "delete_receptors", "delete_queue_batches"}:
        return "State cleanup completed." if ok else "Cleanup did not complete."
    if not ok:
        return "Tool failed; use the error evidence before deciding the next action."
    return "Tool completed; inspect current state before repeating it."


def record_attempt(
    agent_state: dict[str, Any],
    *,
    step: int,
    tool_name: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
    verification: str,
    summary: str,
) -> None:
    rows = agent_state.get("attempt_ledger")
    if not isinstance(rows, list):
        rows = []
    rows.append(
        {
            "step": step,
            "tool": tool_name,
            "signature": normalize_attempt_signature(tool_name, arguments),
            "arguments": dict(arguments or {}),
            "ok": bool(result.get("ok", True)),
            "summary": _short_text(summary, 220),
            "verification": _short_text(verification, 220),
        }
    )
    agent_state["attempt_ledger"] = rows[-16:]


def build_agent_working_memory(*, user_goal: str, state_context: dict[str, Any], agent_state: dict[str, Any]) -> str:
    memory = state_context.get("agent_memory") if isinstance(state_context.get("agent_memory"), dict) else {}
    attempts = recent_attempts(agent_state)
    queue_batches = state_context.get("queue_batches") if isinstance(state_context.get("queue_batches"), list) else []
    lines = [
        "Goal:",
        f"- {_short_text(user_goal, 320) or 'No user goal provided.'}",
        "",
        "Current state:",
        f"- {state_context.get('state_summary') or '-'}",
        f"- queue_total_runs={state_context.get('queue_total_runs', 0)}; run_out_root={state_context.get('run_out_root') or '-'}",
        f"- config_engine={(state_context.get('docking_config') or {}).get('docking_engine') or '-'}; config_mode={(state_context.get('docking_config') or {}).get('docking_mode') or '-'}",
    ]
    if queue_batches:
        preview = ", ".join(
            f"{row.get('batch_id')}:{row.get('job_count')} jobs x{row.get('run_count')} {((row.get('docking_config') or {}).get('docking_engine') or '-')}/{((row.get('docking_config') or {}).get('docking_mode') or '-')}"
            for row in queue_batches[:4]
            if isinstance(row, dict)
        )
        lines.append(f"- queue_batches={preview or '-'}")
    lines.extend(
        [
            "",
            "Recent memory:",
            f"- last_tool={memory.get('last_tool') or '-'}; last_error={memory.get('last_error') or '-'}",
            f"- summary={memory.get('memory_summary') or '-'}",
            "",
            "Recent attempts:",
        ]
    )
    if attempts:
        for row in attempts:
            status = "ok" if row.get("ok") else "failed"
            lines.append(f"- {row.get('tool') or '-'} [{status}]: {row.get('summary') or '-'}; {row.get('verification') or '-'}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "Choose the next useful tool freely. Avoid repeating failed attempts unless the arguments are meaningfully different.",
            "For multi-config experiments, append new batches with replace_queue=false instead of clearing the queue.",
        ]
    )
    return "\n".join(lines)
