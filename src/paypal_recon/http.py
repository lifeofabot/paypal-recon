"""The only network code. Sandbox host, two allowed calls, redacted errors."""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from paypal_recon.config import Secret

SANDBOX_HOST = "api-m.sandbox.paypal.com"
TOKEN_PATH = "/v1/oauth2/token"
SEARCH_PATH = "/v1/reporting/transactions"
ALLOWED: frozenset[tuple[str, str]] = frozenset({("POST", TOKEN_PATH), ("GET", SEARCH_PATH)})

_SAFE = re.compile(r"[^A-Za-z0-9_.\-]")


class PayPalError(Exception):
    """Carries only method, path, status, and PayPal's `name` / `debug_id`."""

    def __init__(self, method: str, path: str, status: int, name: str = "", debug_id: str = ""):
        self.method, self.path, self.status = method, path, status
        self.name, self.debug_id = name, debug_id
        detail = " ".join(p for p in (name, f"debug_id={debug_id}" if debug_id else "") if p)
        super().__init__(f"{method} {path} -> {status}" + (f" {detail}" if detail else ""))


class BlockedRequestError(PayPalError):
    """Raised before any socket opens when a request is outside the allowlist."""

    def __init__(self, method: str, path: str, why: str):
        Exception.__init__(self, f"blocked {method} {path}: {why}")
        self.method, self.path, self.status, self.name, self.debug_id = method, path, 0, "", ""


Opener = Callable[..., Any]


def _clean(value: object) -> str:
    return _SAFE.sub("", str(value))[:64] if isinstance(value, str) else ""


class Transport:
    def __init__(self, opener: Opener | None = None, timeout: float = 30.0) -> None:
        self._opener = opener or urllib.request.urlopen
        self._timeout = timeout
        self.calls: list[tuple[str, str, int]] = []

    @staticmethod
    def check(method: str, url: str) -> tuple[str, str]:
        parts = urllib.parse.urlsplit(url)
        path = parts.path
        if parts.scheme != "https" or parts.hostname != SANDBOX_HOST or parts.port is not None:
            raise BlockedRequestError(method, path, "host is not the PayPal sandbox")
        if (method, path) not in ALLOWED:
            raise BlockedRequestError(method, path, "not on the read-only allowlist")
        return method, path

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> dict[str, Any]:
        url = f"https://{SANDBOX_HOST}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        self.check(method, url)
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        try:
            with self._opener(req, timeout=self._timeout) as resp:
                status = int(getattr(resp, "status", 200))
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            name = debug_id = ""
            try:
                data = json.loads(exc.read())
                name, debug_id = _clean(data.get("name")), _clean(data.get("debug_id"))
            except (ValueError, AttributeError, OSError):
                pass
            self.calls.append((method, path, exc.code))
            raise PayPalError(method, path, exc.code, name, debug_id) from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            self.calls.append((method, path, 0))
            raise PayPalError(method, path, 0, name=type(exc).__name__) from None
        self.calls.append((method, path, status))
        try:
            return json.loads(raw)
        except ValueError:
            raise PayPalError(method, path, status, name="INVALID_JSON") from None

    def get_token(self, client_id: Secret, client_secret: Secret) -> Secret:
        pair = f"{client_id.reveal()}:{client_secret.reveal()}".encode()
        data = self.request(
            "POST",
            TOKEN_PATH,
            headers={
                "Authorization": "Basic " + base64.b64encode(pair).decode(),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            body=b"grant_type=client_credentials",
        )
        token = data.get("access_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise PayPalError("POST", TOKEN_PATH, 200, name="NO_ACCESS_TOKEN")
        return Secret(token)

    def search(self, token: Secret, query: dict[str, str]) -> dict[str, Any]:
        return self.request(
            "GET",
            SEARCH_PATH,
            query=query,
            headers={"Authorization": "Bearer " + token.reveal(), "Accept": "application/json"},
        )
