"""Immutable local snapshots plus a durable, single-worker compute queue."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..config import BASE, DATA_DIR

ROOT = Path(os.environ.get("DOCKUP_MODELING_DATA_DIR", str(DATA_DIR / "modeling"))).resolve()
TERMINAL = {"succeeded", "partially_succeeded", "failed", "cancelled", "timed_out", "interrupted"}
EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dockup-modeling")
_processes: dict[str, subprocess.Popen] = {}
_lock = threading.Lock()
_shutting_down = threading.Event()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def connect():
    ROOT.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ROOT / "jobs.sqlite", timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, kind TEXT, state TEXT, created REAL, updated REAL, owner INTEGER, payload TEXT, result TEXT)")
    return con


def update(job_id, **values):
    allowed = {"state", "owner", "result"}
    if not set(values).issubset(allowed):
        raise ValueError("Invalid update")
    values["updated"] = time.time()
    if "result" in values:
        values["result"] = json.dumps(values["result"], allow_nan=False)
    with connect() as con:
        con.execute("UPDATE jobs SET " + ",".join(k + "=?" for k in values) + " WHERE id=?", [*values.values(), job_id])


def job_dir(job_id):
    if not re.fullmatch(r"job_[a-f0-9]{24}", job_id):
        raise ValueError("Invalid job ID")
    path = ROOT / "jobs" / job_id
    if not path.is_dir():
        raise FileNotFoundError(job_id)
    return path


def get_job(job_id):
    job_dir(job_id)
    with connect() as con:
        row = con.execute("SELECT * FROM jobs WHERE id=?", [job_id]).fetchone()
    if row is None:
        raise FileNotFoundError(job_id)
    data = dict(row)
    data["payload"] = json.loads(data["payload"])
    data["result"] = json.loads(data["result"] or "{}")
    directory = job_dir(job_id)
    log = directory / "worker.log"
    if log.exists():
        with log.open("rb") as stream:
            stream.seek(max(0, log.stat().st_size - 12000))
            data["log"] = stream.read().decode(errors="replace")
    events = directory / "events.jsonl"
    if events.exists():
        data["events"] = []
        for line in events.read_text().splitlines()[-100:]:
            try:
                if line.strip():
                    data["events"].append(json.loads(line))
            except json.JSONDecodeError:
                # A live worker can be between writes; its partial final line
                # must not turn an otherwise valid polling request into a 500.
                continue
    return data


def list_jobs(limit=100):
    with connect() as con:
        ids = [r[0] for r in con.execute("SELECT id FROM jobs ORDER BY created DESC LIMIT ?", [limit])]
    return [get_job(i) for i in ids]


def model_dir(model_id):
    if not re.fullmatch(r"mdl_[A-Za-z0-9_]+", model_id):
        raise ValueError("Invalid model ID")
    directory = ROOT / "library" / "runs" / model_id
    if directory.resolve().parent != (ROOT / "library" / "runs").resolve():
        raise ValueError("Unsafe model directory")
    if not (directory / "manifest.json").is_file():
        raise FileNotFoundError(model_id)
    return directory


def list_models():
    paths = sorted((ROOT / "library" / "runs").glob("mdl_*/manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [json.loads(p.read_text()) for p in paths[:500]]


def list_pose_sets():
    paths = sorted((ROOT / "library" / "runs" / "pose_sets").glob("pset_*.json"), reverse=True)
    return [json.loads(p.read_text()) for p in paths[:100]]


def pose_set(pose_id):
    if not re.fullmatch(r"pset_[A-Za-z0-9_]+", pose_id):
        raise ValueError("Invalid pose set ID")
    return json.loads((ROOT / "library" / "runs" / "pose_sets" / f"{pose_id}.json").read_text())


def submit(kind, payload, *, inline=False):
    if kind not in {"build", "poses", "quantum", "experiment", "homology"}:
        raise ValueError("Unknown modeling job")
    job_id = "job_" + uuid.uuid4().hex[:24]
    directory = ROOT / "jobs" / job_id
    directory.mkdir(parents=True)
    work = directory / "studio" / "runs"
    input_ids = set(payload.get("model_ids", []) + payload.get("surface_model_ids", []))
    for key in ("surface_model_id", "adsorbate_model_id"):
        if payload.get(key):
            input_ids.add(payload[key])
    for pid in payload.get("pose_set_ids", []):
        ps = pose_set(pid)
        write_json(work / "pose_sets" / f"{pid}.json", ps)
        input_ids.update(r["model_id"] for r in ps["poses"])
    input_hashes = {}
    # A pose refinement also needs the mapped surface/adsorbate manifests.
    # Copy these dependencies, not their mutable historical quantum outputs.
    pending = list(input_ids)
    while pending:
        mid = pending.pop()
        meta = json.loads((model_dir(mid) / "manifest.json").read_text())
        source = meta.get("source", {})
        for key in ("surface_model_id","adsorbate_model_id"):
            dependency = source.get(key)
            if dependency and dependency not in input_ids:
                input_ids.add(dependency)
                pending.append(dependency)
    for mid in input_ids:
        source = model_dir(mid)
        # Preserve input topology and coordinates, not mutable historical outputs.
        for name in ("manifest.json", "structure.sdf", "structure.pdb", "structure.xyz"):
            src = source / name
            if src.exists():
                dst = work / mid / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                input_hashes[f"{mid}/{name}"] = sha(dst)
        src = json.loads((source / "manifest.json").read_text()).get("source", {})
        if src.get("pose_set_id"):
            psid = src["pose_set_id"]
            write_json(work / "pose_sets" / f"{psid}.json", pose_set(psid))
    if kind == "homology":
        template = template_dir(payload["template_id"])
        shutil.copy2(template / "template.pdb", directory / "template.pdb")
        input_hashes["template.pdb"] = sha(directory / "template.pdb")
    code_root = Path(__file__).parent
    config = {"kind": kind, "payload": payload, "input_hashes": input_hashes,
              "schema_version": 1, "worker_sha256": sha(code_root / "worker.py"),
              "adapter_code_sha256": {p.name: sha(p) for p in sorted(code_root.glob("*.py"))}}
    write_json(directory / "request.json", config)
    stamp = time.time()
    with connect() as con:
        con.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", [job_id, kind, "queued", stamp, stamp, None, json.dumps(payload), "{}"])
    if inline:
        execute(job_id)
    else:
        EXECUTOR.submit(execute, job_id)
    return get_job(job_id)


def _stop(proc):
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=3)
        except ProcessLookupError:
            pass


def execute(job_id):
    if _shutting_down.is_set():
        return
    with connect() as con:
        changed = con.execute("UPDATE jobs SET state='running',owner=?,updated=? WHERE id=? AND state='queued'", [os.getpid(), time.time(), job_id]).rowcount
    if not changed:
        return
    directory = job_dir(job_id)
    row = get_job(job_id)
    payload = row["payload"]
    timeout = payload.get("timeout_seconds", 300)
    n = len(payload.get("model_ids", [])) or 1
    if payload.get("calculation")=="interaction":
        n *= 3
    if row["kind"] == "experiment":
        n = max(1, len(payload.get("polymers", [])) * len(payload.get("repeats", []))) * (payload.get("representatives", 2) + 2)
    provider_setup = 60 if payload.get("engine")=="crest" else 0
    deadline = time.monotonic() + min(86400, (timeout + provider_setup) * n + 90)
    env = os.environ.copy()
    env["MODELING_DATA_DIR"] = str(directory / "studio")
    env["PYTHONUNBUFFERED"] = "1"
    env["OMP_NUM_THREADS"] = str(payload.get("threads", 2))
    env["OPENBLAS_NUM_THREADS"] = "1"
    env["MKL_NUM_THREADS"] = str(payload.get("threads", 2))
    command = [os.environ.get("DOCKUP_MODELING_PYTHON", sys.executable), "-m", "docking_app.modeling.worker", str(directory)]
    try:
        with (directory / "worker.log").open("w") as log:
            proc = subprocess.Popen(command, cwd=BASE, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            with _lock:
                _processes[job_id] = proc
            while proc.poll() is None:
                if get_job(job_id)["state"] in {"cancelled","interrupted"}:
                    _stop(proc)
                    return
                if time.monotonic() > deadline:
                    _stop(proc)
                    update(job_id, state="timed_out", result={"reason": "Whole-job deadline exceeded"})
                    return
                time.sleep(.2)
        if get_job(job_id)["state"] in {"cancelled","interrupted"}:
            return
        result_path = directory / "result.json"
        result = json.loads(result_path.read_text()) if result_path.exists() else {"reason": "Worker exited without a result; inspect log"}
        state = result.get("state", "failed") if proc.returncode == 0 else "failed"
        if state not in {"succeeded", "partially_succeeded", "failed"}:
            state = "failed"
        # Publish new library models only. Never overwrite an earlier input model.
        for source in (directory / "studio" / "runs").glob("mdl_*"):
            dest = ROOT / "library" / "runs" / source.name
            if not dest.exists() and (source / "manifest.json").exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, dest)
        for source in (directory / "studio" / "runs" / "pose_sets").glob("pset_*.json"):
            dest = ROOT / "library" / "runs" / "pose_sets" / source.name
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)
        update(job_id, state=state, result=result)
    except Exception as exc:
        if get_job(job_id)["state"] not in {"cancelled","interrupted"}:
            update(job_id, state="failed", result={"reason": str(exc)})
    finally:
        # Children such as MPI helpers share this dedicated group. Clean up even
        # if the worker itself has exited after a provider-level timeout.
        if "proc" in locals():
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        with _lock:
            _processes.pop(job_id, None)


def cancel(job_id):
    row = get_job(job_id)
    if row["state"] not in TERMINAL:
        update(job_id, state="cancelled")
    return get_job(job_id)


def retry(job_id):
    row = get_job(job_id)
    if row["state"] not in TERMINAL:
        raise ValueError("Cancel or finish the original job first")
    # Retry creates a new lineage, it is not a claim of engine checkpoint resume.
    child = submit(row["kind"], row["payload"])
    write_json(job_dir(child["id"]) / "retry_of.json", {"parent_job_id": job_id})
    return child


def recover():
    _shutting_down.clear()
    for row in list_jobs():
        if row["state"] == "running":
            try:
                os.kill(row["owner"] or -1, 0) if row["owner"] else (_ for _ in ()).throw(ProcessLookupError())
            except ProcessLookupError:
                update(row["id"], state="interrupted", result={"reason": "Owner process stopped; retry creates a new job"})
            except PermissionError:
                pass
        elif row["state"] == "queued":
            EXECUTOR.submit(execute, row["id"])


def shutdown():
    """Stop this server's compute groups; retain queued work for restart."""
    _shutting_down.set()
    with _lock:
        owned = list(_processes.items())
    for job_id, proc in owned:
        if get_job(job_id)["state"] not in TERMINAL:
            update(job_id,state="interrupted",result={"reason":"Server stopped; retry creates a new job"})
        _stop(proc)


def template_dir(template_id):
    if not re.fullmatch(r"tpl_[a-f0-9]{24}", template_id):
        raise ValueError("Invalid template ID")
    return ROOT / "templates" / template_id


def artifact_file(job_id, index):
    row = get_job(job_id)
    outputs = row["result"].get("outputs", [])
    if index < 0 or index >= len(outputs):
        raise FileNotFoundError("Artifact not found")
    output = outputs[index]
    root = job_dir(job_id).resolve()
    path = (root / output["file"]).resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError("Unsafe or missing artifact")
    if sha(path) != output["sha256"]:
        raise ValueError("Artifact checksum changed")
    return path, output
