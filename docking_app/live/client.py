from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx


DEFAULT_BASE_URL = "http://localhost:8000"


class DockUPClientError(RuntimeError):
    """Raised when the live DockUP backend cannot be reached or parsed."""


class DockUPClient:
    """Thin HTTP client for a running DockUP backend.

    This client deliberately talks to the same API surface used by the UI. It
    does not import or mutate backend internals, which makes it suitable for the
    live CLI now and MCP tools later.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = str(base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._transport = transport

    def _request(self, method: str, path: str, *, json_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url_path = "/" + str(path or "").lstrip("/")
        timeout = httpx.Timeout(self.timeout, connect=min(self.timeout, 5.0))
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=timeout,
                follow_redirects=True,
                transport=self._transport,
            ) as client:
                response = client.request(method.upper(), url_path, json=json_payload)
        except httpx.RequestError as exc:
            raise DockUPClientError(f"Could not reach DockUP backend at {self.base_url}: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise DockUPClientError(
                f"DockUP backend returned non-JSON response for {method.upper()} {url_path}: HTTP {response.status_code}"
            ) from exc

        if not isinstance(payload, dict):
            raise DockUPClientError(
                f"DockUP backend returned unexpected JSON for {method.upper()} {url_path}: {type(payload).__name__}"
            )
        if response.status_code >= 400:
            payload.setdefault("status_code", response.status_code)
            payload.setdefault("error", payload.get("detail") or response.reason_phrase)
        return payload

    @staticmethod
    def _query_path(path: str, **params: Any) -> str:
        clean = {key: value for key, value in params.items() if value not in {None, ""}}
        if not clean:
            return path
        return f"{path}?{urlencode(clean, doseq=True)}"

    def get_state(self) -> dict[str, Any]:
        return self._request("GET", "/api/control/state")

    def get_run_status(self) -> dict[str, Any]:
        return self._request("GET", "/api/control/run/status")

    def list_receptors(self) -> dict[str, Any]:
        return self._request("GET", "/api/control/receptors/list")

    def load_receptors(self, pdb_ids: str) -> dict[str, Any]:
        return self._request("POST", "/api/control/receptors/load", json_payload={"pdb_ids": pdb_ids})

    def select_receptor(self, pdb_id: str) -> dict[str, Any]:
        return self._request("POST", "/api/control/receptors/select", json_payload={"pdb_id": pdb_id})

    def delete_receptor(self, target: str) -> dict[str, Any]:
        return self._request("POST", "/api/control/receptors/delete", json_payload={"target": target})

    def clear_receptors(self) -> dict[str, Any]:
        return self._request("POST", "/api/control/receptors/clear", json_payload={})

    def list_ligands(self) -> dict[str, Any]:
        return self._request("GET", "/api/control/ligands/list")

    def fetch_ligands(self, ligand_ids: str) -> dict[str, Any]:
        return self._request("POST", "/api/control/ligands/fetch", json_payload={"ligand_ids": ligand_ids})

    def delete_ligand(self, name: str) -> dict[str, Any]:
        return self._request("POST", "/api/control/ligands/delete", json_payload={"name": name})

    def clear_ligands(self) -> dict[str, Any]:
        return self._request("POST", "/api/control/ligands/clear", json_payload={})

    def show_viewer(self, pdb_id: str, *, chain: str = "") -> dict[str, Any]:
        return self._request("POST", "/api/control/viewer/show", json_payload={"pdb_id": pdb_id, "chain": chain})

    def select_workspace(self, receptor: str = "all", *, chain: str = "auto", native_ligand: str = "auto", dock_ligands: str = "all") -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/control/workspace/select",
            json_payload={"receptor": receptor, "chain": chain, "native_ligand": native_ligand, "dock_ligands": dock_ligands},
        )

    def set_gridbox(
        self,
        *,
        method: str = "native_ligand",
        size: float = 20.0,
        padding: float = 0.0,
        center: str = "",
        pocket_rank: int = 1,
        p2rank_mode: str = "fit",
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/control/gridbox/set",
            json_payload={
                "method": method,
                "size": size,
                "padding": padding,
                "center": center,
                "pocket_rank": pocket_rank,
                "p2rank_mode": p2rank_mode,
            },
        )

    def set_config(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "/api/control/config/set", json_payload=dict(payload))

    def list_queue(self) -> dict[str, Any]:
        return self._request("GET", "/api/control/queue/list")

    def build_queue(self, *, replace_queue: bool = True) -> dict[str, Any]:
        return self._request("POST", "/api/control/queue/build", json_payload={"replace_queue": replace_queue})

    def remove_queue_batch(self, batch_id: str) -> dict[str, Any]:
        return self._request("POST", "/api/control/queue/remove", json_payload={"batch_id": batch_id})

    def start_run(self, *, test_mode: bool = False, batch_id: int | None = None) -> dict[str, Any]:
        return self._request("POST", "/api/control/run/start", json_payload={"test_mode": test_mode, "batch_id": batch_id})

    def stop_run(self) -> dict[str, Any]:
        return self._request("POST", "/api/control/run/stop", json_payload={})

    def get_receptor_detail(self, pdb_id: str, *, chain: str = "") -> dict[str, Any]:
        path = f"/api/receptors/{str(pdb_id or '').strip()}"
        if chain:
            path = f"{path}?chain={chain}"
        return self._request("GET", path)

    def list_reports(self, *, root_path: str = "", source_path: str = "", output_path: str = "", linked_path: str = "") -> dict[str, Any]:
        return self._request(
            "GET",
            self._query_path(
                "/api/reports/list",
                root_path=root_path,
                source_path=source_path,
                output_path=output_path,
                linked_path=linked_path,
            ),
        )

    def report_preview(
        self,
        *,
        root_path: str = "",
        source_path: str = "",
        receptor_id: str = "",
        run_name: str = "",
        render_mode: str = "",
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            self._query_path(
                "/api/reports/preview",
                root_path=root_path,
                source_path=source_path,
                receptor_id=receptor_id,
                run_name=run_name,
                render_mode=render_mode,
            ),
        )

    def list_report_images(
        self,
        *,
        root_path: str = "",
        source_path: str = "",
        output_path: str = "",
        images_root_path: str = "",
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            self._query_path(
                "/api/reports/images",
                root_path=root_path,
                source_path=source_path,
                output_path=output_path,
                images_root_path=images_root_path,
            ),
        )

    def get_report_root_metadata(self, *, root_path: str = "", source_path: str = "") -> dict[str, Any]:
        return self._request("GET", self._query_path("/api/reports/root-metadata", root_path=root_path, source_path=source_path))

    def save_report_root_metadata(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "/api/reports/root-metadata", json_payload=dict(payload))

    def get_report_doc_config(self, *, root_path: str = "", source_path: str = "") -> dict[str, Any]:
        return self._request("GET", self._query_path("/api/reports/doc-config", root_path=root_path, source_path=source_path))

    def save_report_doc_config(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "/api/reports/doc-config", json_payload=dict(payload))

    def delete_report_source(self, *, root_path: str = "", source_path: str = "") -> dict[str, Any]:
        return self._request("POST", "/api/reports/source/delete", json_payload={"root_path": root_path, "source_path": source_path})

    def delete_all_report_images(
        self,
        *,
        root_path: str = "",
        source_path: str = "",
        output_path: str = "",
        scope: str = "all",
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/reports/images/delete-all",
            json_payload={"root_path": root_path, "source_path": source_path, "output_path": output_path, "scope": scope},
        )

    def delete_report_image(
        self,
        *,
        root_path: str = "",
        source_path: str = "",
        output_path: str = "",
        images_root_path: str = "",
        path: str = "",
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/reports/image/delete",
            json_payload={
                "root_path": root_path,
                "source_path": source_path,
                "output_path": output_path,
                "images_root_path": images_root_path,
                "path": path,
            },
        )

    def trigger_report_graphs(
        self,
        *,
        root_path: str = "data/dock",
        source_path: str = "",
        output_path: str = "",
        linked_path: str = "",
        scripts: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/reports/graphs",
            json_payload={
                "root_path": root_path,
                "source_path": source_path,
                "output_path": output_path,
                "linked_path": linked_path,
                "scripts": list(scripts or []),
            },
        )

    def trigger_report_render(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "/api/reports/render", json_payload=dict(payload))

    def stop_report_render(self) -> dict[str, Any]:
        return self._request("POST", "/api/reports/render/stop", json_payload={})

    def compile_report(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "/api/reports/compile", json_payload=dict(payload))

    def get_report_status(self) -> dict[str, Any]:
        return self._request("GET", "/api/reports/status")
