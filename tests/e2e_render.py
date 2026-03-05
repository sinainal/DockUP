#!/usr/bin/env python3
"""E2E render flow test for report endpoints.

Flow:
1) Pick a report source under data/dock with at least one render-ready receptor.
2) Trigger /api/reports/render in preview mode.
3) Poll /api/reports/status until idle.
4) Validate image list and image serving endpoint.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE_URL = "http://localhost:8000"
POLL_INTERVAL_SECONDS = 1
RENDER_TIMEOUT_SECONDS = 120

_CFG = {"base_url": BASE_URL}


def ts() -> str:
    return time.strftime("%H:%M:%S")


def step(msg: str) -> None:
    print(f"[{ts()}] {msg}")


def api(method: str, path: str, body: dict[str, Any] | None = None, timeout: int = 60) -> dict[str, Any]:
    url = f"{_CFG['base_url'].rstrip('/')}{path}"
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return json.loads(text) if text.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method} {path} -> network error: {exc}") from exc


def get_json(path: str, timeout: int = 60) -> dict[str, Any]:
    return api("GET", path, None, timeout=timeout)


def wait_report_idle(timeout_sec: int) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    last = ""
    while time.time() < deadline:
        status = get_json("/api/reports/status", timeout=30)
        cur = str(status.get("status") or "")
        if cur != last:
            step(
                f"report status={cur} task={status.get('task')} "
                f"{status.get('progress')}/{status.get('total')} {status.get('message')}"
            )
            last = cur
        if cur == "idle":
            return status
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"Report status did not return to idle in {timeout_sec}s")


def pick_render_source() -> tuple[str, str, str]:
    root_payload = get_json("/api/reports/list?root_path=data/dock", timeout=60)
    folders = list(root_payload.get("source_folders") or [])
    if not folders:
        raise RuntimeError("No report source folders under data/dock.")

    # Keep runtime low: prefer folders with fewer receptors (>0).
    candidates = [f for f in folders if int(f.get("receptor_count") or 0) > 0]
    candidates.sort(key=lambda row: int(row.get("receptor_count") or 0))
    if not candidates:
        raise RuntimeError("No source folder with receptor_count > 0.")

    for row in candidates:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        source_path = f"data/dock/{name}"
        output_path = f"{source_path}/report_outputs"
        query = urllib.parse.urlencode(
            {"root_path": "data/dock", "source_path": source_path, "output_path": output_path}
        )
        detail = get_json(f"/api/reports/list?{query}", timeout=60)
        receptors = list(detail.get("receptors") or [])
        ready = [str(r.get("id") or "") for r in receptors if r.get("ready")]
        if ready:
            return source_path, output_path, ready[0]

    raise RuntimeError("No render-ready receptor found in available report sources.")


def run() -> None:
    t0 = time.time()
    step("health check /api/state")
    state = get_json("/api/state", timeout=20)
    if "mode" not in state:
        raise RuntimeError(f"Unexpected /api/state response: {state}")

    step("waiting report task idle")
    wait_report_idle(timeout_sec=30)

    source_path, output_path, receptor_id = pick_render_source()
    step(f"selected source={source_path} receptor={receptor_id}")

    payload = {
        "root_path": "data/dock",
        "source_path": source_path,
        "output_path": output_path,
        "linked_path": "",
        "dpi": 72,
        "receptors": [receptor_id],
        "run_by_receptor": {receptor_id: "run1"},
        "is_preview": True,
    }

    step("triggering /api/reports/render")
    start = api("POST", "/api/reports/render", payload, timeout=30)
    if str(start.get("status") or "") != "started":
        raise RuntimeError(f"Render did not start: {start}")

    step("polling /api/reports/status")
    final_status = wait_report_idle(timeout_sec=RENDER_TIMEOUT_SECONDS)
    errors = list(final_status.get("errors") or [])
    if errors:
        raise RuntimeError(f"Render completed with errors: {errors}")

    query = urllib.parse.urlencode(
        {
            "root_path": "data/dock",
            "source_path": source_path,
            "output_path": output_path,
            "images_root_path": output_path,
        }
    )
    images_payload = get_json(f"/api/reports/images?{query}", timeout=30)
    images = list(images_payload.get("images") or [])
    if not images:
        raise RuntimeError(f"Render finished but no images found under {output_path}")

    first_path = str(images[0].get("path") or "").strip()
    if not first_path:
        raise RuntimeError("First image entry has empty path.")
    serve_url = f"{_CFG['base_url'].rstrip('/')}/api/reports/image/{first_path}"
    req = urllib.request.Request(serve_url, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        content_type = str(resp.headers.get("content-type") or "")
        if "image" not in content_type.lower():
            raise RuntimeError(f"Unexpected image content-type: {content_type}")
        _ = resp.read(64)

    elapsed = time.time() - t0
    step(f"render images={len(images)} first={first_path}")
    print()
    print(f"E2E render PASSED in {elapsed:.1f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description="DockUP E2E render test")
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()
    _CFG["base_url"] = str(args.base_url).rstrip("/")
    print("=" * 58)
    print("DockUP E2E Render")
    print("=" * 58)
    print(f"Base URL: {_CFG['base_url']}")
    try:
        run()
    except Exception as exc:
        print()
        print(f"E2E render FAILED: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()

