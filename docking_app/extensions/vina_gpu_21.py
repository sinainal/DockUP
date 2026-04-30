from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import BASE

EXTENSION_ID = "vina_gpu_21"
DISPLAY_NAME = "Vina-GPU 2.1"
REPO_URL = "https://github.com/DeltaGroupNJUPT/Vina-GPU-2.1.git"
BOOST_VERSION = "1.84.0"
BOOST_DIRNAME = "boost_1_84_0"
BOOST_TARBALL_URL = "https://archives.boost.io/release/1.84.0/source/boost_1_84_0.tar.gz"
OPENCL_HEADERS_TARBALL_URL = "https://github.com/KhronosGroup/OpenCL-Headers/archive/refs/heads/main.tar.gz"

VENV_DIR = BASE / ".venv"
EXTENSIONS_DIR = VENV_DIR / "dockup_extensions"
ROOT_DIR = EXTENSIONS_DIR / EXTENSION_ID
DEPS_DIR = ROOT_DIR / "deps"
OPENCL_HEADERS_DIR = DEPS_DIR / "OpenCL-Headers"
SRC_DIR = ROOT_DIR / "src" / "AutoDock-Vina-GPU-2.1"
BINARY_PATH = SRC_DIR / "AutoDock-Vina-GPU-2-1"
LAUNCHER_PATH = VENV_DIR / "bin" / "vina-gpu-2.1"
STATE_PATH = ROOT_DIR / "state.json"
LOG_PATH = ROOT_DIR / "install.log"

_LOCK = threading.Lock()
_JOB: dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "progress": 0,
    "message": "",
    "error": "",
    "started_at": None,
    "finished_at": None,
}


@dataclass(frozen=True)
class Requirement:
    key: str
    label: str
    ok: bool
    detail: str
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "ok": self.ok,
            "detail": self.detail,
            "required": self.required,
        }


def _human_bytes(num: int | float) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def _which(name: str) -> str:
    return shutil.which(name) or ""


def _run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def _append_log(line: str) -> None:
    ROOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {line.rstrip()}\n")


def _set_job(**updates: Any) -> None:
    with _LOCK:
        _JOB.update(updates)


def _read_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(payload: dict[str, Any]) -> None:
    ROOT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _find_first_existing(paths: list[Path] | tuple[Path, ...]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _env_paths(name: str) -> tuple[Path, ...]:
    raw = os.environ.get(name, "")
    return tuple(Path(item).expanduser() for item in raw.split(os.pathsep) if item.strip())


def _local_source_path() -> Path | None:
    paths = _env_paths("DOCKUP_VINA_GPU_21_LOCAL_SOURCE")
    return paths[0] if paths else None


def _boost_source_candidates() -> tuple[Path, ...]:
    return (
        *_env_paths("DOCKUP_VINA_GPU_21_BOOST_ROOT"),
        ROOT_DIR / "deps" / BOOST_DIRNAME,
        BASE.parent / BOOST_DIRNAME,
        Path("/usr/local/src") / BOOST_DIRNAME,
        Path("/opt") / BOOST_DIRNAME,
    )


def _is_boost_root(path: Path | None) -> bool:
    return bool(
        path
        and path.exists()
        and (path / "boost").exists()
        and (path / "libs" / "thread" / "src" / "pthread" / "thread.cpp").exists()
    )


def _find_library(patterns: tuple[str, ...], roots: tuple[Path, ...]) -> Path | None:
    for root in roots:
        if not root.exists():
            continue
        for pattern in patterns:
            matches = sorted(root.glob(pattern))
            if matches:
                return matches[0]
    return None


def _detect_gpu() -> tuple[bool, str]:
    nvidia_smi = _which("nvidia-smi")
    if not nvidia_smi:
        return False, "GPU command not found (nvidia-smi missing)"
    result = _run([nvidia_smi, "--query-gpu=name,driver_version", "--format=csv,noheader"], timeout=10)
    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode == 0 and names:
        return True, "; ".join(names)
    return False, (result.stdout.strip() or "nvidia-smi returned no GPU")


def _dependency_roots() -> tuple[Path, ...]:
    return (
        *_env_paths("DOCKUP_VINA_GPU_21_DEP_ROOTS"),
        Path("/usr"),
        Path("/usr/local"),
    )


def _include_roots() -> tuple[Path, ...]:
    return tuple(root / "include" for root in _dependency_roots())


def _opencl_include_roots() -> tuple[Path, ...]:
    return (
        *_env_paths("DOCKUP_VINA_GPU_21_OPENCL_HEADERS"),
        *_include_roots(),
        OPENCL_HEADERS_DIR,
    )


def _lib_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    for root in _dependency_roots():
        roots.extend((root / "lib", root / "lib64", root / "lib" / "x86_64-linux-gnu"))
    return tuple(roots)


def _requirement_snapshot() -> tuple[list[Requirement], dict[str, str]]:
    gpu_ok, gpu_detail = _detect_gpu()
    free_bytes = shutil.disk_usage(str(VENV_DIR if VENV_DIR.exists() else BASE)).free
    boost_include = _find_first_existing([root / "boost" for root in _include_roots()])
    boost_root = _find_first_existing([path for path in _boost_source_candidates() if _is_boost_root(path)])
    cl_header = _find_first_existing([root / "CL" / "cl.h" for root in _opencl_include_roots()])
    opencl_lib = _find_library(("libOpenCL.so", "libOpenCL.so.*"), _lib_roots())
    boost_program_options = _find_library(("libboost_program_options.so", "libboost_program_options.so.*", "libboost_program_options.a"), _lib_roots())
    boost_system = _find_library(("libboost_system.so", "libboost_system.so.*", "libboost_system.a"), _lib_roots())
    source = _local_source_path()
    source_ok = source is not None and source.exists()

    reqs = [
        Requirement("venv", "DockUP virtualenv", VENV_DIR.exists(), str(VENV_DIR) if VENV_DIR.exists() else "Missing .venv"),
        Requirement("gpu", "Detected GPU", gpu_ok, gpu_detail, required=False),
        Requirement("opencl_runtime", "OpenCL runtime", opencl_lib is not None, str(opencl_lib) if opencl_lib else "libOpenCL.so not found"),
        Requirement("opencl_headers", "OpenCL headers", True, str(cl_header) if cl_header else "Will download Khronos OpenCL headers into the extension cache"),
        Requirement("compiler", "C/C++ compiler", bool(_which("gcc") and _which("g++")), f"gcc={_which('gcc') or 'missing'}; g++={_which('g++') or 'missing'}"),
        Requirement("make", "make", bool(_which("make")), _which("make") or "make not found"),
        Requirement("boost_source", "Boost source/build root", _is_boost_root(boost_root) or bool(_which("curl") or _which("wget") or True), str(boost_root) if boost_root else f"Will download Boost {BOOST_VERSION} into the extension cache"),
        Requirement("boost_headers", "System Boost headers", boost_include is not None or _is_boost_root(boost_root), str(boost_include or boost_root) if (boost_include or boost_root) else "Will use extension-managed Boost", required=False),
        Requirement("boost_libs", "System Boost libraries", boost_program_options is not None and boost_system is not None or _is_boost_root(boost_root), f"program_options={boost_program_options or 'extension build'}; system={boost_system or 'extension build'}", required=False),
        Requirement("disk", "Free disk", free_bytes >= 2 * 1024**3, f"{_human_bytes(free_bytes)} available"),
        Requirement("source", "Vina-GPU source", source_ok or bool(_which("git")), str(source) if source_ok else "Will clone official repository" if _which("git") else "No local source and git missing"),
    ]
    hints = {
        "boost_include": str(boost_include.parent) if boost_include else "",
        "opencl_include": str(cl_header.parent.parent) if cl_header else "",
        "opencl_lib_dir": str(opencl_lib.parent) if opencl_lib else "",
        "boost_lib_dir": str(boost_program_options.parent) if boost_program_options else "",
        "boost_root": str(boost_root) if boost_root else "",
        "local_source": str(source) if source_ok else "",
    }
    return reqs, hints


def check_requirements() -> dict[str, Any]:
    reqs, _hints = _requirement_snapshot()
    blocking = [req for req in reqs if req.required and not req.ok]
    return {
        "ok": not blocking,
        "requirements": [req.to_dict() for req in reqs],
        "blocking": [req.key for req in blocking],
    }


def _safe_link(path: str, label: str) -> str:
    if not path or " " not in path:
        return path
    link_root = Path(tempfile.gettempdir()) / "dockup_extension_links"
    link_root.mkdir(parents=True, exist_ok=True)
    link = link_root / label
    target = Path(path)
    if link.exists() or link.is_symlink():
        if link.resolve() == target.resolve():
            return str(link)
        link.unlink()
    link.symlink_to(target, target_is_directory=target.is_dir())
    return str(link)


def _remove_safe_path(path: Path) -> None:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)
    except FileNotFoundError:
        pass


def _copy_source_to_build(build_parent: Path, hints: dict[str, str]) -> Path:
    build_src = build_parent / "AutoDock-Vina-GPU-2.1"
    local_source = hints.get("local_source")
    if local_source:
        shutil.copytree(local_source, build_src)
        _append_log(f"Using local source: {local_source}")
        return build_src
    clone_root = build_parent / "repo"
    _append_log(f"Cloning official source: {REPO_URL}")
    result = _run(["git", "clone", "--depth", "1", REPO_URL, str(clone_root)], timeout=300)
    if result.returncode != 0:
        raise RuntimeError(result.stdout.strip() or "git clone failed")
    src = clone_root / "AutoDock-Vina-GPU-2.1"
    if not src.exists():
        raise RuntimeError("Official repository layout changed: AutoDock-Vina-GPU-2.1 folder missing")
    shutil.copytree(src, build_src)
    return build_src


def _ensure_boost_root() -> Path:
    existing = _find_first_existing([path for path in _boost_source_candidates() if _is_boost_root(path)])
    if existing:
        _append_log(f"Using Boost root: {existing}")
        return existing

    deps = ROOT_DIR / "deps"
    deps.mkdir(parents=True, exist_ok=True)
    tarball = deps / f"{BOOST_DIRNAME}.tar.gz"
    boost_root = deps / BOOST_DIRNAME
    if not tarball.exists():
        _append_log(f"Downloading Boost {BOOST_VERSION} ({BOOST_TARBALL_URL})")
        urllib.request.urlretrieve(BOOST_TARBALL_URL, tarball)
    if not boost_root.exists():
        _append_log("Extracting Boost source")
        result = _run(["tar", "-xzf", str(tarball), "-C", str(deps)], timeout=300)
        if result.returncode != 0:
            raise RuntimeError(result.stdout.strip() or "Boost extraction failed")
    if not (boost_root / "stage" / "lib").exists() or not list((boost_root / "stage" / "lib").glob("libboost_program_options*")):
        _append_log("Building required Boost libraries (program_options/system/filesystem/thread/chrono)")
        bootstrap = _run(["./bootstrap.sh"], cwd=boost_root, timeout=300)
        if bootstrap.stdout.strip():
            _append_log(bootstrap.stdout.strip()[-3000:])
        if bootstrap.returncode != 0:
            raise RuntimeError("Boost bootstrap failed")
        jobs = str(max(1, (os.cpu_count() or 2) - 1))
        build = _run(
            [
                "./b2",
                "--with-program_options",
                "--with-system",
                "--with-filesystem",
                "--with-thread",
                "--with-chrono",
                "link=static",
                "variant=release",
                "threading=multi",
                "stage",
                f"-j{jobs}",
            ],
            cwd=boost_root,
            timeout=1800,
        )
        if build.stdout.strip():
            _append_log(build.stdout.strip()[-5000:])
        if build.returncode != 0:
            raise RuntimeError("Boost build failed")
    return boost_root


def _ensure_opencl_headers() -> Path:
    existing = _find_first_existing([root / "CL" / "cl.h" for root in _opencl_include_roots()])
    if existing:
        include_root = existing.parent.parent
        _append_log(f"Using OpenCL headers: {include_root}")
        return include_root

    DEPS_DIR.mkdir(parents=True, exist_ok=True)
    tarball = DEPS_DIR / "opencl_headers.tar.gz"
    if not tarball.exists():
        _append_log(f"Downloading OpenCL headers ({OPENCL_HEADERS_TARBALL_URL})")
        urllib.request.urlretrieve(OPENCL_HEADERS_TARBALL_URL, tarball)

    with tempfile.TemporaryDirectory(prefix="dockup_opencl_headers_") as tmp_name:
        tmp = Path(tmp_name)
        with tarfile.open(tarball, "r:gz") as archive:
            archive.extractall(tmp, filter="data")
        source = next((path.parent.parent for path in tmp.rglob("CL/cl.h")), None)
        if source is None:
            raise RuntimeError("OpenCL headers archive did not contain CL/cl.h")
        if OPENCL_HEADERS_DIR.exists():
            shutil.rmtree(OPENCL_HEADERS_DIR)
        shutil.copytree(source, OPENCL_HEADERS_DIR)

    _append_log(f"Installed OpenCL headers: {OPENCL_HEADERS_DIR}")
    return OPENCL_HEADERS_DIR


def _prepare_opencl_root(tmp: Path, hints: dict[str, str]) -> Path:
    include_root = Path(hints["opencl_include"]) if hints.get("opencl_include") else _ensure_opencl_headers()
    lib_dir = Path(hints["opencl_lib_dir"])
    opencl_root = tmp / "opencl_root"
    (opencl_root / "include").mkdir(parents=True, exist_ok=True)
    (opencl_root / "lib64").mkdir(parents=True, exist_ok=True)
    cl_target = opencl_root / "include" / "CL"
    if not cl_target.exists():
        cl_target.symlink_to(include_root / "CL", target_is_directory=True)
    lib = _find_library(("libOpenCL.so", "libOpenCL.so.*"), (lib_dir,))
    if not lib:
        raise RuntimeError("OpenCL library disappeared during install")
    lib_target = opencl_root / "lib64" / "libOpenCL.so"
    if not lib_target.exists():
        lib_target.symlink_to(lib)
    return opencl_root


def _build_and_install() -> None:
    _set_job(running=True, phase="installing", progress=3, message="Checking requirements", error="", started_at=time.time(), finished_at=None)
    LOG_PATH.unlink(missing_ok=True)
    _append_log("Install requested")
    reqs, hints = _requirement_snapshot()
    missing = [req for req in reqs if req.required and not req.ok]
    if missing:
        for req in missing:
            _append_log(f"Missing requirement: {req.label} - {req.detail}")
        raise RuntimeError("Missing required dependencies: " + ", ".join(req.label for req in missing))

    if is_installed():
        _append_log("Already installed; skipping rebuild")
        _write_state({"installed": True, "tested": bool(_read_state().get("tested")), "updated_at": time.time()})
        _set_job(progress=100, message="Already installed")
        return

    ROOT_DIR.mkdir(parents=True, exist_ok=True)
    _set_job(progress=12, message="Preparing source")
    with tempfile.TemporaryDirectory(prefix="dockup_vina_gpu_build_") as tmp_name:
        tmp = Path(tmp_name)
        build_src = _copy_source_to_build(tmp, hints)
        source_size = sum(p.stat().st_size for p in build_src.rglob("*") if p.is_file())
        _append_log(f"Source prepared ({_human_bytes(source_size)})")
        _set_job(progress=35, message=f"Building Vina-GPU 2.1 ({_human_bytes(source_size)})")

        env = os.environ.copy()
        _set_job(progress=42, message="Preparing Boost build dependency")
        boost_root = _ensure_boost_root()
        opencl_root = _prepare_opencl_root(tmp, hints)
        boost_root_safe = _safe_link(str(boost_root), "boost_root")
        opencl_root_safe = _safe_link(str(opencl_root), "opencl_root")
        env.update(
            {
                "WORK_DIR": str(build_src),
                "BOOST_LIB_PATH": boost_root_safe,
                "OPENCL_LIB_PATH": opencl_root_safe,
                "OPENCL_VERSION": "-DOPENCL_3_0",
                "GPU_PLATFORM": "-DNVIDIA_PLATFORM",
                "LD_LIBRARY_PATH": f"{opencl_root_safe}/lib64:{boost_root_safe}/stage/lib:{env.get('LD_LIBRARY_PATH', '')}",
            }
        )
        make_vars = [
            f"WORK_DIR={build_src}",
            f"BOOST_LIB_PATH={boost_root_safe}",
            f"OPENCL_LIB_PATH={opencl_root_safe}",
            "OPENCL_VERSION=-DOPENCL_3_0",
            "GPU_PLATFORM=-DNVIDIA_PLATFORM",
        ]
        clean = _run(["make", "clean", *make_vars], cwd=build_src, env=env, timeout=120)
        if clean.stdout.strip():
            _append_log(clean.stdout.strip()[-2000:])
        result = _run(["make", "source", *make_vars], cwd=build_src, env=env, timeout=900)
        if result.stdout.strip():
            _append_log(result.stdout.strip()[-5000:])
        if result.returncode != 0 or not (build_src / "AutoDock-Vina-GPU-2-1").exists():
            raise RuntimeError("Vina-GPU build failed. See install log.")

        _set_job(progress=78, message="Installing into DockUP virtualenv")
        if SRC_DIR.exists():
            shutil.rmtree(SRC_DIR)
        SRC_DIR.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(build_src, SRC_DIR)
        shutil.rmtree(SRC_DIR / "test_out", ignore_errors=True)

    hints["boost_lib_dir"] = str(Path(_ensure_boost_root()) / "stage" / "lib")
    _write_launcher(hints)
    _write_state({"installed": True, "tested": False, "updated_at": time.time(), "binary": str(BINARY_PATH), "launcher": str(LAUNCHER_PATH)})
    _append_log(f"Installed launcher: {LAUNCHER_PATH}")
    _set_job(progress=100, message="Install complete")


def _write_launcher(hints: dict[str, str] | None = None) -> None:
    hints = hints or _requirement_snapshot()[1]
    LAUNCHER_PATH.parent.mkdir(parents=True, exist_ok=True)
    lib_paths = [hints.get("boost_lib_dir", ""), hints.get("opencl_lib_dir", "")]
    lib_expr = ":".join(path for path in lib_paths if path)
    script = f"""#!/usr/bin/env bash
set -euo pipefail
METHOD_DIR={str(SRC_DIR)!r}
export VINA_GPU_21_METHOD_DIR="$METHOD_DIR"
export VINA_GPU_21_OPENCL_BINARY_PATH="$METHOD_DIR"
export LD_LIBRARY_PATH="{lib_expr}:$METHOD_DIR:${{LD_LIBRARY_PATH:-}}"
cd "$METHOD_DIR"
exec "$METHOD_DIR/AutoDock-Vina-GPU-2-1" "$@"
"""
    LAUNCHER_PATH.write_text(script, encoding="utf-8")
    LAUNCHER_PATH.chmod(0o755)


def start_install() -> dict[str, Any]:
    with _LOCK:
        if _JOB.get("running"):
            return status()
    thread = threading.Thread(target=_job_wrapper, args=(_build_and_install, "install"), daemon=True)
    thread.start()
    return status()


def _uninstall() -> None:
    _set_job(running=True, phase="uninstalling", progress=5, message="Removing Vina-GPU 2.1", error="", started_at=time.time(), finished_at=None)
    _append_log("Uninstall requested")
    for label in ("boost_root", "opencl_root"):
        link = Path(tempfile.gettempdir()) / "dockup_extension_links" / label
        if link.is_symlink():
            _remove_safe_path(link)
            _append_log(f"Removed temporary link: {link}")
    _set_job(progress=35, message="Removing launcher")
    _remove_safe_path(LAUNCHER_PATH)
    _append_log(f"Removed launcher: {LAUNCHER_PATH}")
    _set_job(progress=70, message="Removing extension files")
    _remove_safe_path(ROOT_DIR)
    _set_job(progress=100, message="Uninstall complete")


def start_uninstall() -> dict[str, Any]:
    with _LOCK:
        if _JOB.get("running"):
            return status()
    thread = threading.Thread(target=_job_wrapper, args=(_uninstall, "uninstall"), daemon=True)
    thread.start()
    return status()


def _job_wrapper(func: Any, phase: str) -> None:
    try:
        func()
        _set_job(running=False, phase=phase, error="", finished_at=time.time())
    except Exception as exc:
        _append_log(f"ERROR: {exc}")
        _set_job(running=False, phase=f"{phase}_failed", error=str(exc), message=str(exc), finished_at=time.time())


def is_installed() -> bool:
    return BINARY_PATH.exists() and os.access(BINARY_PATH, os.X_OK) and LAUNCHER_PATH.exists() and os.access(LAUNCHER_PATH, os.X_OK)


def _run_smoke_test() -> dict[str, Any]:
    if not is_installed():
        raise RuntimeError("Vina-GPU 2.1 is not installed")
    _set_job(running=True, phase="testing", progress=5, message="Running smoke test", error="", started_at=time.time(), finished_at=None)
    _append_log("Smoke test requested")
    with tempfile.TemporaryDirectory(prefix="dockup_vina_gpu_test_") as tmp_name:
        tmp = Path(tmp_name)
        out_file = tmp / "out.pdbqt"
        log_file = tmp / "test.log"
        receptor = SRC_DIR / "input_file_example" / "2bm2_protein.pdbqt"
        ligand = SRC_DIR / "test" / "drugbank1.pdbqt"
        config_values = _read_example_config(SRC_DIR / "input_file_example" / "2bm2_config.txt")
        cmd = [
            str(LAUNCHER_PATH),
            "--receptor", str(receptor),
            "--ligand", str(ligand),
            "--opencl_binary_path", str(SRC_DIR),
            "--center_x", config_values["center_x"],
            "--center_y", config_values["center_y"],
            "--center_z", config_values["center_z"],
            "--size_x", config_values["size_x"],
            "--size_y", config_values["size_y"],
            "--size_z", config_values["size_z"],
            "--thread", config_values.get("thread", "8000"),
            "--num_modes", "9",
            "--out", str(out_file),
            "--log", str(log_file),
        ]
        started = time.time()
        result = _run(cmd, timeout=180)
        elapsed = time.time() - started
        _append_log(result.stdout.strip()[-3000:] if result.stdout.strip() else "Smoke test produced no stdout")
        if result.returncode != 0 or not out_file.exists():
            raise RuntimeError("Smoke test failed. See install log.")
        affinity = ""
        for line in out_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("REMARK VINA RESULT:"):
                affinity = line.replace("REMARK VINA RESULT:", "").strip()
                break
    state = _read_state()
    state.update({"installed": True, "tested": True, "last_tested_at": time.time(), "last_test_elapsed_sec": round(elapsed, 3), "last_test_affinity": affinity})
    _write_state(state)
    _set_job(progress=100, message="Smoke test passed")
    return {"elapsed_sec": round(elapsed, 3), "affinity": affinity}


def _read_example_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {
        "center_x": "40.415",
        "center_y": "110.986",
        "center_z": "82.673",
        "size_x": "30",
        "size_y": "30",
        "size_z": "30",
        "thread": "8000",
    }
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            key = key.strip()
            if key in values:
                values[key] = raw_value.strip()
    except OSError:
        pass
    return values


def start_test() -> dict[str, Any]:
    with _LOCK:
        if _JOB.get("running"):
            return status()
    thread = threading.Thread(target=_job_wrapper, args=(_run_smoke_test, "test"), daemon=True)
    thread.start()
    return status()


def status() -> dict[str, Any]:
    req = check_requirements()
    state = _read_state()
    try:
        logs = LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()[-80:]
    except OSError:
        logs = []
    with _LOCK:
        job = dict(_JOB)
    installed = is_installed()
    return {
        "id": EXTENSION_ID,
        "name": DISPLAY_NAME,
        "installed": installed,
        "tested": bool(state.get("tested")) and installed,
        "launcher": str(LAUNCHER_PATH),
        "binary": str(BINARY_PATH),
        "method_dir": str(SRC_DIR),
        "requirements_ok": req["ok"],
        "requirements": req["requirements"],
        "blocking": req["blocking"],
        "job": job,
        "log": logs,
        "last_test_elapsed_sec": state.get("last_test_elapsed_sec"),
        "last_test_affinity": state.get("last_test_affinity"),
        "expected_download_size": "source/build cache varies; reserve at least 2 GB",
    }
