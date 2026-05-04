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

    def _request_with_fallback(
        self,
        method: str,
        control_path: str,
        legacy_path: str,
        *,
        json_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self._request(method, control_path, json_payload=json_payload)
        if int(payload.get("status_code") or 0) == 404:
            return self._request(method, legacy_path, json_payload=json_payload)
        return payload

    @staticmethod
    def _query_path(path: str, **params: Any) -> str:
        clean = {key: value for key, value in params.items() if value not in {None, ""}}
        if not clean:
            return path
        return f"{path}?{urlencode(clean, doseq=True)}"

    def get_state(self) -> dict[str, Any]:
        payload = self._request("GET", "/api/state")
        if int(payload.get("status_code") or 0) == 404:
            return self._request("GET", "/api/control/state")
        return payload

    def get_run_status(self) -> dict[str, Any]:
        return self._request("GET", "/api/control/run/status")

    def list_receptors(self) -> dict[str, Any]:
        return self._request_with_fallback("GET", "/api/control/receptors/list", "/api/receptors/list")

    def load_receptors(self, pdb_ids: str) -> dict[str, Any]:
        return self._request_with_fallback("POST", "/api/control/receptors/load", "/api/receptors/load", json_payload={"pdb_ids": pdb_ids})

    def select_receptor(self, pdb_id: str) -> dict[str, Any]:
        return self._request_with_fallback("POST", "/api/control/receptors/select", "/api/receptors/select", json_payload={"pdb_id": pdb_id})

    def delete_receptor(self, target: str) -> dict[str, Any]:
        return self._request("POST", "/api/control/receptors/delete", json_payload={"target": target})

    def remove_receptor(self, pdb_id: str) -> dict[str, Any]:
        return self._request("POST", "/api/receptors/remove", json_payload={"pdb_id": pdb_id})

    def clear_receptors(self) -> dict[str, Any]:
        return self._request("POST", "/api/control/receptors/clear", json_payload={})

    def list_ligands(self) -> dict[str, Any]:
        return self._request_with_fallback("GET", "/api/control/ligands/list", "/api/ligands/list")

    def fetch_ligands(self, ligand_ids: str) -> dict[str, Any]:
        return self._request_with_fallback("POST", "/api/control/ligands/fetch", "/api/ligands/fetch", json_payload={"ligand_ids": ligand_ids})

    def delete_ligand(self, name: str) -> dict[str, Any]:
        return self._request("POST", "/api/control/ligands/delete", json_payload={"name": name})

    def clear_ligands(self) -> dict[str, Any]:
        return self._request("POST", "/api/control/ligands/clear", json_payload={})

    def set_active_ligands(self, names: list[str], *, replace: bool = True) -> dict[str, Any]:
        return self._request("POST", "/api/control/ligands/active/set", json_payload={"names": names, "replace": replace})

    def generate_ligands(self, specs: list[dict[str, Any]], *, reset: bool = False, activate: bool = True) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/control/ligands/generate",
            json_payload={"specs": specs, "reset": reset, "activate": activate},
        )

    def inspect_assets(self) -> dict[str, Any]:
        payload = self._request("GET", "/api/control/assets/inspect")
        if int(payload.get("status_code") or 0) != 404:
            return payload
        state = self.get_state()
        receptors = state.get("receptor_meta") if isinstance(state.get("receptor_meta"), list) else []
        ligands = self.list_ligands()
        return {
            "ok": True,
            "action": "assets.inspect",
            "message": f"assets: {len(receptors)} receptor(s), {len(ligands.get('ligands') or [])} ligand(s)",
            "data": {"state": state, "receptors": receptors, "ligands": ligands.get("ligands") or []},
            "ui_hints": {"refresh": ["state", "receptors", "ligands"]},
        }

    def show_viewer(self, pdb_id: str, *, chain: str = "") -> dict[str, Any]:
        payload = self._request("POST", "/api/control/viewer/show", json_payload={"pdb_id": pdb_id, "chain": chain})
        if int(payload.get("status_code") or 0) != 404:
            return payload
        selected = self.select_receptor(pdb_id)
        if selected.get("error"):
            return selected
        detail = self.get_receptor_detail(pdb_id, chain=chain)
        pdb_text = str(detail.get("pdb_text") or "")
        return {
            "ok": bool(pdb_text),
            "action": "viewer.show",
            "message": f"viewer ready: {str(pdb_id or '').upper()} ({len(pdb_text)} pdb chars)" if pdb_text else f"viewer data missing: {pdb_id}",
            "data": {
                "pdb_id": detail.get("pdb_id") or str(pdb_id or "").upper(),
                "pdb_text_length": len(pdb_text),
                "chains": detail.get("chains") or [],
                "ligands_by_chain": detail.get("ligands_by_chain") or {},
                "pdb_file": detail.get("pdb_file") or "",
                "selected_chain": detail.get("selected_chain") or chain or "all",
                "selected_ligand": detail.get("selected_ligand") or "",
            },
            "ui_hints": {"refresh": ["state", "viewer"], "selected_receptor": str(pdb_id or "").upper()},
            "error": None if pdb_text else {"message": "Viewer receptor payload has no PDB text.", "recoverable": True},
        }

    def show_residues(self, pdb_id: str = "", *, residue: str = "TRP", chain: str = "all") -> dict[str, Any]:
        payload = self._request(
            "POST",
            "/api/control/viewer/residues",
            json_payload={"pdb_id": pdb_id, "residue": residue, "chain": chain},
        )
        if int(payload.get("status_code") or 0) != 404:
            return payload
        return self._legacy_show_residues(pdb_id=pdb_id, residue=residue, chain=chain)

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

    def set_gridboxes(self, grid_data: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return self._request("POST", "/api/control/gridbox/set-many", json_payload={"grid_data": grid_data})

    def set_config(self, **payload: Any) -> dict[str, Any]:
        return self._request("POST", "/api/control/config/set", json_payload=dict(payload))

    def list_queue(self) -> dict[str, Any]:
        return self._request("GET", "/api/control/queue/list")

    def build_queue(self, *, replace_queue: bool = True) -> dict[str, Any]:
        return self._request("POST", "/api/control/queue/build", json_payload={"replace_queue": replace_queue})

    def prepare_queue(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/control/queue/prepare", json_payload=dict(payload))

    def remove_queue_batch(self, batch_id: str) -> dict[str, Any]:
        return self._request("POST", "/api/control/queue/remove", json_payload={"batch_id": batch_id})

    def start_run(self, *, test_mode: bool = False, batch_id: int | None = None) -> dict[str, Any]:
        return self._request("POST", "/api/control/run/start", json_payload={"test_mode": test_mode, "batch_id": batch_id})

    def stop_run(self) -> dict[str, Any]:
        return self._request("POST", "/api/control/run/stop", json_payload={})

    def get_latest_control_event(self, *, after_id: int = 0) -> dict[str, Any]:
        return self._request("GET", self._query_path("/api/control/events/latest", after_id=after_id))

    def get_receptor_detail(self, pdb_id: str, *, chain: str = "") -> dict[str, Any]:
        path = f"/api/receptors/{str(pdb_id or '').strip()}"
        if chain:
            path = f"{path}?chain={chain}"
        return self._request("GET", path)

    @staticmethod
    def _residue_alias(value: str) -> str:
        raw = str(value or "").strip().upper()
        aliases = {
            "TRYPTOPHAN": "TRP",
            "TRYPTHOPHAN": "TRP",
            "TRYPTOFAN": "TRP",
            "TRP": "TRP",
            "TYROSINE": "TYR",
            "PHENYLALANINE": "PHE",
            "HISTIDINE": "HIS",
        }
        return aliases.get(raw, raw[:3])

    @staticmethod
    def _residue_selection(resno: str, chain: str) -> str:
        clean_chain = str(chain or "").strip()
        return f"{resno}:{clean_chain}" if clean_chain and clean_chain != "_" else str(resno)

    def _legacy_show_residues(self, *, pdb_id: str = "", residue: str = "TRP", chain: str = "all") -> dict[str, Any]:
        target = str(pdb_id or "").strip().upper()
        if not target:
            state = self.get_state()
            target = str(state.get("selected_receptor") or "").strip().upper()
        detail = self.get_receptor_detail(target, chain="" if chain in {"", "all"} else chain)
        pdb_text = str(detail.get("pdb_text") or "")
        target_residue = self._residue_alias(residue)
        target_chain = str(chain or "all").strip()
        residues: dict[tuple[str, str, str], dict[str, Any]] = {}
        for line in pdb_text.splitlines():
            if not line.startswith(("ATOM  ", "HETATM")):
                continue
            resname = line[17:20].strip().upper()
            if resname != target_residue:
                continue
            line_chain = line[21].strip() or "_"
            if target_chain not in {"", "all"} and line_chain != target_chain:
                continue
            resno = line[22:26].strip()
            if not resno:
                continue
            row = residues.setdefault(
                (line_chain, resno, resname),
                {
                    "chain": line_chain,
                    "resno": resno,
                    "resname": resname,
                    "atom_count": 0,
                    "bbox": {"minX": 1e9, "minY": 1e9, "minZ": 1e9, "maxX": -1e9, "maxY": -1e9, "maxZ": -1e9},
                },
            )
            try:
                x, y, z = float(line[30:38]), float(line[38:46]), float(line[46:54])
            except ValueError:
                continue
            row["atom_count"] += 1
            bbox = row["bbox"]
            bbox["minX"] = min(bbox["minX"], x)
            bbox["minY"] = min(bbox["minY"], y)
            bbox["minZ"] = min(bbox["minZ"], z)
            bbox["maxX"] = max(bbox["maxX"], x)
            bbox["maxY"] = max(bbox["maxY"], y)
            bbox["maxZ"] = max(bbox["maxZ"], z)
        rows = sorted(
            residues.values(),
            key=lambda item: (
                str(item["chain"]),
                0 if str(item["resno"]).isdigit() else 1,
                int(item["resno"]) if str(item["resno"]).isdigit() else str(item["resno"]),
            ),
        )
        selection = " or ".join(self._residue_selection(row["resno"], row["chain"]) for row in rows[:64])
        combined_bbox: dict[str, float] | None = None
        if rows:
            combined_bbox = {
                "minX": round(min(row["bbox"]["minX"] for row in rows), 3),
                "minY": round(min(row["bbox"]["minY"] for row in rows), 3),
                "minZ": round(min(row["bbox"]["minZ"] for row in rows), 3),
                "maxX": round(max(row["bbox"]["maxX"] for row in rows), 3),
                "maxY": round(max(row["bbox"]["maxY"] for row in rows), 3),
                "maxZ": round(max(row["bbox"]["maxZ"] for row in rows), 3),
            }
        viewer_selection = {
            "label": f"{target} {target_residue} ({len(rows)})",
            "selection": selection,
            "residues": rows[:64],
            "bbox": combined_bbox,
        } if rows and combined_bbox else None
        data = {
            "summary": f"Found {len(rows)} {target_residue} residue(s) in {target}.",
            "receptor": target,
            "residue": target_residue,
            "chain": target_chain,
            "residues": rows[:64],
            "selection": selection,
            "viewer_selection": viewer_selection,
        }
        return {
            "ok": True,
            "action": "viewer.residues",
            "message": data["summary"],
            "data": data,
            "ui_hints": {"refresh": ["state", "viewer", "grid-selection"], "viewer_selection": viewer_selection, "selected_receptor": target},
            "error": None,
        }

    def list_result_folders(self) -> dict[str, Any]:
        return self._request_with_fallback("GET", "/api/control/results/folders", "/api/results/dock-folders")

    def scan_results(self, *, root_path: str = "data/dock") -> dict[str, Any]:
        return self._request_with_fallback("POST", "/api/control/results/scan", "/api/results/scan", json_payload={"root_path": root_path})

    def get_result_detail(self, *, result_dir: str) -> dict[str, Any]:
        return self._request_with_fallback("POST", "/api/control/results/detail", "/api/results/detail", json_payload={"result_dir": result_dir})

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
