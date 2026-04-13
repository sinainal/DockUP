from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest

from tests._support.api_client import ApiClient


@dataclass(frozen=True)
class TestConfig:
    base_url: str
    e2e_timeout: int
    poll_interval: float
    repo_root: Path
    workspace: Path
    dock_dir: Path
    ligand_dir: Path
    receptor_dir: Path


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--base-url",
        action="store",
        default="http://localhost:8000",
        help="DockUP API base URL.",
    )
    parser.addoption(
        "--e2e-timeout",
        action="store",
        type=int,
        default=20 * 60,
        help="Max wait seconds for long-running E2E actions.",
    )
    parser.addoption(
        "--poll-interval",
        action="store",
        type=float,
        default=2.0,
        help="Polling interval seconds for status waits.",
    )
    parser.addoption(
        "--run-legacy",
        action="store_true",
        default=False,
        help="Also run legacy heavy test modules.",
    )
    parser.addoption(
        "--e2e-artifacts-dir",
        action="store",
        default="",
        help="Optional directory to persist E2E artifacts before cleanup.",
    )
    parser.addoption(
        "--test-log-path",
        action="store",
        default="",
        help="Optional path for run summary log file (default: tests/.last_test_log.txt).",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "unit: Pure unit tests without live server.")
    config.addinivalue_line("markers", "api: API integration tests against live FastAPI app.")
    config.addinivalue_line("markers", "browser: Real browser UI regression tests.")
    config.addinivalue_line("markers", "e2e: End-to-end flow tests.")
    config.addinivalue_line("markers", "render: Report render/plot flow tests.")
    config.addinivalue_line("markers", "slow: Long-running tests.")
    config.addinivalue_line("markers", "legacy: Legacy broad/regression suites.")
    config.addinivalue_line("markers", "strict_clean: Strict clean-install profile tests.")
    config.addinivalue_line("markers", "legacy_data: Tests expecting optional legacy/preloaded datasets.")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-legacy"):
        return
    skip_legacy = pytest.mark.skip(reason="Legacy suite disabled (use --run-legacy to include).")
    for item in items:
        if "legacy" in item.keywords:
            item.add_marker(skip_legacy)


def pytest_sessionstart(session: pytest.Session) -> None:
    now = datetime.now()
    session.config._dockup_runlog = {
        "passed": [],
        "failed": [],
        "skipped": [],
        "details": {},
        "started_at": now.isoformat(timespec="seconds"),
        "started_perf": perf_counter(),
    }


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]) -> Any:
    outcome = yield
    report: pytest.TestReport = outcome.get_result()
    log = getattr(item.config, "_dockup_runlog", None)
    if log is None:
        return

    details = log.setdefault("details", {})
    rec = details.setdefault(
        report.nodeid,
        {"status": "", "duration_sec": 0.0, "error": "", "when": ""},
    )
    if report.when == "call":
        rec["duration_sec"] = float(getattr(report, "duration", 0.0) or 0.0)

    # Handle setup/call/teardown deterministically:
    # - failures on any phase are failures
    # - skips on setup/call are skips
    # - pass is counted only on call phase
    if report.failed:
        if rec["status"] == "failed":
            return
        err = str(report.longrepr) if report.longrepr else "Unknown error"
        short_err = "\n".join(err.splitlines()[:8])
        rec["status"] = "failed"
        rec["error"] = short_err
        rec["when"] = report.when
        log["failed"].append({"nodeid": report.nodeid, "error": short_err, "when": report.when})
        return
    if report.skipped and report.when in {"setup", "call"}:
        if not rec["status"]:
            rec["status"] = "skipped"
            rec["when"] = report.when
            log["skipped"].append(report.nodeid)
        return
    if report.passed and report.when == "call":
        if not rec["status"]:
            rec["status"] = "passed"
            rec["when"] = "call"
            log["passed"].append(report.nodeid)


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    log = getattr(config, "_dockup_runlog", {"passed": [], "failed": [], "skipped": []})
    tests_dir = Path(__file__).resolve().parent
    user_log_path = str(config.getoption("--test-log-path") or "").strip()
    log_path = Path(user_log_path) if user_log_path else (tests_dir / ".last_test_log.txt")
    if not log_path.is_absolute():
        log_path = (Path.cwd() / log_path).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    started_perf = float(log.get("started_perf") or 0.0)
    elapsed = max(0.0, perf_counter() - started_perf) if started_perf else 0.0
    started_at = str(log.get("started_at") or "")
    finished_at = datetime.now().isoformat(timespec="seconds")

    details = log.get("details") or {}
    passed_with_dur = []
    failed_with_dur = []
    skipped_with_dur = []
    for nodeid, rec in details.items():
        row = (nodeid, float(rec.get("duration_sec") or 0.0), str(rec.get("error") or ""))
        status = str(rec.get("status") or "")
        if status == "passed":
            passed_with_dur.append(row)
        elif status == "failed":
            failed_with_dur.append(row)
        elif status == "skipped":
            skipped_with_dur.append(row)

    lines: list[str] = []
    lines.append("DockUP Test Summary")
    lines.append(f"started_at={started_at}")
    lines.append(f"finished_at={finished_at}")
    lines.append(f"elapsed_sec={elapsed:.3f}")
    lines.append(f"passed={len(log['passed'])} failed={len(log['failed'])} skipped={len(log['skipped'])}")
    if passed_with_dur:
        lines.append("Passed tests:")
        for nodeid, dur, _ in sorted(passed_with_dur, key=lambda r: r[1], reverse=True):
            lines.append(f"- {nodeid} ({dur:.3f}s)")
    if failed_with_dur:
        lines.append("Failed tests:")
        for nodeid, dur, err in sorted(failed_with_dur, key=lambda r: r[1], reverse=True):
            lines.append(f"- {nodeid} ({dur:.3f}s)")
            lines.append(f"  error: {err}")
    if skipped_with_dur:
        lines.append("Skipped tests:")
        for nodeid, dur, _ in sorted(skipped_with_dur, key=lambda r: r[1], reverse=True):
            lines.append(f"- {nodeid} ({dur:.3f}s)")
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture(scope="session")
def test_cfg(pytestconfig: pytest.Config) -> TestConfig:
    repo_root = Path(__file__).resolve().parents[1]
    workspace = repo_root / "docking_app" / "workspace"
    return TestConfig(
        base_url=str(pytestconfig.getoption("--base-url")).rstrip("/"),
        e2e_timeout=int(pytestconfig.getoption("--e2e-timeout")),
        poll_interval=float(pytestconfig.getoption("--poll-interval")),
        repo_root=repo_root,
        workspace=workspace,
        dock_dir=workspace / "data" / "dock",
        ligand_dir=workspace / "data" / "ligand",
        receptor_dir=workspace / "data" / "receptor",
    )


@pytest.fixture(scope="session")
def api(test_cfg: TestConfig) -> ApiClient:
    return ApiClient(test_cfg.base_url)


def _ensure_seed_workspace_fixtures(test_cfg: TestConfig) -> list[Path]:
    created_paths: list[Path] = []
    test_cfg.ligand_dir.mkdir(parents=True, exist_ok=True)
    ligand_files = sorted(path for path in test_cfg.ligand_dir.glob("*.sdf") if path.is_file())
    if not ligand_files:
        ligand_path = test_cfg.ligand_dir / "seed_fixture.sdf"
        ligand_path.write_text(
            "\n  Ketcher\n\n  0  0  0     0  0            999 V2000\nM  END\n$$$$\n",
            encoding="utf-8",
        )
        created_paths.append(ligand_path)

    test_cfg.receptor_dir.mkdir(parents=True, exist_ok=True)
    receptor_path = test_cfg.receptor_dir / "6CM4.pdb"
    if not receptor_path.exists():
        receptor_path.write_text(
            "HEADER    TEST RECEPTOR\nATOM      1  N   GLY A   1      11.104  13.207   9.947  1.00 20.00           N\nEND\n",
            encoding="utf-8",
        )
        created_paths.append(receptor_path)

    test_cfg.dock_dir.mkdir(parents=True, exist_ok=True)
    result_files = sorted(test_cfg.dock_dir.rglob("results.json"))
    if not result_files:
        run_dir = test_cfg.dock_dir / "seed_scan" / "6CM4" / "seed_fixture" / "run1"
        run_dir.mkdir(parents=True, exist_ok=True)
        results_path = run_dir / "results.json"
        results_path.write_text(
            json.dumps(
                {
                    "mock": {
                        "best_affinity": -8.5,
                        "rmsd": 1.2,
                        "job_type": "Docking",
                        "docking_mode": "standard",
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        created_paths.append(results_path)
    return created_paths


def _cleanup_seed_workspace_fixtures(test_cfg: TestConfig, created_paths: list[Path]) -> None:
    for path in reversed(created_paths):
        if path.name == "results.json":
            parent = path.parent
            path.unlink(missing_ok=True)
            while parent != test_cfg.dock_dir and parent.exists():
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        else:
            path.unlink(missing_ok=True)


@pytest.fixture(scope="session")
def ensure_seed_workspace_fixtures(test_cfg: TestConfig) -> None:
    created_paths = _ensure_seed_workspace_fixtures(test_cfg)

    try:
        yield
    finally:
        _cleanup_seed_workspace_fixtures(test_cfg, created_paths)


@pytest.fixture(scope="session", autouse=True)
def _autoload_seed_workspace_fixtures(ensure_seed_workspace_fixtures: None) -> None:
    yield


@pytest.fixture(autouse=True)
def refresh_seed_workspace_fixtures(test_cfg: TestConfig) -> None:
    _ensure_seed_workspace_fixtures(test_cfg)
    yield


@pytest.fixture(scope="session")
def e2e_artifacts_dir(pytestconfig: pytest.Config, test_cfg: TestConfig) -> Path | None:
    raw = str(pytestconfig.getoption("--e2e-artifacts-dir") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = (test_cfg.repo_root / path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="session")
def server_ready(api: ApiClient) -> None:
    try:
        resp = api.get("/api/state", timeout=10)
    except Exception as exc:  # pragma: no cover - network-dependent
        pytest.skip(f"DockUP server not reachable: {exc}")
    if resp.status_code != 200:
        pytest.skip(f"DockUP server not ready: HTTP {resp.status_code}")
    payload = api.json(resp)
    if "mode" not in payload:
        pytest.skip(f"Unexpected /api/state payload: {payload}")
