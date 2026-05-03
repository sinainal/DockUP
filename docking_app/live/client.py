from __future__ import annotations

from typing import Any

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

    def get_state(self) -> dict[str, Any]:
        return self._request("GET", "/api/control/state")

    def get_run_status(self) -> dict[str, Any]:
        return self._request("GET", "/api/run/status")

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

    def get_receptor_detail(self, pdb_id: str, *, chain: str = "") -> dict[str, Any]:
        path = f"/api/receptors/{str(pdb_id or '').strip()}"
        if chain:
            path = f"{path}?chain={chain}"
        return self._request("GET", path)
