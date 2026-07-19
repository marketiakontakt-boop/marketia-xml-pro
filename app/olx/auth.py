"""OLX OAuth 2.0 — `authorization_code` grant.

Reference: https://developer.olx.pl/api/doc/#section/Autoryzacja

Flow:
1. `authorize_url()` builds URL for user consent.
2. `interactive_login()` opens browser + starts local HTTP server 127.0.0.1:8765
   to capture the `?code=...` redirect from OLX.
3. `exchange_code(code)` swaps the auth code for access_token + refresh_token.
4. `refresh_access(refresh_token)` refreshes near-expiry tokens.
5. `get_valid_token()` returns a still-valid access_token, refreshing on demand.

Tokens are persisted in SQLite (`olx_oauth_tokens`) via `save_olx_token` /
`get_olx_token` in `app.cache.sqlite_cache`.
"""
from __future__ import annotations

import http.server
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from typing import Any

from app.cache.sqlite_cache import (
    get_olx_token,
    open_cache,
    save_olx_token,
)


class OLXAuthError(RuntimeError):
    """Raised for any OAuth error (network, invalid response, refresh failure)."""


# Small margin so we refresh before the token actually expires mid-request.
_REFRESH_MARGIN_SECONDS = 300


class OLXAuth:
    """OAuth `authorization_code` client for OLX Partner API.

    Persistent token stored in SQLite. First run: `interactive_login()` opens
    browser and blocks until the local callback receives the code.
    """

    OAUTH_URL = "https://www.olx.pl/api/open/oauth/authorize"
    TOKEN_URL = "https://www.olx.pl/api/open/oauth/token"
    DEFAULT_REDIRECT = "http://127.0.0.1:8765/callback"
    DEFAULT_SCOPE = "v2 read write"
    CALLBACK_HOST = "127.0.0.1"
    CALLBACK_PORT = 8765

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str = DEFAULT_REDIRECT,
        scope: str = DEFAULT_SCOPE,
    ) -> None:
        if not client_id or not client_secret:
            raise OLXAuthError("client_id and client_secret are required")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scope = scope

    # ── URL builders ────────────────────────────────────────────────────────

    def authorize_url(self, state: str = "") -> str:
        """Build the consent URL user opens in browser."""
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "scope": self.scope,
            "redirect_uri": self.redirect_uri,
        }
        if state:
            params["state"] = state
        return f"{self.OAUTH_URL}?{urllib.parse.urlencode(params)}"

    # ── Interactive login ───────────────────────────────────────────────────

    def _start_callback_server(self, timeout: int = 300) -> str:
        """Block until OLX redirects with ?code=... (or timeout). Returns code."""
        captured: dict[str, str] = {}

        class _Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — stdlib signature
                return  # silence stderr

            def do_GET(self) -> None:  # noqa: N802 — stdlib signature
                parsed = urllib.parse.urlparse(self.path)
                qs = urllib.parse.parse_qs(parsed.query)
                if "code" in qs:
                    captured["code"] = qs["code"][0]
                    if "state" in qs:
                        captured["state"] = qs["state"][0]
                    body = (
                        b"<html><body style='font-family:sans-serif;"
                        b"padding:40px'>"
                        b"<h2>OK, mo\xc5\xbcesz wr\xc3\xb3ci\xc4\x87 do Marketia.</h2>"
                        b"<p>Autoryzacja OLX zako\xc5\x84czona sukcesem.</p>"
                        b"</body></html>"
                    )
                    self.send_response(200)
                elif "error" in qs:
                    captured["error"] = qs.get("error", ["unknown"])[0]
                    body = f"<html><body><h2>Blad: {captured['error']}</h2></body></html>".encode(
                        "utf-8"
                    )
                    self.send_response(400)
                else:
                    body = b"OK"
                    self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = http.server.HTTPServer((self.CALLBACK_HOST, self.CALLBACK_PORT), _Handler)
        server.timeout = 1.0

        stop_event = threading.Event()

        def _serve() -> None:
            while not stop_event.is_set() and "code" not in captured and "error" not in captured:
                server.handle_request()

        thread = threading.Thread(target=_serve, daemon=True)
        thread.start()
        thread.join(timeout=timeout)
        stop_event.set()
        server.server_close()

        if "error" in captured:
            raise OLXAuthError(f"OAuth authorization error: {captured['error']}")
        if "code" not in captured:
            raise OLXAuthError(
                f"Timeout waiting for OLX redirect callback (>{timeout}s). "
                f"Sprawdź, czy redirect_uri w panelu OLX = {self.redirect_uri}."
            )
        return captured["code"]

    def interactive_login(self, timeout: int = 300) -> dict:
        """Open browser, capture redirect, exchange code, persist token. Returns token dict."""
        url = self.authorize_url()
        try:
            webbrowser.open(url, new=1, autoraise=True)
        except webbrowser.Error as exc:  # pragma: no cover — env-dependent
            raise OLXAuthError(f"Nie można otworzyć przeglądarki: {exc}") from exc

        code = self._start_callback_server(timeout=timeout)
        token = self.exchange_code(code)
        self._persist(token)
        return token

    # ── Token endpoint calls ────────────────────────────────────────────────

    def exchange_code(self, code: str) -> dict:
        """POST /oauth/token with grant_type=authorization_code."""
        return self._token_request({
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": self.redirect_uri,
            "scope": self.scope,
        })

    def refresh_access(self, refresh_token: str) -> dict:
        """POST /oauth/token with grant_type=refresh_token."""
        token = self._token_request({
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "scope": self.scope,
        })
        self._persist(token)
        return token

    def _token_request(self, payload: dict) -> dict:
        """POST payload as form-urlencoded, parse JSON, add expires_at ISO string."""
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            self.TOKEN_URL,
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise OLXAuthError(f"OLX token endpoint {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise OLXAuthError(f"OLX token endpoint network error: {exc.reason}") from exc

        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OLXAuthError(f"OLX token response not JSON: {raw[:200]}") from exc

        if "access_token" not in body:
            raise OLXAuthError(f"OLX token response missing access_token: {body}")

        expires_in = int(body.get("expires_in", 3600))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        body["expires_at"] = expires_at.isoformat()
        return body

    # ── Persistence ─────────────────────────────────────────────────────────

    def _persist(self, token: dict) -> None:
        with open_cache() as conn:
            save_olx_token(
                conn,
                client_id=self.client_id,
                access_token=token["access_token"],
                refresh_token=token.get("refresh_token", ""),
                expires_at=token["expires_at"],
            )

    def get_valid_token(self) -> str:
        """Return access_token; refresh if <5min to expiry. Raises if not logged in yet."""
        with open_cache() as conn:
            cached = get_olx_token(conn, self.client_id)
        if not cached:
            raise OLXAuthError(
                "Brak tokenu OLX w cache. Uruchom najpierw `interactive_login()` "
                "aby przejść przez OAuth flow."
            )
        try:
            expires_at = datetime.fromisoformat(cached["expires_at"])
        except ValueError as exc:
            raise OLXAuthError(f"Uszkodzony expires_at w cache: {cached['expires_at']}") from exc
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        margin = timedelta(seconds=_REFRESH_MARGIN_SECONDS)
        if expires_at - datetime.now(timezone.utc) < margin:
            if not cached["refresh_token"]:
                raise OLXAuthError("Token wygasł a brak refresh_token — zaloguj ponownie.")
            fresh = self.refresh_access(cached["refresh_token"])
            return fresh["access_token"]
        return cached["access_token"]
