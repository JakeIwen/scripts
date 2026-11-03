"""Mac-side JSON client for the Pi van dashboard's HTTP API (Python 3.9+, stdlib)."""
from __future__ import annotations

import json
from urllib import request

DASHBOARD_URL = "http://vanpi.lan:8788"


class DashboardError(RuntimeError):
    """The dashboard returned data that cannot confirm a completed operation."""


def dashboard_json(url: str, *, data: bytes | None = None, timeout: float) -> dict:
    """GET (or form-POST when data is given) and return a payload whose ok is true."""
    headers = {"Content-Type": "application/x-www-form-urlencoded"} if data is not None else {}
    operation = request.Request(url, data=data, headers=headers)
    with request.urlopen(operation, timeout=timeout) as response:
        if response.status != 200:
            raise DashboardError(f"HTTP {response.status}")
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        detail = payload.get("error", "invalid response") if isinstance(payload, dict) else "invalid response"
        raise DashboardError(str(detail))
    return payload
