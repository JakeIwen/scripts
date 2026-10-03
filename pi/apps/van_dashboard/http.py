"""Shared HTTP helpers for van dashboard routes."""

from urllib.parse import urlsplit

from flask import jsonify, request
from werkzeug.local import LocalProxy


def api_error(message, status):
    return jsonify({"ok": False, "message": str(message)}), status


def request_boolean(name):
    raw = request.values.get(name, "").strip().lower()
    if raw not in ("1", "0", "true", "false", "on", "off"):
        raise ValueError(f"{name} must be true or false")
    return raw in ("1", "true", "on")


def reject_cross_origin_mutations():
    """Block browser CSRF against dashboard mutation endpoints.

    Command-line clients without browser Origin/Referer headers remain usable.
    The custom header also forces a cross-origin fetch to preflight, and this
    server intentionally grants no cross-origin access.
    """
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    if origin:
        if urlsplit(origin).netloc != request.host:
            return api_error("cross-origin control request rejected", 403)
        if request.headers.get("X-Van-Dashboard") != "1":
            return api_error("dashboard control header missing", 403)
    elif referer and urlsplit(referer).netloc != request.host:
        return api_error("cross-origin control request rejected", 403)
    return None


def _exact_form(fields):
    return set(request.form) == set(fields) and all(
        len(request.form.getlist(name)) == 1 for name in fields
    )


def runtime_proxy(name):
    def lookup():
        from . import runtime

        return getattr(runtime, name)

    return LocalProxy(lookup)
