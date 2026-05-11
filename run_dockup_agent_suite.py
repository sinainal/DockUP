from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run DockUP live agent suites from the repo root.")
    parser.add_argument("--suite", choices=["hard10", "hard30"], default="hard30", help="Which live suite to run.")
    parser.add_argument("--dockup-root", default=".", help="Path to the DockUP project root.")
    parser.add_argument("--output-root", default="output/agent_tests", help="Directory that will receive the run folder.")
    parser.add_argument("--base-url", default="http://localhost:11434", help="Ollama base URL used for model probing.")
    parser.add_argument("--think-mode", default="auto", choices=["auto", "think", "no_think"], help="Think mode for the suite.")
    parser.add_argument("--timeout-seconds", type=float, default=120.0, help="Cap the streaming timeout per model turn.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    repo_root = Path(__file__).resolve().parent
    dockup_root = (repo_root / args.dockup_root).expanduser().resolve()
    script_name = "run_hard30.py" if args.suite == "hard30" else "run_hard10.py"
    script_path = dockup_root / "scripts" / "agent_tests" / script_name
    if not script_path.exists():
        print(f"DockUP suite launcher not found: {script_path}", file=sys.stderr)
        return 2

    command = [
        sys.executable,
        "-u",
        str(script_path),
        "--output-root",
        str((repo_root / args.output_root).expanduser().resolve()),
        "--base-url",
        args.base_url,
        "--think-mode",
        args.think_mode,
        "--timeout-seconds",
        str(args.timeout_seconds),
    ]
    completed = subprocess.run(command, check=False)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
