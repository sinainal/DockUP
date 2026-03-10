from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import requests


@dataclass
class ApiClient:
    base_url: str

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url.rstrip('/')}{path}"

    def get(self, path: str, *, timeout: int = 30, **kwargs: Any) -> requests.Response:
        return requests.get(self._url(path), timeout=timeout, **kwargs)

    def post(
        self,
        path: str,
        json_data: dict[str, Any] | None = None,
        *,
        timeout: int = 30,
        **kwargs: Any,
    ) -> requests.Response:
        return requests.post(self._url(path), json=json_data, timeout=timeout, **kwargs)

    @staticmethod
    def json(resp: requests.Response) -> dict[str, Any]:
        try:
            parsed = resp.json()
        except Exception as exc:
            raise AssertionError(f"Response is not JSON (HTTP {resp.status_code}): {resp.text[:300]}") from exc
        if not isinstance(parsed, dict):
            raise AssertionError(f"Expected JSON object, got {type(parsed).__name__}: {parsed}")
        return parsed

    def assert_ok(self, resp: requests.Response, *, where: str = "") -> dict[str, Any]:
        assert resp.status_code == 200, (
            f"{where or 'request'} failed: HTTP {resp.status_code}: {resp.text[:400]}"
        )
        return self.json(resp)

    def wait_until(
        self,
        predicate: Callable[[], bool],
        *,
        timeout_sec: int,
        interval_sec: float,
        timeout_message: str,
    ) -> None:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(interval_sec)
        raise TimeoutError(timeout_message)

