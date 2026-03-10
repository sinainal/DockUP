from __future__ import annotations

import pytest

from tests._support.api_client import ApiClient


pytestmark = [pytest.mark.api]


def test_openapi_schema_available(server_ready: None, api: ApiClient) -> None:
    payload = api.assert_ok(api.get("/openapi.json"), where="GET /openapi.json")
    assert str(payload.get("openapi") or "").startswith("3."), f"Unexpected OpenAPI version: {payload.get('openapi')}"
    paths = payload.get("paths")
    assert isinstance(paths, dict) and paths, "OpenAPI paths are missing or empty."


def test_openapi_contains_core_endpoints(server_ready: None, api: ApiClient) -> None:
    payload = api.assert_ok(api.get("/openapi.json"), where="GET /openapi.json")
    paths = payload.get("paths") or {}
    required = [
        "/api/state",
        "/api/mode",
        "/api/receptors/load",
        "/api/ligands/list",
        "/api/queue/build",
        "/api/run/start",
        "/api/run/status",
        "/api/reports/status",
    ]
    missing = [p for p in required if p not in paths]
    assert not missing, f"OpenAPI missing expected paths: {missing}"


def test_api_state_returns_json_content_type(server_ready: None, api: ApiClient) -> None:
    resp = api.get("/api/state")
    assert resp.status_code == 200, f"/api/state failed: {resp.status_code} {resp.text[:300]}"
    content_type = str(resp.headers.get("content-type") or "").lower()
    assert "application/json" in content_type, f"Unexpected content-type: {content_type}"
    payload = api.json(resp)
    assert "mode" in payload and "queue_count" in payload, f"Unexpected /api/state payload: {payload}"


def test_http_exception_shape_for_invalid_scan_path(server_ready: None, api: ApiClient) -> None:
    resp = api.post("/api/results/scan", {"root_path": "/tmp"})
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text[:300]}"
    payload = api.json(resp)
    assert "detail" in payload, f"FastAPI HTTPException shape missing detail: {payload}"
    assert isinstance(payload["detail"], str), f"detail must be a string: {payload}"

