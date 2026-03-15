from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ..config import BASE
from .config import receptor_run_dir, resolve_p2rank_bin

_RUNTIME_LOCK = threading.Lock()
_RUNTIME_STATE: dict[str, Any] = {
    "job_id": 0,
    "status": "idle",
    "pdb_id": "",
    "message": "",
    "error": "",
    "started_at": None,
    "finished_at": None,
    "work_dir": "",
    "output_dir": "",
}


def get_runtime_state() -> dict[str, Any]:
    with _RUNTIME_LOCK:
        return dict(_RUNTIME_STATE)


def _safe_rmtree(path: str | os.PathLike[str] | None) -> None:
    if not path:
        return
    try:
        target = Path(path).expanduser()
    except (TypeError, OSError):
        return
    if not target.exists():
        return
    shutil.rmtree(target, ignore_errors=True)


def clear_runtime_state() -> dict[str, Any]:
    current = get_runtime_state()
    next_job_id = int(current.get("job_id") or 0) + 1
    with _RUNTIME_LOCK:
        _RUNTIME_STATE.update(
            {
                "job_id": next_job_id,
                "status": "idle",
                "pdb_id": "",
                "message": "",
                "error": "",
                "started_at": None,
                "finished_at": None,
                "work_dir": "",
                "output_dir": "",
            }
        )
        snapshot = dict(_RUNTIME_STATE)
    _safe_rmtree(current.get("work_dir"))
    return snapshot


def _set_state(**updates: Any) -> None:
    with _RUNTIME_LOCK:
        _RUNTIME_STATE.update(updates)


def _run_predict(job_id: int, pdb_id: str, receptor_file: Path, work_dir: Path) -> None:
    output_dir = work_dir / "output"
    input_file = work_dir / f"{pdb_id}.pdb"
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(receptor_file, input_file)

        prank = resolve_p2rank_bin()
        cmd = [
            str(prank),
            "predict",
            "-f",
            str(input_file),
            "-o",
            str(output_dir),
        ]
        env = os.environ.copy()
        if not shutil.which("java", path=env.get("PATH", "")):
            java_bin = BASE.parent / "pocket_test" / "p2rank_java" / "bin"
            java_exec = java_bin / "java"
            if java_exec.exists():
                env["PATH"] = f"{java_bin}:{env.get('PATH', '')}"
                env.setdefault("JAVA_HOME", str(java_bin.parent))

        result = subprocess.run(
            cmd,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "P2Rank failed").strip())

        current = get_runtime_state()
        if int(current.get("job_id") or 0) != int(job_id):
            _safe_rmtree(work_dir)
            return
        _set_state(
            status="done",
            message=f"Binding site prediction ready for {pdb_id}.",
            error="",
            finished_at=time.time(),
            output_dir=str(output_dir),
        )
    except Exception as exc:
        current = get_runtime_state()
        if int(current.get("job_id") or 0) != int(job_id):
            _safe_rmtree(work_dir)
            return
        _set_state(
            status="error",
            message="Binding site prediction failed.",
            error=str(exc),
            finished_at=time.time(),
            output_dir=str(output_dir),
        )


def run_p2rank_async(pdb_id: str, receptor_file: Path) -> dict[str, Any]:
    current = get_runtime_state()
    if current.get("status") == "running":
        if str(current.get("pdb_id") or "").upper() == str(pdb_id or "").upper():
            return current
        raise RuntimeError("Another binding site prediction is already running.")

    base_dir = receptor_run_dir(pdb_id)
    base_dir.mkdir(parents=True, exist_ok=True)
    job_id = int(current.get("job_id") or 0) + 1
    work_dir = base_dir / f"run_{int(time.time() * 1000)}"
    _set_state(
        job_id=job_id,
        status="running",
        pdb_id=pdb_id.upper(),
        message=f"Running P2Rank for {pdb_id.upper()}...",
        error="",
        started_at=time.time(),
        finished_at=None,
        work_dir=str(work_dir),
        output_dir=str(work_dir / "output"),
    )
    thread = threading.Thread(
        target=_run_predict,
        args=(job_id, pdb_id.upper(), receptor_file, work_dir),
        daemon=True,
    )
    thread.start()
    return get_runtime_state()
