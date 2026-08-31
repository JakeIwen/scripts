#!/usr/bin/env python3
"""Serve the built React dashboard preview and proxy its API to the live backend.

This process deliberately owns no dashboard state and imports none of the
dashboard controllers.  The existing service on port 8788 remains the only
process allowed to control van hardware and services.

The preview starts read-only.  GET and HEAD requests under ``/api`` are
proxied, while every mutating endpoint must be added explicitly to
``ALLOWED_MUTATIONS`` after its React control has been reviewed and tested.
"""

from __future__ import annotations

import argparse
import http.client
import json
import mimetypes
import os
import posixpath
import sys
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit


DEFAULT_LISTEN_HOST = "0.0.0.0"
DEFAULT_PREVIEW_PORT = 8790
DEFAULT_BACKEND_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 8788
DEFAULT_BACKEND_TIMEOUT = 45.0
DEFAULT_BUILD_ROOT = Path(
    "/home/pi/scripts/van-dashboard-preview/current"
)

SAFE_API_METHODS = frozenset({"GET", "HEAD"})
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Keep this list exact and easy to audit.  Do not add a wildcard or enable a
# whole HTTP method. Each entry is reviewed against the production React
# control's confirmation, single-flight, reconciliation, and failure behavior.
ALLOWED_MUTATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        # Reversible media and lighting controls. Each corresponding React
        # feature is single-flight and performs an authoritative refresh after
        # both successful and ambiguous outcomes.
        ("POST", "/api/speakers/select"),
        ("POST", "/api/speakers/group"),
        ("POST", "/api/speakers/volume"),
        ("POST", "/api/speakers/mute"),
        ("POST", "/api/speakers/group-volume"),
        ("POST", "/api/speakers/group-mute"),
        ("POST", "/api/speakers/transport"),
        ("POST", "/api/lights/power"),
        ("POST", "/api/lights/brightness"),
        ("POST", "/api/lights/hue"),
        ("POST", "/api/lights/color-temperature"),
        # Bounded routine controls. These never retry their mutation and
        # reconcile with the authoritative GET state after every outcome.
        ("POST", "/api/telemetry-service"),
        ("POST", "/api/telemetry-voltage-check"),
        ("POST", "/api/speedtest"),
        ("POST", "/api/price-checks/add"),
        ("POST", "/api/price-checks/edit"),
        ("POST", "/api/price-checks/remove"),
        ("POST", "/api/price-checks/mute"),
        ("POST", "/api/price-checks/check"),
        ("POST", "/api/price-checks/schedule"),
        ("POST", "/api/price-checks/schedule/parse"),
        ("POST", "/api/price-checks/searches/add"),
        ("POST", "/api/price-checks/searches/remove"),
        ("POST", "/api/price-checks/searches/dismiss"),
        ("POST", "/api/price-checks/searches/check"),
        ("POST", "/api/ignition-monitor/disable"),
        ("POST", "/api/ignition-monitor/enable"),
        # Guarded storage, USB, and backup operations. The UI keeps accepted
        # operations pending until their matching GET state observes them.
        ("POST", "/api/storage-policy"),
        ("POST", "/api/disks/action"),
        ("POST", "/api/usb-ports/discover"),
        ("POST", "/api/usb-ports/action"),
        ("POST", "/api/usb-ports/recover"),
        ("POST", "/api/backups/clone"),
        ("POST", "/api/backups/borg"),
        ("POST", "/api/backups/exfat"),
        ("POST", "/api/backups/borg/stop"),
        ("POST", "/api/backups/exfat/stop"),
        # Network-changing UBNT operations retain credential scrubbing and
        # converge through the controller's authoritative operation status.
        ("POST", "/api/ubnt-wifi/scan"),
        ("POST", "/api/ubnt-wifi/connect"),
        ("POST", "/api/ubnt-wifi/provision"),
        ("POST", "/api/ubnt-wifi/resume"),
        ("POST", "/api/ubnt-wifi/profile"),
        # COP ALERT publishes requested intent only. The independent guarded
        # supervisor remains the sole CAN-wake authority.
        ("POST", "/api/cop-alert"),
        ("POST", "/api/starlink"),
        ("POST", "/api/dashboard-service/restart"),
        ("POST", "/api/system-power"),
        ("POST", "/api/system-monitor/crash-analysis"),
        # vOnStar accepts only the fixed high-level action catalog and the
        # separately confirmed, bodyless point-in-time access-state request.
        ("POST", "/api/vonstar"),
        ("POST", "/api/vonstar/access-state"),
    }
)

MAX_REQUEST_BODY_BYTES = 1024 * 1024
COPY_BUFFER_BYTES = 64 * 1024

# RFC 7230 section 6.1: these headers describe one transport connection and
# must not be relayed to the other connection.
HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


@dataclass(frozen=True)
class PreviewConfig:
    """Runtime settings kept together so tests can use an isolated server."""

    build_root: Path
    backend_host: str = DEFAULT_BACKEND_HOST
    backend_port: int = DEFAULT_BACKEND_PORT
    backend_timeout: float = DEFAULT_BACKEND_TIMEOUT
    allowed_mutations: frozenset[tuple[str, str]] = field(
        default_factory=lambda: ALLOWED_MUTATIONS
    )
    log_requests: bool = True

    def __post_init__(self) -> None:
        normalized = frozenset(
            (method.upper(), path)
            for method, path in self.allowed_mutations
        )
        for method, path in normalized:
            if method not in MUTATING_METHODS:
                raise ValueError(f"mutation allowlist contains unsafe method: {method}")
            if not is_api_path(path) or "?" in path or "#" in path:
                raise ValueError(f"mutation allowlist contains invalid API path: {path}")
        object.__setattr__(self, "allowed_mutations", normalized)
        object.__setattr__(self, "build_root", self.build_root.resolve())


class PreviewHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server carrying immutable preview configuration."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], config: PreviewConfig):
        self.preview_config = config
        super().__init__(address, PreviewRequestHandler)


class PreviewRequestHandler(BaseHTTPRequestHandler):
    """Small static-file server and narrowly scoped reverse proxy."""

    protocol_version = "HTTP/1.1"
    server_version = "VanDashboardPreview/1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle_request()

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle_request()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle_request()

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle_request()

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle_request()

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle_request()

    def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        # The preview intentionally grants no cross-origin access.  Same-origin
        # browser requests do not need a preflight.
        self._send_json_error(
            405,
            "cross-origin and unlisted preview requests are not allowed",
            extra_headers=(
                ("Allow", self._allowed_methods_header()),
            ),
            close_connection=True,
        )

    @property
    def config(self) -> PreviewConfig:
        return self.server.preview_config  # type: ignore[attr-defined]

    def log_message(self, format_string: str, *args: object) -> None:
        if self.config.log_requests:
            super().log_message(format_string, *args)

    def _handle_request(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path

        if is_api_path(path):
            self._handle_api_request(parsed)
            return

        if self.command not in SAFE_API_METHODS:
            self._send_json_error(
                405,
                "preview files are read-only",
                close_connection=True,
            )
            return

        if path == "/healthz":
            self._send_json(
                200,
                {"ok": True, "service": "van-dashboard-preview"},
                send_body=self.command != "HEAD",
            )
            return

        self._serve_static(path, send_body=self.command != "HEAD")

    def _handle_api_request(self, parsed) -> None:
        method = self.command.upper()
        path = parsed.path

        if method not in SAFE_API_METHODS and (
            method,
            path,
        ) not in self.config.allowed_mutations:
            self._send_json_error(
                405,
                f"{method} {path} is not enabled in the preview",
                extra_headers=(("Allow", self._allowed_methods_header(path)),),
                close_connection=True,
            )
            return

        if (
            method in MUTATING_METHODS
            and self.headers.get("X-Van-Dashboard") != "1"
        ):
            self._send_json_error(
                403,
                "dashboard control header missing",
                close_connection=True,
            )
            return

        try:
            body = self._read_request_body()
        except RequestBodyError as exc:
            self._send_json_error(
                exc.status,
                str(exc),
                close_connection=True,
            )
            return

        target = parsed.path
        if parsed.query:
            target = f"{target}?{parsed.query}"

        request_headers = self._upstream_request_headers(body)
        connection = http.client.HTTPConnection(
            self.config.backend_host,
            self.config.backend_port,
            timeout=self.config.backend_timeout,
        )
        try:
            connection.request(method, target, body=body, headers=request_headers)
            response = connection.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            self._send_json_error(502, f"dashboard backend unavailable: {exc}")
        else:
            try:
                self._relay_upstream_response(response, send_body=method != "HEAD")
            except (OSError, http.client.HTTPException):
                # Response headers may already be on the wire, so a second HTTP
                # status would be invalid.  Closing makes a truncated upstream
                # response unambiguous to the browser.
                self.close_connection = True
        finally:
            connection.close()

    def _read_request_body(self) -> bytes | None:
        transfer_encoding = self.headers.get("Transfer-Encoding")
        if transfer_encoding:
            raise RequestBodyError(400, "chunked preview requests are not supported")

        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return None
        try:
            length = int(raw_length, 10)
        except ValueError as exc:
            raise RequestBodyError(400, "invalid Content-Length") from exc
        if length < 0:
            raise RequestBodyError(400, "invalid Content-Length")
        if length > MAX_REQUEST_BODY_BYTES:
            raise RequestBodyError(413, "preview request body is too large")
        body = self.rfile.read(length)
        if len(body) != length:
            raise RequestBodyError(400, "incomplete preview request body")
        return body

    def _upstream_request_headers(self, body: bytes | None) -> dict[str, str]:
        blocked_headers = set(HOP_BY_HOP_HEADERS)
        for token in self.headers.get("Connection", "").split(","):
            token = token.strip().lower()
            if token:
                blocked_headers.add(token)
        blocked_headers.add("content-length")

        headers = {
            name: value
            for name, value in self.headers.items()
            if name.lower() not in blocked_headers
        }

        # Passing Host through unchanged is essential.  The dashboard's CSRF
        # guard compares Origin.netloc with Flask's request.host; replacing Host
        # with 127.0.0.1:8788 would reject legitimate preview controls.
        incoming_host = self.headers.get("Host")
        if incoming_host:
            headers["Host"] = incoming_host
        if body is not None:
            headers["Content-Length"] = str(len(body))
        headers["Connection"] = "close"
        return headers

    def _relay_upstream_response(
        self,
        response: http.client.HTTPResponse,
        *,
        send_body: bool,
    ) -> None:
        blocked_headers = set(HOP_BY_HOP_HEADERS)
        for name, value in response.getheaders():
            if name.lower() == "connection":
                blocked_headers.update(
                    token.strip().lower()
                    for token in value.split(",")
                    if token.strip()
                )

        response_headers = [
            (name, value)
            for name, value in response.getheaders()
            if name.lower() not in blocked_headers
            and name.lower() not in {"date", "server"}
        ]
        has_content_length = any(
            name.lower() == "content-length" for name, _ in response_headers
        )

        self.send_response(response.status, response.reason)
        for name, value in response_headers:
            self.send_header(name, value)
        if not has_content_length:
            # http.client removes chunk framing.  Closing our response is the
            # simplest correct delimiter when the backend did not send a size.
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()

        if not send_body:
            return
        try:
            while True:
                block = response.read(COPY_BUFFER_BYTES)
                if not block:
                    break
                self.wfile.write(block)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _serve_static(self, request_path: str, *, send_body: bool) -> None:
        try:
            relative = safe_relative_path(request_path)
        except ValueError:
            self._send_json_error(404, "preview file not found")
            return

        requested = self.config.build_root / relative
        if is_file_within(self.config.build_root, requested):
            self._send_file(requested.resolve(), request_path, send_body=send_body)
            return

        # Missing hashed assets should be a real 404, not index.html delivered
        # with a JavaScript MIME type.  Other paths are client-side routes.
        if request_path == "/assets" or request_path.startswith("/assets/"):
            self._send_json_error(404, "preview asset not found")
            return

        index = self.config.build_root / "index.html"
        if not is_file_within(self.config.build_root, index):
            self._send_json_error(503, "preview build is not installed")
            return
        self._send_file(index.resolve(), "/index.html", send_body=send_body)

    def _send_file(self, path: Path, request_path: str, *, send_body: bool) -> None:
        content_type, content_encoding = mimetypes.guess_type(path.name)
        if path.suffix == ".webmanifest":
            content_type = "application/manifest+json"
        elif path.suffix in {".js", ".mjs"}:
            content_type = "text/javascript"
        if content_type is None:
            content_type = "application/octet-stream"

        try:
            stat_result = path.stat()
            handle = path.open("rb")
        except OSError:
            self._send_json_error(404, "preview file not found")
            return

        with handle:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            if content_encoding:
                self.send_header("Content-Encoding", content_encoding)
            self.send_header("Content-Length", str(stat_result.st_size))
            if request_path.startswith("/assets/"):
                self.send_header(
                    "Cache-Control", "public, max-age=31536000, immutable"
                )
            elif path.name == "index.html":
                self.send_header("Cache-Control", "no-store")
            else:
                self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            if not send_body:
                return
            try:
                while True:
                    block = handle.read(COPY_BUFFER_BYTES)
                    if not block:
                        break
                    self.wfile.write(block)
            except (BrokenPipeError, ConnectionResetError):
                return

    def _send_json(
        self,
        status: int,
        payload: dict[str, object],
        *,
        send_body: bool = True,
        extra_headers: Iterable[tuple[str, str]] = (),
    ) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def _send_json_error(
        self,
        status: int,
        message: str,
        *,
        extra_headers: Iterable[tuple[str, str]] = (),
        close_connection: bool = False,
    ) -> None:
        if close_connection:
            self.close_connection = True
            extra_headers = (*extra_headers, ("Connection", "close"))
        self._send_json(
            status,
            {"ok": False, "message": message},
            send_body=self.command != "HEAD",
            extra_headers=extra_headers,
        )

    def _allowed_methods_header(self, path: str | None = None) -> str:
        methods = set(SAFE_API_METHODS)
        if path is not None:
            methods.update(
                method
                for method, allowed_path in self.config.allowed_mutations
                if allowed_path == path
            )
        return ", ".join(sorted(methods))


class RequestBodyError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def is_api_path(path: str) -> bool:
    """Return true only for the exact API root or a child of it."""

    return path == "/api" or path.startswith("/api/")


def safe_relative_path(request_path: str) -> Path:
    """Convert a URL path to a normalized relative filesystem path."""

    try:
        decoded = unquote(request_path, errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid URL encoding") from exc
    if "\x00" in decoded:
        raise ValueError("NUL byte in path")

    # posixpath normalization is deliberate: URL paths always use '/', even
    # when tests or development happen on a different host platform.
    normalized = posixpath.normpath(decoded)
    if normalized in {".", "/"}:
        return Path("index.html")
    relative = normalized.lstrip("/")
    if relative == ".." or relative.startswith("../"):
        raise ValueError("path escapes build root")
    return Path(relative)


def is_file_within(root: Path, candidate: Path) -> bool:
    """Reject missing files and symlinks that escape the build root."""

    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, ValueError):
        return False
    return resolved.is_file()


def create_server(
    address: tuple[str, int],
    config: PreviewConfig,
) -> PreviewHTTPServer:
    """Construct a server without starting it, primarily for focused tests."""

    return PreviewHTTPServer(address, config)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(
            os.environ.get("VAN_DASHBOARD_PREVIEW_ROOT", str(DEFAULT_BUILD_ROOT))
        ),
        help="directory containing the built index.html and assets/",
    )
    parser.add_argument("--listen", default=DEFAULT_LISTEN_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PREVIEW_PORT)
    parser.add_argument("--backend-host", default=DEFAULT_BACKEND_HOST)
    parser.add_argument("--backend-port", type=int, default=DEFAULT_BACKEND_PORT)
    parser.add_argument(
        "--backend-timeout",
        type=float,
        default=DEFAULT_BACKEND_TIMEOUT,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.expanduser().resolve()
    if not (root / "index.html").is_file():
        print(f"preview build is missing index.html: {root}", file=sys.stderr)
        return 1

    config = PreviewConfig(
        build_root=root,
        backend_host=args.backend_host,
        backend_port=args.backend_port,
        backend_timeout=args.backend_timeout,
    )
    server = create_server((args.listen, args.port), config)
    print(
        f"serving dashboard preview on {args.listen}:{server.server_port}; "
        f"proxying read-only API traffic to "
        f"{config.backend_host}:{config.backend_port}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
