from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable


PACKAGE_DIR = Path(__file__).resolve().parent
FINAL_DINAMIK_SCRIPT = PACKAGE_DIR / "final_dinamik.py"
CREATE_VISUALIZATION_SCRIPT = PACKAGE_DIR / "create_visualization.py"
FINAL_FORMATTER_SCRIPT = PACKAGE_DIR / "final_formatter.py"


def _candidate_interpreters(extra: Iterable[str | Path | None]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in extra:
        raw = str(item or "").strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)
        out.append(raw)
    return out


def _find_python_with_modules(
    modules: list[str],
    *,
    env_var: str,
    extra_candidates: Iterable[str | Path | None] = (),
) -> str:
    candidates = _candidate_interpreters(
        [
            os.environ.get(env_var),
            sys.executable,
            shutil.which("python3"),
            "/usr/bin/python3",
            Path.home() / "anaconda3/bin/python3",
            Path.home() / "miniconda3/bin/python3",
            *extra_candidates,
        ]
    )
    probe = "; ".join(f"import {name}" for name in modules)
    for candidate in candidates:
        try:
            completed = subprocess.run(
                [candidate, "-c", probe],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError:
            continue
        if completed.returncode == 0:
            return candidate
    module_list = ", ".join(modules)
    raise RuntimeError(
        f"No usable Python interpreter found for OtoFigure modules: {module_list}. "
        f"Set {env_var} to an interpreter that can import them."
    )


def _copy_case_inputs(
    work_dir: Path,
    *,
    receptor_id: str,
    run_entries: list[tuple[str, Path]],
) -> dict[str, Path]:
    protein_dir = work_dir / "protein"
    ligands_dir = work_dir / "ligands"
    results_dir = work_dir / "results"
    interaction_dir = work_dir / "interaction"
    final_results_dir = work_dir / "final_results"
    formatted_results_dir = work_dir / "formatted_results"

    for directory in (
        protein_dir,
        ligands_dir,
        results_dir,
        interaction_dir,
        final_results_dir,
        formatted_results_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    first_run_dir = run_entries[0][1]
    protein_candidates = sorted(first_run_dir.glob("*_rec_raw.pdb"))
    if not protein_candidates:
        protein_candidates = sorted(first_run_dir.glob("*_complex.pdb"))
    if not protein_candidates:
        raise FileNotFoundError(f"Missing receptor pdb in {first_run_dir}")

    protein_target = protein_dir / f"{str(receptor_id or '').lower()}.pdb"
    shutil.copy2(protein_candidates[0], protein_target)

    for run_name, run_dir in run_entries[:5]:
        pose_candidates = sorted(run_dir.glob("*_pose.pdb"))
        if not pose_candidates:
            raise FileNotFoundError(f"Missing pose pdb for {run_name} in {run_dir}")
        shutil.copy2(pose_candidates[0], ligands_dir / f"{run_name}.pdb")

    return {
        "protein_dir": protein_dir,
        "ligands_dir": ligands_dir,
        "results_dir": results_dir,
        "interaction_dir": interaction_dir,
        "final_results_dir": final_results_dir,
        "formatted_results_dir": formatted_results_dir,
    }


def _render_settings(dpi: int, *, preview_mode: bool) -> tuple[int, int, int, int]:
    safe_dpi = max(10, min(120, int(dpi or 30)))
    if preview_mode:
        return (320, 240, min(safe_dpi, 15), max(60, safe_dpi))
    return (400, 300, min(max(12, safe_dpi), 30), max(72, min(180, safe_dpi * 2)))


def _run_step(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    on_process_start=None,
    on_process_end=None,
) -> str:
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    if callable(on_process_start):
        on_process_start(proc)
    output_chunks: list[str] = []
    try:
        while True:
            chunk = proc.stdout.readline() if proc.stdout is not None else ""
            if chunk:
                output_chunks.append(chunk)
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        remaining = proc.stdout.read() if proc.stdout is not None else ""
        if remaining:
            output_chunks.append(remaining)
    finally:
        if callable(on_process_end):
            on_process_end(proc)
    output_text = "".join(output_chunks)
    if proc.returncode != 0:
        raise RuntimeError(
            f"OtoFigure step failed ({' '.join(cmd)}):\n{output_text.strip()}"
        )
    return output_text


def run(
    *,
    receptor_id: str,
    ligand_name: str,
    run_entries: list[tuple[str, Path]],
    output_png: str | Path,
    work_dir: str | Path,
    dpi: int = 30,
    preview_mode: bool = False,
    on_process_start=None,
    on_process_end=None,
) -> dict[str, Any]:
    if not run_entries:
        raise FileNotFoundError(f"No OtoFigure run entries found for {receptor_id}/{ligand_name}")

    resolved_runs = [(str(run_name), Path(run_dir).resolve()) for run_name, run_dir in run_entries]
    work_root = Path(work_dir).resolve()
    if work_root.exists():
        shutil.rmtree(work_root, ignore_errors=True)
    work_root.mkdir(parents=True, exist_ok=True)

    layout = _copy_case_inputs(work_root, receptor_id=receptor_id, run_entries=resolved_runs)
    width, height, render_dpi, formatter_dpi = _render_settings(dpi, preview_mode=preview_mode)

    pymol_python = _find_python_with_modules(["pymol"], env_var="DOCKUP_OTOFIGURE_PYMOL_PYTHON")
    viz_python = _find_python_with_modules(
        ["cv2", "pandas", "matplotlib"],
        env_var="DOCKUP_OTOFIGURE_VIZ_PYTHON",
    )

    env = os.environ.copy()
    env.setdefault("MPLBACKEND", "Agg")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env["MPLCONFIGDIR"] = str((work_root / ".matplotlib").resolve())

    logs: dict[str, str] = {}
    logs["final_dinamik"] = _run_step(
        [
            pymol_python,
            str(FINAL_DINAMIK_SCRIPT),
            "--pdb_id",
            str(receptor_id or "").lower(),
            "--ligands_dir",
            str(layout["ligands_dir"]),
            "--output_dir",
            str(layout["results_dir"]),
            "--width",
            str(width),
            "--height",
            str(height),
            "--dpi",
            str(render_dpi),
        ],
        cwd=work_root,
        env=env,
        on_process_start=on_process_start,
        on_process_end=on_process_end,
    )
    logs["create_visualization"] = _run_step(
        [
            viz_python,
            str(CREATE_VISUALIZATION_SCRIPT),
            "--input_dir",
            str(layout["results_dir"]),
            "--output_dir",
            str(layout["final_results_dir"]),
            "--interaction_dir",
            str(layout["interaction_dir"]),
        ],
        cwd=work_root,
        env=env,
        on_process_start=on_process_start,
        on_process_end=on_process_end,
    )
    logs["final_formatter"] = _run_step(
        [
            viz_python,
            str(FINAL_FORMATTER_SCRIPT),
            "--input_dir",
            str(layout["final_results_dir"]),
            "--output_dir",
            str(layout["formatted_results_dir"]),
            "--render_dpi",
            str(formatter_dpi),
            "--max_images",
            "1",
        ],
        cwd=work_root,
        env=env,
        on_process_start=on_process_start,
        on_process_end=on_process_end,
    )

    final_images = sorted(layout["final_results_dir"].glob("*_final.png"))
    if not final_images:
        raise FileNotFoundError(f"No final OtoFigure image generated in {layout['final_results_dir']}")

    formatted_images = sorted(layout["formatted_results_dir"].glob("*.png"))
    target_png = Path(output_png).resolve()
    target_png.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(final_images[0], target_png)

    return {
        "final_png": str(target_png),
        "raw_final_png": str(final_images[0]),
        "formatted_png": str(formatted_images[0]) if formatted_images else "",
        "ligand_name": ligand_name,
        "used_runs": [run_name for run_name, _ in resolved_runs[:5]],
        "work_dir": str(work_root),
        "logs": logs,
    }
