#!/usr/bin/env python3
"""Read-only qBittorrent Web API identity resolution for video playback.

The qBittorrent torrent ID plus its file index is stable while a download moves
from qBittorrent's temporary directory to its final save directory.  This module
turns that pair into a durable, client-scoped identity without treating a
filename as the identity.

Only read-only Web API calls are exposed.  The sole POST is qBittorrent's
cookie-based login endpoint; no torrent or application setting is changed.
The response fields used here are present in qBittorrent 4.5.2 / Web API 2.8.x.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import ipaddress
import json
import math
import os
import re
import socket
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    Request,
    build_opener,
)


DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_TIMEOUT = 3.0
MAX_RESPONSE_BYTES = 32 * 1024 * 1024

_TORRENT_ID_RE = re.compile(r"[0-9a-fA-F]{40}\Z")
_INFOHASH_V2_RE = re.compile(r"[0-9a-fA-F]{64}\Z")
_CLIENT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_FILE_INDEX_RE = re.compile(r"(?:0|[1-9][0-9]{0,9})\Z")
_HOOK_PREFIX = "qbt1"


class QbittorrentError(RuntimeError):
    """Base class for deterministic qBittorrent integration failures."""


class QbittorrentConfigurationError(QbittorrentError):
    """The local client or path-root configuration is unsafe or invalid."""


class QbittorrentUnavailable(QbittorrentError):
    """The Web API could not be reached within the configured timeout."""


class QbittorrentAuthenticationError(QbittorrentError):
    """The Web API requires credentials or rejected the supplied credentials."""


class QbittorrentProtocolError(QbittorrentError):
    """qBittorrent returned an unexpected status or response shape."""


class QbittorrentNotFound(QbittorrentError):
    """A previously known torrent or torrent file no longer exists."""


class QbittorrentMetadataUnavailable(QbittorrentError):
    """A magnet does not yet have the file metadata needed for resolution."""


class QbittorrentUnsafePath(QbittorrentError):
    """A playback path is not a safe descendant of a configured media root."""


class QbittorrentAmbiguousPath(QbittorrentError):
    """More than one qBittorrent file claims the same playback path."""


class InvalidHookIdentifier(QbittorrentError):
    """A completion-hook identifier failed strict syntax validation."""


@dataclass(frozen=True)
class QbittorrentCredentials:
    """Cookie-login credentials whose password is omitted from representations."""

    username: str
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.username, str) or not self.username or "\x00" in self.username:
            raise QbittorrentConfigurationError("qBittorrent username is invalid")
        if not isinstance(self.password, str) or not self.password or "\x00" in self.password:
            raise QbittorrentConfigurationError("qBittorrent password is invalid")


@dataclass(frozen=True)
class HttpResponse:
    """Small transport-neutral HTTP response used by the client and its tests."""

    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        """Perform one bounded HTTP request."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class UrllibTransport:
    """stdlib transport with an in-memory SID cookie jar and no redirects."""

    def __init__(self, *, max_response_bytes: int = MAX_RESPONSE_BYTES) -> None:
        if isinstance(max_response_bytes, bool) or int(max_response_bytes) <= 0:
            raise QbittorrentConfigurationError("HTTP response limit must be positive")
        self.max_response_bytes = int(max_response_bytes)
        self._opener = build_opener(
            HTTPCookieProcessor(http.cookiejar.CookieJar()),
            _NoRedirect(),
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        request = Request(url, data=body, headers=dict(headers), method=method)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                payload = response.read(self.max_response_bytes + 1)
                if len(payload) > self.max_response_bytes:
                    raise QbittorrentProtocolError("qBittorrent response is too large")
                return HttpResponse(
                    int(response.status),
                    payload,
                    dict(response.headers.items()),
                )
        except HTTPError as exc:
            payload = exc.read(self.max_response_bytes + 1)
            if len(payload) > self.max_response_bytes:
                raise QbittorrentProtocolError("qBittorrent response is too large") from None
            return HttpResponse(int(exc.code), payload, dict(exc.headers.items()))
        except (URLError, TimeoutError, socket.timeout, OSError):
            raise QbittorrentUnavailable("qBittorrent Web API is unavailable") from None


def validate_client_id(value: str) -> str:
    if not isinstance(value, str) or not _CLIENT_ID_RE.fullmatch(value):
        raise InvalidHookIdentifier("invalid qBittorrent client identifier")
    return value


def validate_torrent_id(value: str) -> str:
    """Validate a qBittorrent TorrentID as accepted from a completion hook."""

    if not isinstance(value, str) or not _TORRENT_ID_RE.fullmatch(value):
        raise InvalidHookIdentifier("invalid qBittorrent torrent identifier")
    return value.casefold()


def validate_file_index(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2_147_483_647:
        raise InvalidHookIdentifier("invalid qBittorrent file index")
    return value


@dataclass(frozen=True, order=True)
class TorrentFileIdentity:
    """Stable identity: qB client instance, TorrentID, and torrent file index."""

    client_id: str
    torrent_id: str
    file_index: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "client_id", validate_client_id(self.client_id))
        object.__setattr__(self, "torrent_id", validate_torrent_id(self.torrent_id))
        object.__setattr__(self, "file_index", validate_file_index(self.file_index))

    @property
    def hook_id(self) -> str:
        """Shell-safe, versioned identifier suitable for a trusted local hook."""

        return f"{_HOOK_PREFIX}:{self.client_id}:{self.torrent_id}:{self.file_index}"

    @classmethod
    def from_hook_id(
        cls,
        value: str,
        *,
        expected_client_id: str | None = None,
    ) -> "TorrentFileIdentity":
        if not isinstance(value, str):
            raise InvalidHookIdentifier("invalid qBittorrent hook identifier")
        parts = value.split(":")
        if len(parts) != 4 or parts[0] != _HOOK_PREFIX:
            raise InvalidHookIdentifier("invalid qBittorrent hook identifier")
        client_id, torrent_id, file_index_text = parts[1:]
        if not _FILE_INDEX_RE.fullmatch(file_index_text):
            raise InvalidHookIdentifier("invalid qBittorrent hook file index")
        identity = cls(client_id, torrent_id, int(file_index_text))
        if expected_client_id is not None and identity.client_id != validate_client_id(expected_client_id):
            raise InvalidHookIdentifier("qBittorrent hook belongs to another client")
        return identity


@dataclass(frozen=True)
class TorrentSummary:
    torrent_id: str
    infohash_v1: str
    infohash_v2: str
    name: str
    state: str
    progress: float
    expected_size: int
    save_path: str
    download_path: str
    content_path: str


@dataclass(frozen=True)
class TorrentFile:
    index: int
    relative_path: str
    expected_size: int
    progress: float
    piece_range: tuple[int, int]
    priority: int
    availability: float | None


@dataclass(frozen=True)
class ResolvedTorrentFile:
    """A catalog-ready qB file snapshot, optionally matched to one live path."""

    identity: TorrentFileIdentity
    torrent_name: str
    torrent_state: str
    infohash_v1: str
    infohash_v2: str
    relative_path: str
    expected_size: int
    progress: float
    piece_range: tuple[int, int]
    priority: int
    availability: float | None
    save_path: str
    download_path: str
    content_path: str
    temporary_paths: tuple[str, ...]
    final_paths: tuple[str, ...]
    matched_path: str | None = None
    location_kind: str | None = None

    @property
    def complete(self) -> bool:
        return self.progress >= 1.0


CredentialsProvider = Callable[[], QbittorrentCredentials]


class QbittorrentClient:
    """Read-only qBittorrent 4.5 Web API client and path resolver."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        temp_roots: Iterable[str | os.PathLike[str]],
        final_roots: Iterable[str | os.PathLike[str]],
        client_id: str | None = None,
        credentials: QbittorrentCredentials | CredentialsProvider | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: HttpTransport | None = None,
    ) -> None:
        self.base_url = _validate_local_base_url(base_url)
        if client_id is None:
            digest = hashlib.sha256(self.base_url.encode("utf-8")).hexdigest()[:16]
            client_id = f"local-{digest}"
        try:
            self.client_id = validate_client_id(client_id)
        except InvalidHookIdentifier as exc:
            raise QbittorrentConfigurationError(str(exc)) from None

        if not math.isfinite(timeout) or timeout <= 0:
            raise QbittorrentConfigurationError("qBittorrent timeout must be positive")
        self.timeout = float(timeout)
        self.temp_roots = _normalize_roots(temp_roots, "temporary")
        self.final_roots = _normalize_roots(final_roots, "final")
        if not self.temp_roots and not self.final_roots:
            raise QbittorrentConfigurationError("at least one qBittorrent media root is required")
        overlap = set(self.temp_roots).intersection(self.final_roots)
        if overlap:
            raise QbittorrentConfigurationError("temporary and final roots must be distinct")

        if credentials is not None and not isinstance(credentials, QbittorrentCredentials) and not callable(credentials):
            raise QbittorrentConfigurationError("qBittorrent credentials source is invalid")
        self._credentials = credentials
        self._transport = transport or UrllibTransport()
        self._logged_in = False

    def version(self) -> str:
        response = self._request("GET", "app/version")
        try:
            value = response.body.decode("utf-8").strip()
        except UnicodeDecodeError:
            raise QbittorrentProtocolError("invalid qBittorrent version response") from None
        if not value or len(value) > 64:
            raise QbittorrentProtocolError("invalid qBittorrent version response")
        return value

    def webapi_version(self) -> str:
        response = self._request("GET", "app/webapiVersion")
        try:
            value = response.body.decode("utf-8").strip()
        except UnicodeDecodeError:
            raise QbittorrentProtocolError("invalid qBittorrent Web API version response") from None
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", value):
            raise QbittorrentProtocolError("invalid qBittorrent Web API version response")
        return value

    def list_torrents(self) -> tuple[TorrentSummary, ...]:
        return self._list_torrents()

    def torrent_files(self, torrent_id: str) -> tuple[TorrentFile, ...]:
        torrent_id = validate_torrent_id(torrent_id)
        payload = self._request_json(
            "GET",
            "torrents/files",
            params={"hash": torrent_id},
            metadata_conflict=True,
        )
        if not isinstance(payload, list):
            raise QbittorrentProtocolError("invalid qBittorrent torrent files response")
        files: list[TorrentFile] = []
        seen_indexes: set[int] = set()
        for item in payload:
            if not isinstance(item, dict):
                raise QbittorrentProtocolError("invalid qBittorrent torrent file entry")
            index = _required_int(item, "index", minimum=0, maximum=2_147_483_647)
            if index in seen_indexes:
                raise QbittorrentProtocolError("duplicate qBittorrent torrent file index")
            seen_indexes.add(index)
            relative_path = _safe_relative_qb_path(_required_str(item, "name"))
            size = _required_int(item, "size", minimum=0)
            progress = _required_fraction(item, "progress")
            priority = _required_int(item, "priority", minimum=0)
            availability = _optional_fraction(item, "availability")
            piece_range = _piece_range(item.get("piece_range"))
            files.append(
                TorrentFile(
                    index=index,
                    relative_path=relative_path,
                    expected_size=size,
                    progress=progress,
                    piece_range=piece_range,
                    priority=priority,
                    availability=availability,
                )
            )
        if not files:
            # qBittorrent 4.5.2 can return an empty 200 response while a magnet
            # is still fetching metadata rather than returning the documented
            # 409.  A valid torrent has at least one file.
            raise QbittorrentMetadataUnavailable("qBittorrent torrent metadata is not available")
        return tuple(sorted(files, key=lambda entry: entry.index))

    def resolve_path(self, path: str | os.PathLike[str]) -> ResolvedTorrentFile | None:
        """Resolve an absolute playback path, returning ``None`` when unknown.

        An unsafe/out-of-root path, API failure, or ambiguous API result is
        explicit.  A caller such as ``playp()`` can catch ``QbittorrentError``
        and still launch VLC; identity resolution is never a playback gate.
        """

        target, location_kind = self._validate_playback_path(path)
        matches: dict[TorrentFileIdentity, ResolvedTorrentFile] = {}
        for torrent in self._list_torrents():
            if not self._summary_can_contain_target(torrent, target):
                continue
            try:
                files = self.torrent_files(torrent.torrent_id)
            except QbittorrentNotFound:
                # Torrent was removed between the list and files requests.
                continue
            for record in self._records(torrent, files):
                candidates = record.temporary_paths if location_kind == "temporary" else record.final_paths
                if target in candidates:
                    matches[record.identity] = replace(
                        record,
                        matched_path=target,
                        location_kind=location_kind,
                    )
        if not matches:
            return None
        if len(matches) != 1:
            raise QbittorrentAmbiguousPath(
                f"playback path matches {len(matches)} qBittorrent files"
            )
        return next(iter(matches.values()))

    def reconcile_completed_torrent(self, hook_torrent_id: str) -> tuple[ResolvedTorrentFile, ...]:
        """Return the latest records for one strictly validated completion hook.

        This operation is idempotent and intentionally does not modify qB.  A
        hook can hand the records to the local catalog to add final locations
        while preserving identities previously observed in the temp directory.
        """

        torrent_id = validate_torrent_id(hook_torrent_id)
        torrents = self._list_torrents(hashes=(torrent_id,))
        exact = [item for item in torrents if item.torrent_id == torrent_id]
        if not exact:
            raise QbittorrentNotFound("qBittorrent torrent was not found")
        if len(exact) != 1 or len(torrents) != 1:
            raise QbittorrentProtocolError("ambiguous qBittorrent torrent lookup")
        files = self.torrent_files(torrent_id)
        return self._records(exact[0], files)

    def reconcile_file(self, identity: TorrentFileIdentity) -> ResolvedTorrentFile:
        """Refresh one stable file identity after a move or service restart."""

        if not isinstance(identity, TorrentFileIdentity):
            raise InvalidHookIdentifier("invalid qBittorrent file identity")
        if identity.client_id != self.client_id:
            raise InvalidHookIdentifier("qBittorrent identity belongs to another client")
        records = self.reconcile_completed_torrent(identity.torrent_id)
        for record in records:
            if record.identity.file_index == identity.file_index:
                return record
        raise QbittorrentNotFound("qBittorrent torrent file was not found")

    def reconcile_hook_id(self, hook_id: str) -> ResolvedTorrentFile:
        identity = TorrentFileIdentity.from_hook_id(
            hook_id,
            expected_client_id=self.client_id,
        )
        return self.reconcile_file(identity)

    def _list_torrents(self, *, hashes: tuple[str, ...] = ()) -> tuple[TorrentSummary, ...]:
        params: dict[str, str] = {}
        if hashes:
            params["hashes"] = "|".join(validate_torrent_id(value) for value in hashes)
        payload = self._request_json("GET", "torrents/info", params=params)
        if not isinstance(payload, list):
            raise QbittorrentProtocolError("invalid qBittorrent torrent list response")
        torrents: list[TorrentSummary] = []
        identifiers: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                raise QbittorrentProtocolError("invalid qBittorrent torrent list entry")
            try:
                torrent_id = validate_torrent_id(_required_str(item, "hash"))
            except InvalidHookIdentifier:
                raise QbittorrentProtocolError("invalid torrent ID in qBittorrent response") from None
            if torrent_id in identifiers:
                raise QbittorrentProtocolError("duplicate torrent ID in qBittorrent response")
            identifiers.add(torrent_id)
            infohash_v1 = _optional_infohash(item.get("infohash_v1"), bits=1)
            infohash_v2 = _optional_infohash(item.get("infohash_v2"), bits=2)
            torrents.append(
                TorrentSummary(
                    torrent_id=torrent_id,
                    infohash_v1=infohash_v1,
                    infohash_v2=infohash_v2,
                    name=_required_str(item, "name"),
                    state=_required_str(item, "state"),
                    progress=_required_fraction(item, "progress"),
                    expected_size=_required_int(item, "total_size", minimum=0),
                    save_path=_optional_absolute_api_path(item.get("save_path"), "save_path"),
                    download_path=_optional_absolute_api_path(item.get("download_path"), "download_path"),
                    content_path=_optional_absolute_api_path(item.get("content_path"), "content_path"),
                )
            )
        return tuple(sorted(torrents, key=lambda entry: entry.torrent_id))

    def _records(
        self,
        torrent: TorrentSummary,
        files: tuple[TorrentFile, ...],
    ) -> tuple[ResolvedTorrentFile, ...]:
        records = []
        for file in files:
            temporary_paths, final_paths = self._candidate_paths(torrent, file, len(files))
            records.append(
                ResolvedTorrentFile(
                    identity=TorrentFileIdentity(
                        self.client_id,
                        torrent.torrent_id,
                        file.index,
                    ),
                    torrent_name=torrent.name,
                    torrent_state=torrent.state,
                    infohash_v1=torrent.infohash_v1,
                    infohash_v2=torrent.infohash_v2,
                    relative_path=file.relative_path,
                    expected_size=file.expected_size,
                    progress=file.progress,
                    piece_range=file.piece_range,
                    priority=file.priority,
                    availability=file.availability,
                    save_path=torrent.save_path,
                    download_path=torrent.download_path,
                    content_path=torrent.content_path,
                    temporary_paths=temporary_paths,
                    final_paths=final_paths,
                )
            )
        return tuple(records)

    def _candidate_paths(
        self,
        torrent: TorrentSummary,
        file: TorrentFile,
        file_count: int,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        relative = PurePosixPath(file.relative_path)
        candidates: dict[str, set[str]] = {"temporary": set(), "final": set()}

        for root in self.temp_roots:
            self._add_joined_candidate(candidates, Path(root), relative)
        for root in self.final_roots:
            self._add_joined_candidate(candidates, Path(root), relative)

        for api_path in (torrent.save_path, torrent.download_path):
            if api_path:
                self._add_joined_candidate(candidates, Path(api_path), relative)

        if torrent.content_path:
            content = Path(torrent.content_path)
            if file_count == 1:
                self._add_candidate(candidates, content)
            else:
                self._add_joined_candidate(
                    candidates,
                    content,
                    relative,
                    base_is_content_root=True,
                )

        return (
            tuple(sorted(candidates["temporary"])),
            tuple(sorted(candidates["final"])),
        )

    def _add_joined_candidate(
        self,
        candidates: dict[str, set[str]],
        base: Path,
        relative: PurePosixPath,
        *,
        base_is_content_root: bool = False,
    ) -> None:
        parts = relative.parts
        # qB's content_path already includes the common top-level folder for a
        # multi-file torrent.  save_path, download_path, and configured roots
        # are storage bases and do not.  Select exactly one join rather than
        # adding a known-impossible duplicated Foo/Foo candidate.
        if base_is_content_root and len(parts) > 1 and base.name == parts[0]:
            parts = parts[1:]
        self._add_candidate(candidates, base.joinpath(*parts))

    def _summary_can_contain_target(
        self,
        torrent: TorrentSummary,
        normalized_target: str,
    ) -> bool:
        """Conservatively filter torrents before requesting their file lists.

        qBittorrent's ``content_path`` is the exact current file path for a
        single-file torrent and the common content root for a multi-file one.
        Rebasing that path between download/save/configured roots also covers a
        VLC session that still reports the old temp path just after completion.
        """

        target = Path(normalized_target)
        api_bases = tuple(
            Path(value)
            for value in (torrent.download_path, torrent.save_path)
            if value
        )
        configured_bases = tuple(
            Path(value) for value in (*self.temp_roots, *self.final_roots)
        )
        all_bases = tuple(dict.fromkeys((*api_bases, *configured_bases)))

        if torrent.content_path:
            content = Path(torrent.content_path)
            possible_content_roots = {content}
            for source_base in all_bases:
                if not _is_within(content, source_base):
                    continue
                relative_content = content.relative_to(source_base)
                for destination_base in all_bases:
                    possible_content_roots.add(
                        destination_base.joinpath(relative_content)
                    )
            return any(_is_within(target, candidate) for candidate in possible_content_roots)

        # No metadata means qB may not have content_path yet.  Do not risk a
        # false negative: an API storage base is the strongest available hint.
        if api_bases:
            return any(_is_within(target, base) for base in api_bases)
        return True

    def _add_candidate(self, candidates: dict[str, set[str]], path: Path) -> None:
        try:
            normalized = _canonical_path(path)
            kind = self._location_kind(normalized)
        except QbittorrentUnsafePath:
            return
        candidates[kind].add(normalized)

    def _validate_playback_path(
        self,
        value: str | os.PathLike[str],
    ) -> tuple[str, str]:
        try:
            raw = os.fspath(value)
        except TypeError:
            raise QbittorrentUnsafePath("playback path must be an absolute filesystem path") from None
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise QbittorrentUnsafePath("playback path must be an absolute filesystem path")
        path = Path(raw)
        if not path.is_absolute() or ".." in path.parts:
            raise QbittorrentUnsafePath("playback path must not contain traversal")
        normalized = _canonical_path(path)
        return normalized, self._location_kind(normalized)

    def _location_kind(self, normalized_path: str) -> str:
        path = Path(normalized_path)
        matches: list[tuple[int, str]] = []
        for kind, roots in (("temporary", self.temp_roots), ("final", self.final_roots)):
            for root in roots:
                if _is_within(path, Path(root)):
                    matches.append((len(Path(root).parts), kind))
        if not matches:
            raise QbittorrentUnsafePath("playback path is outside configured media roots")
        most_specific = max(length for length, _kind in matches)
        kinds = {kind for length, kind in matches if length == most_specific}
        if len(kinds) != 1:
            raise QbittorrentConfigurationError("media roots give an ambiguous location kind")
        return next(iter(kinds))

    def _request_json(
        self,
        method: str,
        endpoint: str,
        *,
        params: Mapping[str, str] | None = None,
        metadata_conflict: bool = False,
    ) -> Any:
        response = self._request(method, endpoint, params=params, metadata_conflict=metadata_conflict)
        try:
            text = response.body.decode("utf-8")
            return json.loads(
                text,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise QbittorrentProtocolError(f"invalid JSON from qBittorrent endpoint {endpoint}") from None

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Mapping[str, str] | None = None,
        metadata_conflict: bool = False,
    ) -> HttpResponse:
        if method != "GET":
            raise QbittorrentConfigurationError("only read-only qBittorrent requests are allowed")
        self._ensure_login()
        response = self._raw_request(method, endpoint, params=params)
        if response.status in (401, 403) and self._credentials is not None:
            self._logged_in = False
            self._login()
            response = self._raw_request(method, endpoint, params=params)
        if response.status in (401, 403):
            raise QbittorrentAuthenticationError("qBittorrent Web API authentication is required")
        if response.status == 404:
            raise QbittorrentNotFound("qBittorrent object was not found")
        if response.status == 409 and metadata_conflict:
            raise QbittorrentMetadataUnavailable("qBittorrent torrent metadata is not available")
        if response.status >= 500:
            raise QbittorrentUnavailable("qBittorrent Web API is unavailable")
        if response.status != 200:
            raise QbittorrentProtocolError(
                f"unexpected qBittorrent status {response.status} for endpoint {endpoint}"
            )
        return response

    def _raw_request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> HttpResponse:
        suffix = f"/api/v2/{endpoint}"
        if params:
            suffix += "?" + urlencode(params)
        headers = {
            "Accept": "application/json",
            "Origin": self.base_url,
            "Referer": self.base_url + "/",
            "User-Agent": "van-video-library/qbittorrent-readonly",
        }
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            response = self._transport.request(
                method,
                self.base_url + suffix,
                headers=headers,
                body=body,
                timeout=self.timeout,
            )
        except QbittorrentError:
            raise
        except (TimeoutError, socket.timeout, OSError):
            raise QbittorrentUnavailable("qBittorrent Web API is unavailable") from None
        if not isinstance(response, HttpResponse):
            raise QbittorrentProtocolError("qBittorrent transport returned an invalid response")
        return response

    def _ensure_login(self) -> None:
        if self._credentials is not None and not self._logged_in:
            self._login()

    def _login(self) -> None:
        credentials = self._credentials() if callable(self._credentials) else self._credentials
        if not isinstance(credentials, QbittorrentCredentials):
            raise QbittorrentConfigurationError("qBittorrent credentials source returned an invalid value")
        body = urlencode(
            {"username": credentials.username, "password": credentials.password}
        ).encode("utf-8")
        response = self._raw_request("POST", "auth/login", body=body)
        if response.status == 403:
            raise QbittorrentAuthenticationError("qBittorrent login was rejected")
        if response.status >= 500:
            raise QbittorrentUnavailable("qBittorrent Web API is unavailable")
        if response.status != 200 or response.body.strip() != b"Ok.":
            raise QbittorrentAuthenticationError("qBittorrent login was rejected")
        self._logged_in = True


def _validate_local_base_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise QbittorrentConfigurationError("qBittorrent base URL is invalid")
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https"):
        raise QbittorrentConfigurationError("qBittorrent base URL must use HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise QbittorrentConfigurationError("credentials must not be embedded in qBittorrent URL")
    if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise QbittorrentConfigurationError("qBittorrent base URL must contain only an origin")
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    is_loopback = hostname in ("localhost", "localhost.localdomain")
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback:
        raise QbittorrentConfigurationError("qBittorrent base URL must be loopback-only")
    try:
        port = parsed.port
    except ValueError:
        raise QbittorrentConfigurationError("qBittorrent base URL port is invalid") from None
    host_text = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        host_text += f":{port}"
    return urlunsplit((parsed.scheme.casefold(), host_text, "", "", ""))


def _normalize_roots(
    values: Iterable[str | os.PathLike[str]],
    label: str,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, os.PathLike)):
        raise QbittorrentConfigurationError(f"qBittorrent {label} roots must be an iterable of paths")
    roots: set[str] = set()
    try:
        iterator = iter(values)
    except TypeError:
        raise QbittorrentConfigurationError(f"qBittorrent {label} roots are invalid") from None
    for value in iterator:
        try:
            raw = os.fspath(value)
        except TypeError:
            raise QbittorrentConfigurationError(f"qBittorrent {label} root is invalid") from None
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise QbittorrentConfigurationError(f"qBittorrent {label} root is invalid")
        path = Path(raw)
        if not path.is_absolute() or ".." in path.parts:
            raise QbittorrentConfigurationError(f"qBittorrent {label} root must be absolute")
        roots.add(_canonical_path(path))
    return tuple(sorted(roots))


def _canonical_path(path: Path) -> str:
    try:
        return str(path.resolve(strict=False))
    except (OSError, RuntimeError):
        raise QbittorrentUnsafePath("filesystem path could not be resolved safely") from None


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_relative_qb_path(value: str) -> str:
    if not value or "\x00" in value or "\\" in value:
        raise QbittorrentProtocolError("invalid relative path in qBittorrent response")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise QbittorrentProtocolError("unsafe relative path in qBittorrent response")
    return path.as_posix()


def _optional_absolute_api_path(value: Any, field_name: str) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or "\x00" in value:
        raise QbittorrentProtocolError(f"invalid qBittorrent {field_name}")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise QbittorrentProtocolError(f"invalid qBittorrent {field_name}")
    return _canonical_path(path)


def _required_str(item: Mapping[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise QbittorrentProtocolError(f"invalid qBittorrent field {key}")
    return value


def _required_int(
    item: Mapping[str, Any],
    key: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise QbittorrentProtocolError(f"invalid qBittorrent field {key}")
    if minimum is not None and value < minimum:
        raise QbittorrentProtocolError(f"invalid qBittorrent field {key}")
    if maximum is not None and value > maximum:
        raise QbittorrentProtocolError(f"invalid qBittorrent field {key}")
    return value


def _required_fraction(item: Mapping[str, Any], key: str) -> float:
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QbittorrentProtocolError(f"invalid qBittorrent field {key}")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise QbittorrentProtocolError(f"invalid qBittorrent field {key}")
    return result


def _optional_fraction(item: Mapping[str, Any], key: str) -> float | None:
    if key not in item or item[key] is None:
        return None
    return _required_fraction(item, key)


def _piece_range(value: Any) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise QbittorrentProtocolError("invalid qBittorrent piece range")
    first, last = value
    if isinstance(first, bool) or isinstance(last, bool) or not isinstance(first, int) or not isinstance(last, int):
        raise QbittorrentProtocolError("invalid qBittorrent piece range")
    # qBittorrent reports an unusual but valid non-ascending interval for a
    # zero-byte torrent member.  Retain the pair verbatim; only its shape and
    # non-negative integer type are part of the Web API contract we need.
    if first < 0 or last < 0:
        raise QbittorrentProtocolError("invalid qBittorrent piece range")
    return first, last


def _optional_infohash(value: Any, *, bits: int) -> str:
    if value in (None, ""):
        return ""
    pattern = _TORRENT_ID_RE if bits == 1 else _INFOHASH_V2_RE
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise QbittorrentProtocolError(f"invalid qBittorrent v{bits} infohash")
    return value.casefold()


__all__ = [
    "DEFAULT_BASE_URL",
    "HttpResponse",
    "HttpTransport",
    "InvalidHookIdentifier",
    "QbittorrentAmbiguousPath",
    "QbittorrentAuthenticationError",
    "QbittorrentClient",
    "QbittorrentConfigurationError",
    "QbittorrentCredentials",
    "QbittorrentError",
    "QbittorrentMetadataUnavailable",
    "QbittorrentNotFound",
    "QbittorrentProtocolError",
    "QbittorrentUnavailable",
    "QbittorrentUnsafePath",
    "ResolvedTorrentFile",
    "TorrentFile",
    "TorrentFileIdentity",
    "TorrentSummary",
    "UrllibTransport",
    "validate_client_id",
    "validate_file_index",
    "validate_torrent_id",
]
