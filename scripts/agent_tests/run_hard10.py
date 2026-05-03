from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from docking_app.agent import autonomous_docking
from docking_app.extensions import ollama_agent
from docking_app.state import RUN_STATE, STATE, save_state_cache

from scripts.agent_tests.logger import AgentRunLogger
from scripts.agent_tests.suite import SeedBundle, build_hard10_cases, prepare_seed_bundle, reset_state


def _default_output_root() -> Path:
    return REPO_ROOT.parent / "agent tests"


def _pick_model(base_url: str) -> tuple[str, dict[str, Any]]:
    status = {}
    try:
        status = ollama_agent.status()
    except Exception as exc:
        status = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    model = str(status.get("model") or "").strip()
    if model:
        return model, status

    connected, version, models, error = ollama_agent.probe_ollama(base_url)
    status = {
        "connected": connected,
        "version": version,
        "models": [model.as_dict() for model in models],
        "error": error or "",
    }
    if models:
        score_rows = [row.as_dict() for row in models]
        model = ollama_agent._preferred_model(score_rows, "")
    return model, status


def _cap_stream_timeout(max_seconds: float) -> None:
    original = ollama_agent.stream_chat

    def capped_stream_chat(*args: Any, **kwargs: Any):
        current = kwargs.get("timeout_seconds", 240.0)
        try:
            current_value = float(current)
        except (TypeError, ValueError):
            current_value = 240.0
        kwargs["timeout_seconds"] = min(max_seconds, current_value)
        yield from original(*args, **kwargs)

    ollama_agent.stream_chat = capped_stream_chat  # type: ignore[assignment]


def _restore_stream_timeout(original: Any) -> None:
    ollama_agent.stream_chat = original  # type: ignore[assignment]


def _prompt_context(bundle: SeedBundle) -> dict[str, Any]:
    ligand_names = list(bundle.ligand_names or [bundle.ligand_name])
    receptor_names = list(bundle.receptor_files.keys())
    while len(ligand_names) < 3:
        ligand_names.append(ligand_names[-1])
    while len(receptor_names) < 3:
        receptor_names.append(receptor_names[-1])
    return {
        "ligand": bundle.ligand_name,
        "ligands": ";".join(ligand_names),
        "ligand_list": ", ".join(ligand_names),
        "ligand_a": ligand_names[0],
        "ligand_b": ligand_names[1],
        "ligand_c": ligand_names[2],
        "primary_ligand": ligand_names[0],
        "secondary_ligand": ligand_names[1],
        "tertiary_ligand": ligand_names[2],
        "receptors": ",".join(receptor_names),
        "receptor_list": ", ".join(receptor_names),
        "receptor_a": receptor_names[0],
        "receptor_b": receptor_names[1],
        "receptor_c": receptor_names[2],
        "primary_receptor": receptor_names[0],
        "secondary_receptor": receptor_names[1],
        "tertiary_receptor": receptor_names[2],
    }


def _format_prompt(template: str, bundle: SeedBundle) -> str:
    try:
        return template.format(**_prompt_context(bundle))
    except Exception:
        return template.format(ligand=bundle.ligand_name)


def _evaluation_record(ok: bool, note: str, result: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": ok and bool(result.get("ok", False)),
        "note": note,
        "stopped_reason": str(result.get("stopped_reason") or ""),
        "tools": [row.get("tool") for row in result.get("trace") or [] if isinstance(row, dict) and row.get("tool")],
        "event_count": len(events),
        "answer_len": len(str(result.get("answer") or "")),
        "thinking_len": len(str(result.get("thinking") or "")),
    }


def run_suite(
    args: argparse.Namespace,
    *,
    suite_name: str = "DockUP hard 10",
    case_builder=build_hard10_cases,
) -> int:
    output_root = Path(args.output_root or _default_output_root()).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    model, status = _pick_model(args.base_url)
    if not model:
        print("No Ollama model is available. Connect Ollama and try again.", file=sys.stderr)
        return 2

    logger = AgentRunLogger(
        root_dir=output_root,
        suite_name=suite_name,
        model_name=model,
        think_mode=args.think_mode,
    )

    bundle = prepare_seed_bundle(logger.run_dir)
    manifest = {
        "suite": suite_name,
        "model": model,
        "base_url": args.base_url,
        "think_mode": args.think_mode,
        "started_at": logger.started_at,
        "status_snapshot": status,
        "inputs": {
            "receptors": {key: str(path) for key, path in bundle.receptor_files.items()},
            "ligand_name": bundle.ligand_name,
            "ligand_path": str(bundle.ligand_path),
            "ligand_names": list(bundle.ligand_names or [bundle.ligand_name]),
            "ligand_paths": {key: str(path) for key, path in bundle.ligand_paths.items()},
        },
    }
    logger.write_manifest(manifest)

    state_snapshot = copy.deepcopy(STATE)
    run_snapshot = copy.deepcopy(RUN_STATE)
    agent_snapshot = copy.deepcopy(autonomous_docking.AGENT_STATE)
    original_stream_chat = ollama_agent.stream_chat
    _cap_stream_timeout(float(args.timeout_seconds))

    rows: list[dict[str, Any]] = []
    failures = 0
    try:
        for index, case in enumerate(case_builder(), start=1):
            reset_state(bundle)
            if case.prepare:
                case.prepare(bundle)
            events: list[dict[str, Any]] = []
            prompt = _format_prompt(case.prompt, bundle)
            payload = {
                "model": model,
                "message": prompt,
                "think_mode": case.think_mode,
                "history": [],
            }
            request = ollama_agent._build_chat_request(payload)
            if not request.get("model"):
                raise RuntimeError("Resolved model is empty after building the request.")

            started = time.perf_counter()
            result = ollama_agent._run_single_agent_tool_loop(
                payload,
                request,
                progress_callback=events.append,
                max_steps=case.max_steps,
            )
            elapsed = round(time.perf_counter() - started, 3)
            ok, note = case.check(result, events, bundle) if case.check else (bool(result.get("ok", False)), "")
            record = _evaluation_record(ok, note, result, events)
            record.update(
                {
                    "index": index,
                    "case_id": case.case_id,
                    "seconds": elapsed,
                    "status": "pass" if ok and result.get("ok", False) else "fail",
                    "prompt": prompt,
                    "answer": str(result.get("answer") or ""),
                    "thinking": str(result.get("thinking") or ""),
                    "stopped_reason": str(result.get("stopped_reason") or ""),
                    "tool_trace": [row for row in result.get("trace") or [] if isinstance(row, dict)],
                    "events": events,
                    "case_notes": case.notes,
                }
            )
            if record["status"] != "pass":
                failures += 1

            logger.write_case_artifacts(
                index=index,
                case_id=case.case_id,
                prompt=prompt,
                events=events,
                result=result,
                evaluation=record,
            )
            rows.append(
                {
                    "index": index,
                    "case_id": case.case_id,
                    "status": record["status"],
                    "seconds": elapsed,
                    "tools": record["tools"],
                    "note": note or case.notes,
                }
            )
            print(
                f"[{index:02d}] {case.case_id}: {record['status']} in {elapsed:.2f}s | tools={', '.join(record['tools']) or '-'} | {note or case.notes}"
            )
    finally:
        logger.write_summary(rows)
        STATE.clear()
        STATE.update(state_snapshot)
        RUN_STATE.clear()
        RUN_STATE.update(run_snapshot)
        autonomous_docking.AGENT_STATE.clear()
        autonomous_docking.AGENT_STATE.update(agent_snapshot)
        save_state_cache()
        for path in bundle.cleanup_paths:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        _restore_stream_timeout(original_stream_chat)

    print(f"Suite complete. failures={failures}. Logs: {logger.run_dir}")
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the DockUP hard 10 live-agent suite.")
    parser.add_argument("--output-root", default="", help="Directory that will receive the agent tests run folder.")
    parser.add_argument("--base-url", default="http://localhost:11434", help="Ollama base URL used for model probing.")
    parser.add_argument("--think-mode", default="auto", choices=["auto", "think", "no_think"], help="Think mode for the suite.")
    parser.add_argument("--timeout-seconds", type=float, default=120.0, help="Cap the streaming timeout per model turn.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run_suite(args)


if __name__ == "__main__":
    raise SystemExit(main())
