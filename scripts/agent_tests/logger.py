from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-._")
    return slug or "run"


@dataclass
class AgentRunLogger:
    root_dir: Path
    suite_name: str
    model_name: str
    think_mode: str
    started_at: float = field(default_factory=time.time)
    run_id: str = field(init=False)
    run_dir: Path = field(init=False)
    cases_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(self.started_at))
        self.run_id = f"{stamp}_{_slugify(self.suite_name)}_{_slugify(self.model_name)}"
        self.run_dir = self.root_dir / self.run_id
        self.cases_dir = self.run_dir / "cases"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.cases_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _dump_json(path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")

    @staticmethod
    def _dump_text(path: Path, text: str) -> None:
        path.write_text(str(text or ""), encoding="utf-8")

    def write_manifest(self, data: dict[str, Any]) -> None:
        self._dump_json(self.run_dir / "manifest.json", data)

    def case_dir(self, index: int, case_id: str) -> Path:
        path = self.cases_dir / f"{index:02d}_{_slugify(case_id)}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_case_artifacts(
        self,
        *,
        index: int,
        case_id: str,
        prompt: str,
        events: list[dict[str, Any]],
        result: dict[str, Any],
        evaluation: dict[str, Any],
    ) -> Path:
        case_dir = self.case_dir(index, case_id)
        self._dump_text(case_dir / "prompt.txt", prompt)
        self._dump_text(case_dir / "answer.txt", str(result.get("answer") or result.get("error") or ""))
        self._dump_json(case_dir / "result.json", result)
        self._dump_json(case_dir / "evaluation.json", evaluation)
        ndjson = "\n".join(json.dumps(row, ensure_ascii=False, default=str) for row in events)
        self._dump_text(case_dir / "events.ndjson", ndjson + ("\n" if ndjson else ""))
        trace = result.get("trace") if isinstance(result.get("trace"), list) else []
        self._dump_json(case_dir / "trace.json", trace)
        self._dump_json(case_dir / "agent_state.json", result.get("agent_state") or {})
        return case_dir

    def write_summary(self, rows: list[dict[str, Any]]) -> None:
        self._dump_json(self.run_dir / "summary.json", rows)
        lines = [
            f"# {self.suite_name}",
            "",
            f"- model: `{self.model_name}`",
            f"- think mode: `{self.think_mode}`",
            f"- run id: `{self.run_id}`",
            "",
            "| # | Case | Status | Seconds | Tools | Note |",
            "|---|------|--------|---------|-------|------|",
        ]
        for row in rows:
            tools = ", ".join(row.get("tools") or [])
            note = str(row.get("note") or "").replace("\n", " ").strip()
            lines.append(
                f"| {row.get('index')} | {row.get('case_id')} | {row.get('status')} | {row.get('seconds')} | {tools} | {note} |"
            )
        self._dump_text(self.run_dir / "summary.md", "\n".join(lines) + "\n")
