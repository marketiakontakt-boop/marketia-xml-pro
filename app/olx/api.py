"""Thin HTTP wrapper for OLX Partner API.

Rate-limit: OLX allows 4500 req / 5 min / IP → ~15 req/s. We cap to 10 req/s
to leave headroom. Blocking at 30 min when exceeded, so we prefer to throttle
client-side. Retries on 429 / 5xx with exponential backoff.

Reference: https://developer.olx.pl/api/doc/
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.olx.auth import OLXAuth


class OLXAPIError(RuntimeError):
    """Non-2xx response from the OLX API."""

    def __init__(self, status: int, message: str, response: dict | None = None) -> None:
        super().__init__(f"OLX API {status}: {message}")
        self.status = status
        self.response = response or {}


class OLXClient:
    """Thin HTTP client. Reuses OLXAuth for token refresh."""

    BASE_URL = "https://www.olx.pl/api/partner"
    API_VERSION = "2.0"
    RATE_LIMIT_RPS = 10.0
    MAX_RETRIES = 3
    BACKOFF_BASE = 1.5  # 1.5, 2.25, 3.375s

    def __init__(self, auth: "OLXAuth", timeout: int = 30) -> None:
        self.auth = auth
        self.timeout = timeout
        self._last_request_ts = 0.0
        self._lock = threading.Lock()

    # ── Rate limiting ───────────────────────────────────────────────────────

    def _wait_rate_limit(self) -> None:
        """Ensure at most RATE_LIMIT_RPS req/s (thread-safe)."""
        min_interval = 1.0 / self.RATE_LIMIT_RPS
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_ts
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            self._last_request_ts = time.monotonic()

    # ── Headers ─────────────────────────────────────────────────────────────

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {
            "Version": self.API_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.auth.get_valid_token()}",
        }
        if extra:
            headers.update(extra)
        return headers

    # ── Core request ────────────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict | None = None,
        body: dict | None = None,
    ) -> dict:
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        data = json.dumps(body).encode("utf-8") if body is not None else None

        last_exc: Exception | None = None
        for attempt in range(self.MAX_RETRIES + 1):
            self._wait_rate_limit()
            req = urllib.request.Request(
                url, data=data, method=method, headers=self._headers()
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    if not raw:
                        return {}
                    return json.loads(raw)
            except urllib.error.HTTPError as exc:
                status = exc.code
                body_raw = exc.read().decode("utf-8", errors="replace")
                try:
                    body_json = json.loads(body_raw) if body_raw else {}
                except json.JSONDecodeError:
                    body_json = {"raw": body_raw}

                # Retry on 429 (rate limit) and 5xx
                if status == 429 or 500 <= status < 600:
                    if attempt < self.MAX_RETRIES:
                        wait = self.BACKOFF_BASE ** (attempt + 1)
                        time.sleep(wait)
                        last_exc = exc
                        continue
                raise OLXAPIError(
                    status=status,
                    message=body_json.get("error", {}).get("title", body_raw[:200])
                    if isinstance(body_json.get("error"), dict)
                    else body_raw[:200],
                    response=body_json,
                ) from exc
            except urllib.error.URLError as exc:
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.BACKOFF_BASE ** (attempt + 1))
                    last_exc = exc
                    continue
                raise OLXAPIError(status=0, message=f"Network error: {exc.reason}") from exc

        # Should never reach here — every branch either returns or raises.
        raise OLXAPIError(status=0, message=f"Max retries exceeded: {last_exc}")  # pragma: no cover

    # ── Verb helpers ────────────────────────────────────────────────────────

    def get(self, endpoint: str, params: dict | None = None) -> dict:
        return self._request("GET", endpoint, params=params)

    def post(self, endpoint: str, body: dict) -> dict:
        return self._request("POST", endpoint, body=body)

    def put(self, endpoint: str, body: dict) -> dict:
        return self._request("PUT", endpoint, body=body)

    def delete(self, endpoint: str) -> None:
        self._request("DELETE", endpoint)
