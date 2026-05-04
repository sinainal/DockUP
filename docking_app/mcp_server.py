from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import BASE
from .live.client import DEFAULT_BASE_URL, DockUPClient


SERVER_INFO = {"name": "dockup-control", "version": "0.1.0"}
TRACE_PATH = os.getenv("DOCKUP_MCP_TRACE", "").strip()


def _trace(event: str, payload: Any) -> None:
    if not TRACE_PATH:
        return
    try:
        with open(TRACE_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": round(time.time(), 3), "event": event, "payload": payload}, ensure_ascii=False) + "\n")
    except Exception:
        return

TOOLS: list[dict[str, Any]] = [
    {
        "name": "dockup_state",
        "description": "Read compact DockUP state. Use view=full only when the complete queue/state JSON is needed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "view": {"type": "string", "enum": ["summary", "queue", "receptors", "ligands", "grid", "run", "full"]},
                "limit": {"type": "integer", "minimum": 0},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "dockup_assets",
        "description": "Inspect receptor and ligand inventory, with compact mismatch warnings for stale receptor metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "view": {"type": "string", "enum": ["summary", "receptors", "ligands", "mismatches", "full"]},
                "include_files": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "dockup_mutate",
        "description": "Small DockUP maintenance/config mutations. Does not start docking runs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "receptor.load",
                        "receptor.remove",
                        "receptor.delete_file",
                        "receptor.clear",
                        "receptor.reload",
                        "ligand.generate",
                        "ligand.active.set",
                        "grid.set_many",
                        "config.set",
                        "state.repair",
                    ],
                },
                "payload": {"type": "object", "additionalProperties": True},
                "response": {"type": "string", "enum": ["summary", "full"]},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "dockup_queue",
        "description": "Prepare, build, list, clear, or remove DockUP queue rows without starting runs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["prepare", "build", "list", "clear", "remove_batch"],
                },
                "payload": {"type": "object", "additionalProperties": True},
                "response": {"type": "string", "enum": ["summary", "full"]},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "dockup_validate",
        "description": "Validate current DockUP state/assets/queue against caller-provided expectations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["state", "assets", "queue", "all"]},
                "checks": {"type": "object", "additionalProperties": True},
                "response": {"type": "string", "enum": ["summary", "full"]},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "dockup_backend",
        "description": "Inspect or start the local DockUP web backend using the repo venv. Does not start docking runs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "start"]},
                "wait_seconds": {"type": "number", "minimum": 0},
                "response": {"type": "string", "enum": ["summary", "full"]},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "dockup_report",
        "description": "Manage DockUP result/report views, plots, renders, DPI, images, metadata, and report compilation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "results.folders",
                        "results.scan",
                        "results.detail",
                        "report.list",
                        "report.images",
                        "report.preview",
                        "report.metadata.get",
                        "report.metadata.set",
                        "report.doc_config.get",
                        "report.doc_config.set",
                        "report.source.delete",
                        "report.images.delete",
                        "report.images.delete_all",
                        "report.graphs",
                        "report.render",
                        "report.render.stop",
                        "report.compile",
                        "report.status",
                    ],
                },
                "payload": {"type": "object", "additionalProperties": True},
                "response": {"type": "string", "enum": ["summary", "full"]},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
]

GUIDE_URI = "dockup://guide/control"
GUIDE_TEXT = """DockUP MCP guide:
- Use MCP/Control API tools only; do not mutate STATE or files directly.
- For queue prep: read state/assets, ensure receptors/ligands/grid/config, prepare queue, validate.
- Docking uses dock-ready SDF ligands; Redocking uses receptor-native ligands.
- In Docking, all_set expands to active SDF ligands.
- If localhost backend is down, use dockup_backend start/status; it starts only the web app via the repo venv.
- For reports/results, use dockup_report. It can scan result folders, list images, generate plots, render views, set DPI/render options, and compile docs.
- After mutations, validate queue_count, total_runs, run_status, grid sizes, active ligands, and pdb_file/pdb_id matches.
- Do not start runs unless the user explicitly asks.
"""


class DockUPMCPServer:
    def __init__(self, *, base_url: str = DEFAULT_BASE_URL, timeout: float = 60.0, auto_start: bool = True) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.auto_start = auto_start
        self.backend_process: subprocess.Popen[Any] | None = None
        if self.auto_start and self._is_local_base_url():
            self._ensure_backend(wait_seconds=10.0)
        self.client = DockUPClient(base_url=base_url, timeout=timeout)

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = str(request.get("method") or "")
        request_id = request.get("id")
        try:
            if method == "initialize":
                return self._result(
                    request_id,
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}, "resources": {}},
                        "serverInfo": SERVER_INFO,
                    },
                )
            if method == "notifications/initialized":
                return None
            if method == "ping":
                return self._result(request_id, {})
            if method == "tools/list":
                return self._result(request_id, {"tools": TOOLS})
            if method == "tools/call":
                params = request.get("params") if isinstance(request.get("params"), dict) else {}
                return self._result(request_id, self._call_tool(str(params.get("name") or ""), params.get("arguments") or {}))
            if method == "resources/list":
                return self._result(
                    request_id,
                    {
                        "resources": [
                            {
                                "uri": GUIDE_URI,
                                "name": "DockUP control guide",
                                "description": "Short non-prescriptive workflow and safety guide for DockUP MCP use.",
                                "mimeType": "text/markdown",
                            }
                        ]
                    },
                )
            if method == "resources/read":
                params = request.get("params") if isinstance(request.get("params"), dict) else {}
                if str(params.get("uri") or "") != GUIDE_URI:
                    return self._error(request_id, -32602, f"Unknown resource: {params.get('uri')}")
                return self._result(request_id, {"contents": [{"uri": GUIDE_URI, "mimeType": "text/markdown", "text": GUIDE_TEXT}]})
            return self._error(request_id, -32601, f"Unsupported MCP method: {method}")
        except Exception as exc:
            return self._error(request_id, -32000, f"{type(exc).__name__}: {exc}")

    def _call_tool(self, name: str, arguments: Any) -> dict[str, Any]:
        args = arguments if isinstance(arguments, dict) else {}
        if name == "dockup_state":
            payload = self._state(args)
        elif name == "dockup_assets":
            payload = self._assets(args)
        elif name == "dockup_mutate":
            payload = self._mutate(args)
        elif name == "dockup_queue":
            payload = self._queue(args)
        elif name == "dockup_validate":
            payload = self._validate(args)
        elif name == "dockup_backend":
            payload = self._backend(args)
        elif name == "dockup_report":
            payload = self._report(args)
        else:
            raise ValueError(f"Unknown tool: {name}")
        return {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}],
            "isError": not bool(payload.get("ok", not payload.get("error"))),
        }

    @staticmethod
    def _data(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _queue_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = DockUPMCPServer._data(payload)
        rows = data.get("queue") if isinstance(data.get("queue"), list) else []
        return [row for row in rows if isinstance(row, dict)]

    @staticmethod
    def _as_bool(value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        return bool(value)

    @staticmethod
    def _compact_envelope(action: str, raw: dict[str, Any], facts: dict[str, Any] | None = None, *, response: str = "summary") -> dict[str, Any]:
        data = DockUPMCPServer._data(raw)
        error = raw.get("error")
        if isinstance(error, dict):
            error_text = str(error.get("message") or error.get("code") or "")
        else:
            error_text = str(error or data.get("error") or "")
        out = {
            "ok": bool(raw.get("ok", not error_text)),
            "action": action,
            "summary": str(raw.get("message") or data.get("summary") or ""),
            "facts": facts or {},
            "warnings": [],
            "error": error_text or None,
        }
        if response == "full":
            out["raw"] = raw
        return out

    @staticmethod
    def _state_facts(state: dict[str, Any]) -> dict[str, Any]:
        data = DockUPMCPServer._data(state)
        queue = data.get("queue") if isinstance(data.get("queue"), list) else []
        total_runs = sum(int(row.get("run_count") or 0) for row in queue if isinstance(row, dict))
        return {
            "mode": data.get("mode"),
            "selected_receptor": data.get("selected_receptor"),
            "selected_chain": data.get("selected_chain"),
            "selected_ligand": data.get("selected_ligand"),
            "active_ligands": data.get("active_ligands") or [],
            "queue_count": data.get("queue_count", len(queue)),
            "total_runs": total_runs,
            "runs_per_job": data.get("runs"),
            "run_status": data.get("run_status"),
            "gridbox_count": len(data.get("agent_grid_data") if isinstance(data.get("agent_grid_data"), dict) else {}),
            "out_root_name": data.get("out_root_name"),
        }

    @staticmethod
    def _pdb_file_mismatches(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
        mismatches: list[dict[str, str]] = []
        for row in rows:
            pdb_id = str(row.get("pdb_id") or "").strip().upper()
            pdb_file = str(row.get("pdb_file") or row.get("name") or "").strip()
            if not pdb_id or not pdb_file:
                continue
            expected = f"{pdb_id}.PDB"
            if not pdb_file.upper().endswith(expected):
                mismatches.append({"pdb_id": pdb_id, "pdb_file": pdb_file})
        return mismatches

    def _state(self, args: dict[str, Any]) -> dict[str, Any]:
        view = str(args.get("view") or "summary")
        limit = int(args.get("limit") or 20)
        raw = self.client.get_state()
        if view == "full":
            return raw
        data = self._data(raw)
        facts = self._state_facts(raw)
        if view == "queue":
            rows = self._queue_rows(raw)
            facts["queue"] = rows[:limit] if limit > 0 else []
        elif view == "grid":
            facts["agent_grid_data"] = data.get("agent_grid_data") or {}
        elif view == "ligands":
            facts = {"active_ligands": data.get("active_ligands") or []}
        elif view == "run":
            facts = {key: facts.get(key) for key in ["run_status", "queue_count", "total_runs"]}
        elif view == "receptors":
            facts = {"selection_map": data.get("selection_map") or {}, "selected_receptor": data.get("selected_receptor")}
        return self._compact_envelope("state.get", raw, facts)

    def _assets(self, args: dict[str, Any]) -> dict[str, Any]:
        view = str(args.get("view") or "summary")
        response = str(args.get("response") or ("full" if view == "full" else "summary"))
        receptors = self.client.list_receptors()
        ligands = self.client.list_ligands()
        receptor_rows = self._data(receptors).get("receptors") or []
        ligand_rows = self._data(ligands).get("ligands") or []
        receptor_rows = [row for row in receptor_rows if isinstance(row, dict)]
        mismatches = self._pdb_file_mismatches(receptor_rows)
        facts: dict[str, Any] = {
            "receptor_count": len(receptor_rows),
            "ligand_count": len(ligand_rows if isinstance(ligand_rows, list) else []),
            "mismatch_count": len(mismatches),
        }
        if view in {"receptors", "full"}:
            facts["receptors"] = receptor_rows
        if view in {"ligands", "full"}:
            facts["ligands"] = ligand_rows
        if view in {"mismatches", "full"}:
            facts["mismatches"] = mismatches
        out = self._compact_envelope("assets.inspect", {"ok": receptors.get("ok") and ligands.get("ok"), "message": "assets inspected"}, facts, response=response)
        if mismatches:
            out["warnings"] = [f"{len(mismatches)} receptor file mismatch(es) detected"]
        if response == "full":
            out["raw"] = {"receptors": receptors, "ligands": ligands}
        return out

    def _mutate(self, args: dict[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "").strip()
        payload = args.get("payload") if isinstance(args.get("payload"), dict) else {}
        response = str(args.get("response") or "summary")
        raw: dict[str, Any]
        if action == "receptor.load":
            pdb_ids = payload.get("pdb_ids") or payload.get("receptors") or ""
            if isinstance(pdb_ids, list):
                pdb_ids = ",".join(str(item) for item in pdb_ids)
            raw = self.client.load_receptors(str(pdb_ids))
        elif action == "receptor.remove":
            raw = self.client.remove_receptor(str(payload.get("pdb_id") or payload.get("target") or ""))
        elif action == "receptor.delete_file":
            raw = self.client.delete_receptor(str(payload.get("target") or payload.get("pdb_id") or payload.get("name") or ""))
        elif action == "receptor.clear":
            raw = self.client.clear_receptors()
        elif action == "receptor.reload":
            pdb_id = str(payload.get("pdb_id") or payload.get("target") or "").strip().upper()
            if not pdb_id:
                raise ValueError("receptor.reload requires payload.pdb_id")
            removed = self.client.remove_receptor(pdb_id)
            loaded = self.client.load_receptors(pdb_id)
            raw = {"ok": bool(loaded.get("ok", not loaded.get("error"))), "action": "receptor.reload", "message": f"reloaded receptor: {pdb_id}", "data": {"removed": removed, "loaded": loaded}}
        elif action == "ligand.generate":
            specs = payload.get("specs") if isinstance(payload.get("specs"), list) else payload.get("ligand_specs")
            if not isinstance(specs, list):
                specs = [payload]
            raw = self.client.generate_ligands(specs, reset=self._as_bool(payload.get("reset"), False), activate=self._as_bool(payload.get("activate"), True))
        elif action == "ligand.active.set":
            names = payload.get("names") if isinstance(payload.get("names"), list) else []
            raw = self.client.set_active_ligands([str(item) for item in names], replace=self._as_bool(payload.get("replace"), True))
        elif action == "grid.set_many":
            grid_data = payload.get("grid_data") if isinstance(payload.get("grid_data"), dict) else payload
            raw = self.client.set_gridboxes(grid_data)
        elif action == "config.set":
            raw = self.client.set_config(**payload)
        elif action == "state.repair":
            raw = self._repair_state(payload)
        else:
            raise ValueError(f"Unsupported dockup_mutate action: {action}")
        return self._compact_envelope(action, raw, self._state_facts(self.client.get_state()), response=response)

    def _repair_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        repair_receptors = self._as_bool(payload.get("receptors"), True)
        repaired: list[str] = []
        if repair_receptors:
            receptors = self.client.list_receptors()
            rows = self._data(receptors).get("receptors") or []
            for row in [item for item in rows if isinstance(item, dict)]:
                pdb_id = str(row.get("pdb_id") or "").strip().upper()
                name = str(row.get("name") or "").strip().upper()
                if pdb_id and name and not name.endswith(f"{pdb_id}.PDB"):
                    self.client.remove_receptor(pdb_id)
                    self.client.load_receptors(pdb_id)
                    repaired.append(pdb_id)
        return {"ok": True, "action": "state.repair", "message": f"repaired receptors: {len(repaired)}", "data": {"repaired_receptors": repaired}}

    def _is_local_base_url(self) -> bool:
        parsed = urlparse(self.base_url)
        host = (parsed.hostname or "").lower()
        return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

    def _backend_host_port(self) -> tuple[str, int]:
        parsed = urlparse(self.base_url)
        host = parsed.hostname or "127.0.0.1"
        port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
        return host, port

    def _backend_running(self) -> bool:
        host, port = self._backend_host_port()
        connect_host = "127.0.0.1" if host in {"0.0.0.0", "::1"} else host
        try:
            with socket.create_connection((connect_host, port), timeout=0.35):
                return True
        except OSError:
            return False

    def _ensure_backend(self, *, wait_seconds: float = 10.0) -> dict[str, Any]:
        host, port = self._backend_host_port()
        if not self._is_local_base_url():
            return {"ok": False, "action": "backend.start", "message": "backend auto-start is only supported for local URLs", "data": {"base_url": self.base_url}, "error": "non-local base_url"}
        if self._backend_running():
            return {"ok": True, "action": "backend.status", "message": f"DockUP backend is already listening on {self.base_url}", "data": {"base_url": self.base_url, "running": True, "started": False}, "error": None}
        python_bin = BASE / ".venv" / "bin" / "python"
        interpreter = str(python_bin if python_bin.exists() else Path(sys.executable))
        log_path = Path(os.getenv("DOCKUP_MCP_BACKEND_LOG", f"/tmp/dockup_mcp_uvicorn_{port}.log"))
        cmd = [
            interpreter,
            "-m",
            "uvicorn",
            "docking_app.app:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
        ]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log_fh:
            self.backend_process = subprocess.Popen(
                cmd,
                cwd=str(BASE),
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=log_fh,
                start_new_session=True,
            )
        deadline = time.time() + max(0.0, float(wait_seconds))
        while time.time() <= deadline:
            if self._backend_running():
                return {
                    "ok": True,
                    "action": "backend.start",
                    "message": f"DockUP backend started on {self.base_url}",
                    "data": {"base_url": self.base_url, "running": True, "started": True, "pid": self.backend_process.pid if self.backend_process else None, "log_path": str(log_path)},
                    "error": None,
                }
            time.sleep(0.2)
        return {
            "ok": False,
            "action": "backend.start",
            "message": f"DockUP backend did not become ready within {wait_seconds:g}s",
            "data": {"base_url": self.base_url, "host": host, "port": port, "log_path": str(log_path), "pid": self.backend_process.pid if self.backend_process else None},
            "error": "backend not ready",
        }

    def _backend(self, args: dict[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "status")
        response = str(args.get("response") or "summary")
        if action == "status":
            raw = {
                "ok": True,
                "action": "backend.status",
                "message": "backend status checked",
                "data": {"base_url": self.base_url, "local": self._is_local_base_url(), "running": self._backend_running()},
                "error": None,
            }
        elif action == "start":
            raw = self._ensure_backend(wait_seconds=float(args.get("wait_seconds") or 10.0))
        else:
            raise ValueError(f"Unsupported dockup_backend action: {action}")
        data = self._data(raw)
        facts = {key: data.get(key) for key in ["base_url", "local", "running", "started", "pid", "log_path"] if key in data}
        return self._compact_envelope(action, raw, facts, response=response)

    def _queue(self, args: dict[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "").strip()
        payload = args.get("payload") if isinstance(args.get("payload"), dict) else {}
        response = str(args.get("response") or "summary")
        if action == "prepare":
            raw = self.client.prepare_queue(payload)
        elif action == "build":
            raw = self.client.build_queue(replace_queue=self._as_bool(payload.get("replace_queue"), True))
        elif action == "list":
            raw = self.client.list_queue()
        elif action == "clear":
            raw = self.client.remove_queue_batch("")
        elif action == "remove_batch":
            raw = self.client.remove_queue_batch(str(payload.get("batch_id") or ""))
        else:
            raise ValueError(f"Unsupported dockup_queue action: {action}")
        rows = self._queue_rows(raw)
        facts = self._state_facts(self.client.get_state())
        if rows:
            facts["queue_count"] = len(rows)
            facts["total_runs"] = sum(int(row.get("run_count") or 0) for row in rows)
        if response == "full":
            facts["queue"] = rows
        return self._compact_envelope(f"queue.{action}", raw, facts, response=response)

    def _report(self, args: dict[str, Any]) -> dict[str, Any]:
        action = str(args.get("action") or "").strip()
        payload = args.get("payload") if isinstance(args.get("payload"), dict) else {}
        response = str(args.get("response") or "summary")
        raw: dict[str, Any]
        if action == "results.folders":
            raw = self.client.list_result_folders()
        elif action == "results.scan":
            raw = self.client.scan_results(root_path=str(payload.get("root_path") or "data/dock"))
        elif action == "results.detail":
            raw = self.client.get_result_detail(result_dir=str(payload.get("result_dir") or payload.get("source_path") or ""))
        elif action == "report.list":
            raw = self.client.list_reports(
                root_path=str(payload.get("root_path") or ""),
                source_path=str(payload.get("source_path") or ""),
                output_path=str(payload.get("output_path") or ""),
                linked_path=str(payload.get("linked_path") or ""),
            )
        elif action == "report.images":
            raw = self.client.list_report_images(
                root_path=str(payload.get("root_path") or ""),
                source_path=str(payload.get("source_path") or ""),
                output_path=str(payload.get("output_path") or ""),
                images_root_path=str(payload.get("images_root_path") or ""),
            )
        elif action == "report.preview":
            raw = self.client.report_preview(
                root_path=str(payload.get("root_path") or ""),
                source_path=str(payload.get("source_path") or ""),
                receptor_id=str(payload.get("receptor_id") or ""),
                run_name=str(payload.get("run_name") or ""),
                render_mode=str(payload.get("render_mode") or ""),
            )
        elif action == "report.metadata.get":
            raw = self.client.get_report_root_metadata(root_path=str(payload.get("root_path") or ""), source_path=str(payload.get("source_path") or ""))
        elif action == "report.metadata.set":
            raw = self.client.save_report_root_metadata(**payload)
        elif action == "report.doc_config.get":
            raw = self.client.get_report_doc_config(root_path=str(payload.get("root_path") or ""), source_path=str(payload.get("source_path") or ""))
        elif action == "report.doc_config.set":
            raw = self.client.save_report_doc_config(**payload)
        elif action == "report.source.delete":
            raw = self.client.delete_report_source(root_path=str(payload.get("root_path") or ""), source_path=str(payload.get("source_path") or ""))
        elif action == "report.images.delete":
            raw = self.client.delete_report_image(
                root_path=str(payload.get("root_path") or ""),
                source_path=str(payload.get("source_path") or ""),
                output_path=str(payload.get("output_path") or ""),
                images_root_path=str(payload.get("images_root_path") or ""),
                path=str(payload.get("path") or ""),
            )
        elif action == "report.images.delete_all":
            raw = self.client.delete_all_report_images(
                root_path=str(payload.get("root_path") or ""),
                source_path=str(payload.get("source_path") or ""),
                output_path=str(payload.get("output_path") or ""),
                scope=str(payload.get("scope") or "all"),
            )
        elif action == "report.graphs":
            scripts = payload.get("scripts") if isinstance(payload.get("scripts"), list) else None
            raw = self.client.trigger_report_graphs(
                root_path=str(payload.get("root_path") or "data/dock"),
                source_path=str(payload.get("source_path") or ""),
                output_path=str(payload.get("output_path") or ""),
                linked_path=str(payload.get("linked_path") or ""),
                scripts=[str(item) for item in scripts] if scripts is not None else None,
            )
        elif action == "report.render":
            render_payload = dict(payload)
            if "dpi" in render_payload and "render_dpi" not in render_payload:
                render_payload["render_dpi"] = render_payload["dpi"]
            raw = self.client.trigger_report_render(**render_payload)
        elif action == "report.render.stop":
            raw = self.client.stop_report_render()
        elif action == "report.compile":
            raw = self.client.compile_report(**payload)
        elif action == "report.status":
            raw = self.client.get_report_status()
        else:
            raise ValueError(f"Unsupported dockup_report action: {action}")
        return self._compact_report(action, raw, response=response)

    def _compact_report(self, action: str, raw: dict[str, Any], *, response: str = "summary") -> dict[str, Any]:
        data = self._data(raw)
        facts: dict[str, Any] = {}
        for key in ["status", "task", "progress", "total", "root_path", "source_path", "output_path", "doc_path"]:
            value = data.get(key, raw.get(key))
            if value not in (None, "", []):
                facts[key] = value
        for key in ["results", "averages", "folders", "render_images", "plot_images", "images", "source_folders"]:
            value = data.get(key, raw.get(key))
            if isinstance(value, list):
                facts[f"{key}_count"] = len(value)
                facts[key] = value[:10]
        for key in ["errors", "warnings"]:
            value = data.get(key, raw.get(key))
            if isinstance(value, list) and value:
                facts[key] = value[:10]
        out = self._compact_envelope(action, raw, facts, response=response)
        if "warnings" in facts:
            out["warnings"] = facts["warnings"]
        return out

    def _validate(self, args: dict[str, Any]) -> dict[str, Any]:
        scope = str(args.get("scope") or "all")
        checks = args.get("checks") if isinstance(args.get("checks"), dict) else {}
        response = str(args.get("response") or "summary")
        state = self.client.get_state()
        data = self._data(state)
        queue = self._queue_rows(state)
        failures: list[dict[str, Any]] = []
        warnings: list[str] = []

        def expect(key: str, actual: Any) -> None:
            if key in checks and checks[key] != actual:
                failures.append({"check": key, "expected": checks[key], "actual": actual})

        total_runs = sum(int(row.get("run_count") or 0) for row in queue)
        expect("queue_count", data.get("queue_count", len(queue)))
        expect("total_runs", total_runs)
        expect("run_status", data.get("run_status"))
        expect("mode", data.get("mode"))
        if "active_ligands" in checks:
            expected = sorted(str(item) for item in checks.get("active_ligands") or [])
            actual = sorted(str(item) for item in data.get("active_ligands") or [])
            if expected != actual:
                failures.append({"check": "active_ligands", "expected": expected, "actual": actual})
        if "job_type" in checks:
            actual_types = sorted({str(row.get("job_type") or "") for row in queue})
            expected_type = str(checks.get("job_type") or "")
            if actual_types != [expected_type]:
                failures.append({"check": "job_type", "expected": expected_type, "actual": actual_types})
        if "grid_size" in checks:
            expected_grid = [float(item) for item in checks.get("grid_size") or []]
            bad = []
            for row in queue:
                grid = row.get("grid_params") if isinstance(row.get("grid_params"), dict) else {}
                actual = [float(grid.get("sx", -1)), float(grid.get("sy", -1)), float(grid.get("sz", -1))]
                if actual != expected_grid:
                    bad.append({"pdb_id": row.get("pdb_id"), "ligand": row.get("ligand_name"), "grid": actual})
            if bad:
                failures.append({"check": "grid_size", "expected": expected_grid, "bad_count": len(bad), "examples": bad[:5]})
        if self._as_bool(checks.get("pdb_file_matches_pdb_id"), False):
            mismatches = self._pdb_file_mismatches(queue)
            if mismatches:
                failures.append({"check": "pdb_file_matches_pdb_id", "bad_count": len(mismatches), "examples": mismatches[:5]})
        if scope in {"assets", "all"}:
            asset_mismatches = self._assets({"view": "mismatches"}).get("facts", {}).get("mismatches", [])
            if asset_mismatches:
                warnings.append(f"{len(asset_mismatches)} asset mismatch(es)")
        facts = {
            "passed": not failures,
            "failure_count": len(failures),
            "queue_count": data.get("queue_count", len(queue)),
            "total_runs": total_runs,
            "run_status": data.get("run_status"),
        }
        out = {"ok": not failures, "action": "validate", "summary": "validation passed" if not failures else "validation failed", "facts": facts, "warnings": warnings, "error": None if not failures else "validation failed"}
        if failures or response == "full":
            out["failures"] = failures
        if response == "full":
            out["state"] = state
        return out

    @staticmethod
    def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _read_exact(stream: Any, length: int) -> bytes:
    """Read exactly *length* bytes from a binary stream."""
    chunks: list[bytes] = []
    remaining = length
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_message(stdin: Any) -> tuple[dict[str, Any], str] | None:
    headers: dict[str, str] = {}
    while True:
        line = stdin.buffer.readline()
        if not line:
            return None
        text = line.decode("utf-8").rstrip("\r\n")
        if not headers and text.lstrip().startswith("{"):
            return json.loads(text), "ndjson"
        if not text:
            break
        if ":" in text:
            key, value = text.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length") or 0)
    if length <= 0:
        return None
    body = _read_exact(stdin.buffer, length)
    if len(body) < length:
        return None
    return json.loads(body.decode("utf-8")), "headers"


def _write_message(stdout: Any, payload: dict[str, Any], *, framing: str = "headers") -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if framing == "ndjson":
        stdout.buffer.write(body + b"\n")
    else:
        stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    stdout.buffer.flush()


def serve_stdio(*, base_url: str = DEFAULT_BASE_URL, timeout: float = 60.0, auto_start: bool = True) -> None:
    server = DockUPMCPServer(base_url=base_url, timeout=timeout, auto_start=auto_start)
    while True:
        try:
            message = _read_message(sys.stdin)
        except Exception:
            _trace("read_error", {})
            continue
        if message is None:
            _trace("eof", {})
            break
        request, framing = message
        _trace("request", {"id": request.get("id"), "method": request.get("method")})
        response = server.handle(request)
        if response is not None:
            _trace("response", {"id": response.get("id"), "keys": list(response.keys()), "error": response.get("error")})
            _write_message(sys.stdout, response, framing=framing)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DockUP MCP stdio server")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--no-auto-start", action="store_true", help="Do not auto-start the local DockUP backend")
    args = parser.parse_args(argv)
    serve_stdio(base_url=args.base_url, timeout=args.timeout, auto_start=not args.no_auto_start)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
