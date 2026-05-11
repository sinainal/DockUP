from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def _api_url(base_url: str, path: str) -> str:
    return f"{str(base_url or DEFAULT_BASE_URL).rstrip('/')}/{path.lstrip('/')}"


def _request_json(base_url: str, path: str, payload: dict[str, Any] | None = None, *, method: str = "GET") -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    request = Request(_api_url(base_url, path), data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {"error": str(exc)}
        body.setdefault("status_code", exc.code)
        return body
    except URLError as exc:
        return {"error": f"Could not reach DockUP backend: {exc.reason}"}


def _stream_post(base_url: str, path: str, payload: dict[str, Any]):
    request = Request(
        _api_url(base_url, path),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
        method="POST",
    )
    with urlopen(request, timeout=None) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if line:
                yield line


def _print_json(payload: dict[str, Any], pretty: bool = False) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None))


def cmd_state(args: argparse.Namespace) -> int:
    payload = _request_json(args.base_url, "/api/state")
    _print_json(payload, args.pretty)
    return 0 if not payload.get("error") else 2


def cmd_ask(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {"message": args.message}
    if args.model:
        payload["model"] = args.model
    if args.think_mode:
        payload["think_mode"] = args.think_mode
    if args.raw:
        for line in _stream_post(args.base_url, "/api/extensions/ollama/chat/stream", payload):
            print(line, flush=True)
        return 0
    for line in _stream_post(args.base_url, "/api/extensions/ollama/chat/stream", payload):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            print(line, flush=True)
            continue
        event_type = str(event.get("type") or "")
        if event_type == "tool_call":
            print(f"tool: {event.get('prompt') or event.get('tool')}", flush=True)
        elif event_type == "status":
            print(f"status: {event.get('delta') or ''}", flush=True)
        elif event_type == "answer":
            print(str(event.get("delta") or ""), end="", flush=True)
        elif event_type == "error":
            print(f"error: {event.get('error') or 'unknown error'}", file=sys.stderr)
            return 2
        elif event_type == "done":
            if not str(event.get("raw") or "").strip():
                print()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {"is_test_mode": bool(args.test_mode)}
    if args.batch_id:
        try:
            payload["batch_id"] = int(args.batch_id)
        except ValueError:
            print("batch_id must be numeric because the UI run endpoint uses numeric batch IDs.", file=sys.stderr)
            return 2
    result = _request_json(args.base_url, "/api/run/start", payload)
    _print_json(result, args.pretty)
    if result.get("error"):
        return 2
    if not args.stream:
        return 0
    while True:
        status = _request_json(args.base_url, "/api/run/status")
        if args.pretty:
            _print_json(status, True)
        else:
            print(
                f"run={status.get('status')} completed={status.get('completed_runs', 0)}/{status.get('total_runs', 0)} out={status.get('out_root') or '-'}",
                flush=True,
            )
        if str(status.get("status") or "") not in {"running", "stopping"}:
            return 0 if str(status.get("status") or "") in {"done", "completed", "idle"} else 2
        time.sleep(max(0.2, float(args.interval)))


def cmd_eval(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parent
    command = [
        sys.executable,
        str(repo_root / "run_dockup_agent_suite.py"),
        "--suite",
        args.suite,
        "--base-url",
        args.ollama_url,
        "--think-mode",
        args.think_mode,
        "--timeout-seconds",
        str(args.timeout_seconds),
    ]
    return int(subprocess.run(command, check=False).returncode)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DockUP agent operator CLI")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="DockUP backend URL, not Ollama URL.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    state = sub.add_parser("state", help="Read the same state used by the UI.")
    state.add_argument("--pretty", action="store_true")
    state.set_defaults(func=cmd_state)

    ask = sub.add_parser("ask", help="Ask the DockUP agent through the UI streaming endpoint.")
    ask.add_argument("message")
    ask.add_argument("--model", default="")
    ask.add_argument("--think-mode", choices=["auto", "think", "no_think"], default="")
    ask.add_argument("--raw", action="store_true", help="Print raw NDJSON events.")
    ask.set_defaults(func=cmd_ask)

    run = sub.add_parser("run", help="Start the current UI queue through /api/run/start.")
    run.add_argument("--batch-id", default="")
    run.add_argument("--test-mode", action="store_true")
    run.add_argument("--stream", action="store_true", help="Poll /api/run/status until the active run finishes.")
    run.add_argument("--interval", type=float, default=1.0)
    run.add_argument("--pretty", action="store_true")
    run.set_defaults(func=cmd_run)

    eval_cmd = sub.add_parser("eval", help="Run live agent evaluation suites.")
    eval_cmd.add_argument("--suite", choices=["hard10", "hard30"], default="hard30")
    eval_cmd.add_argument("--ollama-url", default="http://localhost:11434")
    eval_cmd.add_argument("--think-mode", choices=["auto", "think", "no_think"], default="auto")
    eval_cmd.add_argument("--timeout-seconds", type=float, default=120.0)
    eval_cmd.set_defaults(func=cmd_eval)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
