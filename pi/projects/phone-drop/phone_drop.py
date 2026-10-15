#!/usr/bin/env python3
"""Token-protected HTTP inbox for files sent from an iPhone share-sheet Shortcut.

POST /upload accepts multipart/form-data (every file part is saved) or a raw
body named by the X-Filename header or ``name`` query parameter. Files stream
to a hidden partial directory under a byte budget, then are hard-linked into
the inbox under a sanitized, never-overwriting name. GET /health is public.
"""

from __future__ import annotations

import argparse
import hmac
import io
import logging
import mimetypes
import os
import shutil
import stat
import sys
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import unquote

from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.serving import WSGIRequestHandler, make_server
from werkzeug.wrappers import Request, Response

LOG = logging.getLogger("phone_drop")

PARTIAL_DIR_NAME = ".incoming"
MAX_NAME_BYTES = 200
MAX_EXTENSION_BYTES = 16
MAX_FORM_PARTS = 256
MAX_FORM_FIELD_BYTES = 1 << 20
RAW_CHUNK_BYTES = 1 << 20
# A refused request's body is read and discarded up to this size so the client
# receives the refusal text instead of a connection reset mid-send.
DRAIN_LIMIT_BYTES = 32 << 20
# Seconds a connection may sit idle mid-request before it is dropped, so a phone
# that walks out of range cannot hold the upload lock indefinitely.
SOCKET_IDLE_TIMEOUT = 120
# Multipart framing (boundaries and part headers) is counted in Content-Length
# but not written to disk; allow for it when rejecting a request up front.
FRAMING_SLACK_BYTES = 1 << 20
MIN_TOKEN_CHARS = 32
# Characters macOS Finder or SMB clients reject or remap in file names.
FORBIDDEN_NAME_CHARS = frozenset('/\\:*?"<>|')


@dataclass(frozen=True)
class Settings:
    inbox: Path
    token: str
    quota_bytes: int
    min_free_bytes: int


class UploadRefused(RequestEntityTooLarge):
    """A file would exceed the inbox quota or the filesystem free-space floor."""

    def __init__(self, message: str) -> None:
        super().__init__(description=message)


class BudgetedFile(io.FileIO):
    """Partial upload file that refuses writes beyond the request's byte budget."""

    def __init__(self, path: Path, budget: "ByteBudget") -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        super().__init__(fd, "wb", closefd=True)
        self.path = path
        self._budget = budget

    def write(self, data) -> int:  # type: ignore[override]
        self._budget.consume(len(data))
        return super().write(data)


class ByteBudget:
    def __init__(self, remaining: int, refusal: str) -> None:
        self.remaining = remaining
        self._refusal = refusal

    def consume(self, count: int) -> None:
        if count > self.remaining:
            raise UploadRefused(self._refusal)
        self.remaining -= count


def format_bytes(count: int) -> str:
    value = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    raise AssertionError("unreachable")


def _truncate_utf8(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore")


def safe_filename(raw: Optional[str], content_type: Optional[str], now: float) -> str:
    """Reduce a client-supplied name to one visible path component."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFC", name)
    name = "".join(
        ch for ch in name if ch.isprintable() and ch not in FORBIDDEN_NAME_CHARS
    )
    # A leading dot would hide the file in Finder; "." and ".." vanish here too.
    name = name.strip().lstrip(".").strip()
    stem, extension = os.path.splitext(name)
    if len(extension.encode("utf-8")) > MAX_EXTENSION_BYTES:
        stem, extension = name, ""
    if not stem:
        mime = (content_type or "").split(";", 1)[0].strip().lower()
        guessed = mimetypes.guess_extension(mime) if mime else None
        stem = time.strftime("upload-%Y%m%d-%H%M%S", time.localtime(now))
        extension = extension or guessed or ""
    stem = _truncate_utf8(stem, MAX_NAME_BYTES - len(extension.encode("utf-8")))
    return stem + extension


def inbox_usage(inbox: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(inbox):
        for file_name in files:
            try:
                info = os.lstat(os.path.join(root, file_name))
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def place_without_overwrite(partial: Path, inbox: Path, name: str) -> Path:
    """Hard-link the finished partial under the first free name, then drop it."""
    stem, extension = os.path.splitext(name)
    for attempt in range(1, 1000):
        candidate = name if attempt == 1 else f"{stem} ({attempt}){extension}"
        destination = inbox / candidate
        try:
            os.link(partial, destination)
        except FileExistsError:
            continue
        os.unlink(partial)
        return destination
    raise RuntimeError(f"no free name for {name!r} in {inbox}")


class PhoneDrop:
    """WSGI application holding the inbox settings and the upload lock."""

    def __init__(self, settings: Settings, clock: Callable[[], float] = time.time):
        self.settings = settings
        self.partial_dir = settings.inbox / PARTIAL_DIR_NAME
        self._clock = clock
        # One upload at a time keeps the quota and free-space budget exact.
        self._upload_lock = threading.Lock()

    def __call__(self, environ, start_response):
        request = Request(environ)
        try:
            response = self._dispatch(request)
        except HTTPException as error:
            _discard_body(request)
            response = _text(str(error.description), error.code or 500)
        except (ConnectionError, TimeoutError):
            raise  # the client is gone; let the server drop the connection
        except Exception:
            LOG.exception("upload failed")
            response = _text("Upload failed on the Pi; see journalctl -u phone-drop.", 500)
        return response(environ, start_response)

    def _dispatch(self, request: Request) -> Response:
        if request.path == "/health" and request.method in ("GET", "HEAD"):
            return Response('{"ok": true}\n', mimetype="application/json")
        if request.path != "/upload":
            return _text("Not found.", 404)
        if request.method != "POST":
            return _text("Use POST.", 405)
        if not self._authorized(request):
            _discard_body(request)
            return _text("Upload refused: missing or wrong token.", 401)
        with self._upload_lock:
            return self._receive(request)

    def _authorized(self, request: Request) -> bool:
        header = request.headers.get("Authorization", "")
        scheme, _, supplied = header.partition(" ")
        if scheme.lower() != "bearer" or not supplied:
            return False
        return hmac.compare_digest(
            supplied.strip().encode("utf-8"), self.settings.token.encode("utf-8")
        )

    def _budget(self, content_length: Optional[int]) -> ByteBudget:
        settings = self.settings
        used = inbox_usage(settings.inbox)
        free = shutil.disk_usage(settings.inbox).free
        quota_left = settings.quota_bytes - used
        floor_left = free - settings.min_free_bytes
        if quota_left <= floor_left:
            refusal = (
                f"Upload refused: the Pi inbox is full ({format_bytes(used)} used of "
                f"{format_bytes(settings.quota_bytes)}). Move files out of "
                f"{settings.inbox} and try again."
            )
        else:
            refusal = (
                f"Upload refused: the Pi needs {format_bytes(settings.min_free_bytes)} "
                f"free and has {format_bytes(free)}."
            )
        remaining = max(0, min(quota_left, floor_left))
        if content_length is not None and content_length > remaining + FRAMING_SLACK_BYTES:
            raise UploadRefused(refusal)
        return ByteBudget(remaining, refusal)

    def _receive(self, request: Request) -> Response:
        budget = self._budget(request.content_length)
        self.partial_dir.mkdir(mode=0o700, exist_ok=True)
        partials: List[BudgetedFile] = []

        def open_partial(*_args, **_kwargs) -> BudgetedFile:
            handle = BudgetedFile(self.partial_dir / f"{uuid.uuid4().hex}.part", budget)
            partials.append(handle)
            return handle

        try:
            if request.mimetype == "multipart/form-data":
                uploads = self._multipart_uploads(request, open_partial)
            else:
                uploads = self._raw_upload(request, open_partial)
            if not uploads:
                return _text("Upload refused: the request contained no file.", 400)
            saved = []
            for handle, raw_name, content_type in uploads:
                handle.flush()
                os.fsync(handle.fileno())
                size = os.fstat(handle.fileno()).st_size
                handle.close()
                name = safe_filename(raw_name, content_type, self._clock())
                destination = place_without_overwrite(handle.path, self.settings.inbox, name)
                saved.append((destination.name, size))
                LOG.info("saved %s (%d bytes)", destination.name, size)
            _fsync_directory(self.settings.inbox)
        finally:
            for handle in partials:
                handle.close()
                try:
                    handle.path.unlink()
                except FileNotFoundError:
                    pass
        return _text(_summary(saved), 200)

    def _multipart_uploads(self, request: Request, open_partial):
        # Werkzeug calls this factory once per file part as the part begins.
        request._get_file_stream = open_partial  # type: ignore[method-assign]
        request.max_form_memory_size = MAX_FORM_FIELD_BYTES
        request.max_form_parts = MAX_FORM_PARTS
        uploads = []
        for _field, storage in request.files.items(multi=True):
            handle = storage.stream
            if not isinstance(handle, BudgetedFile):
                continue
            if os.fstat(handle.fileno()).st_size == 0 and not storage.filename:
                continue  # an empty file field: nothing was chosen
            uploads.append((handle, storage.filename, storage.content_type))
        return uploads

    def _raw_upload(self, request: Request, open_partial):
        raw_name = request.headers.get("X-Filename") or request.args.get("name")
        handle = open_partial()
        stream = request.stream
        while True:
            chunk = stream.read(RAW_CHUNK_BYTES)
            if not chunk:
                break
            handle.write(chunk)
        if handle.tell() == 0:
            return []
        return [(handle, unquote(raw_name) if raw_name else None, request.mimetype)]


def _discard_body(request: Request) -> None:
    length = request.content_length
    if length is None or length > DRAIN_LIMIT_BYTES:
        return
    try:
        stream = request.stream
        while stream.read(RAW_CHUNK_BYTES):
            pass
    except (HTTPException, OSError):
        pass


class IdleTimeoutHandler(WSGIRequestHandler):
    timeout = SOCKET_IDLE_TIMEOUT

    def log_request(self, code="-", size="-") -> None:
        # Werkzeug's default adds terminal colour codes, which clutter the journal.
        self.log("info", '"%s" %s %s', self.requestline, getattr(code, "value", code), size)


def _text(message: str, status: int) -> Response:
    return Response(message + "\n", status=status, mimetype="text/plain")


def _summary(saved) -> str:
    if len(saved) == 1:
        name, size = saved[0]
        return f"Saved {name} ({format_bytes(size)})"
    total = sum(size for _name, size in saved)
    names = ", ".join(name for name, _size in saved)
    return f"Saved {len(saved)} files ({format_bytes(total)}): {names}"


def load_token(path: Path) -> str:
    """Read the shared token, refusing a file other users could read."""
    info = path.stat()
    if info.st_mode & 0o077:
        raise SystemExit(f"token file {path} must not be group/other accessible")
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < MIN_TOKEN_CHARS:
        raise SystemExit(f"token in {path} is shorter than {MIN_TOKEN_CHARS} characters")
    return token


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8793)
    parser.add_argument("--inbox", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--quota-mib", type=int, default=8192)
    parser.add_argument("--min-free-mib", type=int, default=8192)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if not args.inbox.is_dir():
        raise SystemExit(f"inbox {args.inbox} is not a directory")
    settings = Settings(
        inbox=args.inbox,
        token=load_token(args.token_file),
        quota_bytes=args.quota_mib << 20,
        min_free_bytes=args.min_free_mib << 20,
    )
    server = make_server(
        args.bind,
        args.port,
        PhoneDrop(settings),
        threaded=True,
        request_handler=IdleTimeoutHandler,
    )
    LOG.info("serving %s on %s:%d", settings.inbox, args.bind, server.server_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
