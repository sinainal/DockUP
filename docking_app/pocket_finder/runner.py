from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ..config import BASE
from ..services import _filter_pdb_text_by_chain, _normalize_chain_id
from .config import receptor_run_dir, resolve_p2rank_bin

_RUNTIME_LOCK = threading.Lock()
_RUNTIME_STATE: dict[str, Any] = {
    "job_id": 0,
    "status": "idle",
    "pdb_id": "",
    "chain": "all",
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


def _is_completed_output_dir(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    return any(path.glob("*_predictions.csv"))


def latest_output_dir(pdb_id: str, chain: str = "all") -> Path | None:
    base_dir = receptor_run_dir(pdb_id, chain)
    if not base_dir.exists():
        return None
    candidates: list[tuple[float, Path]] = []
    legacy_output = base_dir / "output"
    if _is_completed_output_dir(legacy_output):
        try:
            candidates.append((legacy_output.stat().st_mtime, legacy_output))
        except OSError:
            pass
    for child in base_dir.iterdir():
        if not child.is_dir():
            continue
        output_dir = child / "output"
        if not _is_completed_output_dir(output_dir):
            continue
        try:
            candidates.append((child.stat().st_mtime, output_dir))
        except OSError:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def clear_cached_results(pdb_id: str | None = None, chain: str | None = None) -> None:
    if not pdb_id:
        target = None
    elif chain is None:
        target = receptor_run_dir(pdb_id, "all").parent
    else:
        target = receptor_run_dir(pdb_id, chain)
    if target is not None:
        _safe_rmtree(target)


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
                "chain": "all",
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


def _prepare_input_pdb(receptor_file: Path, input_file: Path, chain: str) -> None:
    selected_chain = _normalize_chain_id(chain)
    if selected_chain == "all":
        shutil.copy2(receptor_file, input_file)
        return
    text = receptor_file.read_text(encoding="utf-8", errors="ignore")
    filtered = _filter_pdb_text_by_chain(text, selected_chain)
    if not filtered.strip():
        raise RuntimeError(f"No atoms found for chain {selected_chain}.")
    input_file.write_text(filtered, encoding="utf-8")


def _run_predict(job_id: int, pdb_id: str, chain: str, receptor_file: Path, work_dir: Path) -> None:
    output_dir = work_dir / "output"
    input_file = work_dir / f"{pdb_id}.pdb"
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        _prepare_input_pdb(receptor_file, input_file, chain)

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
            message=f"Binding site prediction ready for {pdb_id} ({_normalize_chain_id(chain)}).",
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


def run_p2rank_async(pdb_id: str, receptor_file: Path, chain: str = "all") -> dict[str, Any]:
    selected_chain = _normalize_chain_id(chain)
    current = get_runtime_state()
    if current.get("status") == "running":
        if (
            str(current.get("pdb_id") or "").upper() == str(pdb_id or "").upper()
            and _normalize_chain_id(str(current.get("chain") or "all")) == selected_chain
        ):
            return current
        raise RuntimeError("Another binding site prediction is already running.")

    base_dir = receptor_run_dir(pdb_id, selected_chain)
    base_dir.mkdir(parents=True, exist_ok=True)
    job_id = int(current.get("job_id") or 0) + 1
    work_dir = base_dir / f"run_{int(time.time() * 1000)}"
    _set_state(
        job_id=job_id,
        status="running",
        pdb_id=pdb_id.upper(),
        chain=selected_chain,
        message=f"Running P2Rank for {pdb_id.upper()} ({selected_chain})...",
        error="",
        started_at=time.time(),
        finished_at=None,
        work_dir=str(work_dir),
        output_dir=str(work_dir / "output"),
    )
    thread = threading.Thread(
        target=_run_predict,
        args=(job_id, pdb_id.upper(), selected_chain, receptor_file, work_dir),
        daemon=True,
    )
    thread.start()
    return get_runtime_state()
