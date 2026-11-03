"""Mac-side JSON client for the Pi van dashboard's HTTP API (Python 3.9+, stdlib)."""
from __future__ import annotations

import json
from urllib import error, request

DASHBOARD_URL = "http://vanpi.lan:8788"


class DashboardError(RuntimeError):
    """The dashboard returned data that cannot confirm a completed operation."""


def _refusal(payload) -> str | None:
    # The dashboard's api_error replies {"ok": false, "message": ...}.
    return payload.get("message") if isinstance(payload, dict) else None


def dashboard_json(url: str, *, data: bytes | None = None, timeout: float) -> dict:
    """GET (or form-POST when data is given) and return a payload whose ok is true."""
    headers = {"Content-Type": "application/x-www-form-urlencoded"} if data is not None else {}
    operation = request.Request(url, data=data, headers=headers)
    try:
        with request.urlopen(operation, timeout=timeout) as response:
            if response.status != 200:
                raise DashboardError(f"HTTP {response.status}")
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as failure:
        try:
            refusal = _refusal(json.loads(failure.read().decode("utf-8")))
        except ValueError:
            refusal = None
        raise DashboardError(refusal or f"HTTP {failure.code}") from failure
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise DashboardError(_refusal(payload) or "invalid response")
    return payload
