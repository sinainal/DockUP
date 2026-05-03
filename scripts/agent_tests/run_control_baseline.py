from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.agent_tests.run_hard10 import build_parser as _build_parser, run_suite
from scripts.agent_tests.suite import build_agent_control_baseline_cases


def build_parser() -> argparse.ArgumentParser:
    parser = _build_parser()
    parser.description = "Run the DockUP agent-control baseline 10 suite."
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run_suite(
        args,
        suite_name="DockUP agent control baseline 10",
        case_builder=build_agent_control_baseline_cases,
    )


if __name__ == "__main__":
    raise SystemExit(main())
