"""Hosted-project vocabulary, records, and untrusted-input validation."""

from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import NotRequired, TypedDict
from urllib.parse import urlsplit


class Host(StrEnum):
    MAC = "mac"
    PI = "pi"
    WEB = "web"
    CUSTOM = "custom"


class Status(StrEnum):
    NEW = "new"
    LISTED = "listed"
    HIDDEN = "hidden"


class Source(StrEnum):
    SCANNED = "scanned"
    MANUAL = "manual"


class LinkKind(StrEnum):
    LOCAL = "local"
    LAN = "lan"
    TS = "ts"
    WEB = "web"


LINK_LABELS = {
    LinkKind.LOCAL: "Local", LinkKind.LAN: "LAN",
    LinkKind.TS: "Tailscale", LinkKind.WEB: "Web",
}
HOST_LABELS = {Host.MAC: "This Mac", Host.PI: "Vanpi", Host.WEB: "lionfeather.dev", Host.CUSTOM: "Custom"}
SCAN_HOSTS = frozenset({Host.MAC, Host.PI, Host.WEB})
# The link kinds each host's scan reports; a row's links of these kinds are the scan's to set.
HOST_LINK_KINDS = {
    Host.MAC: frozenset({LinkKind.LOCAL}),
    Host.PI: frozenset({LinkKind.LAN, LinkKind.TS}),
    Host.WEB: frozenset({LinkKind.WEB}),
}
REVIEW_STATUSES = frozenset({Status.LISTED, Status.HIDDEN})


class ProjectLink(TypedDict):
    kind: str
    label: str
    url: str


class ProjectListener(TypedDict):
    key: str
    host: str
    name: str
    description: str
    links: list[ProjectLink]
    # Web listeners only: the port lionfeather-edge forwards to, which ties a
    # public route to the Mac app serving the same port.
    service_port: NotRequired[int]


class ProjectService(TypedDict):
    key: str
    live: bool


class HostedProject(TypedDict):
    id: str
    services: list[ProjectService]
    host: str
    name: str
    description: str
    links: list[ProjectLink]
    status: str
    source: str
    live: bool
    first_seen: str | None
    last_seen: str | None
    updated_at: str


class ScanReport(TypedDict):
    hosts: list[str]
    scanned_at: str
    listeners: list[ProjectListener]


class ReportCounts(TypedDict):
    created: int
    adopted: int
    merged: int
    updated: int
    stale: int


class ProjectUpdates(TypedDict, total=False):
    name: str
    description: str
    status: str
    links: list[ProjectLink]


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_name(value):
    if not isinstance(value, str):
        raise ValueError("Project name must be text")
    value = value.strip()
    if not value or len(value) > 80 or not value.isprintable():
        raise ValueError("Enter a project name of 1–80 printable characters")
    return value


def validate_description(value):
    if not isinstance(value, str) or len(value) > 200:
        raise ValueError("Project description must be text of at most 200 characters")
    return value


def validate_url(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} URL must be text")
    value = value.strip()
    if len(value) > 2048 or "\\" in value or any(c.isspace() or not c.isprintable() for c in value):
        raise ValueError(f"Invalid {label} URL")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in ("http", "https") and parsed.hostname
            and parsed.username is None and parsed.password is None
        )
        parsed.port  # Parsing this property rejects malformed port numbers.
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"{label} URL must use http:// or https:// without credentials")
    return value


def validate_links(value) -> list[ProjectLink]:
    if not isinstance(value, list) or not 1 <= len(value) <= 4:
        raise ValueError("Enter one to four project links")
    links = []
    for link in value:
        if not isinstance(link, dict) or set(link) - {"kind", "label", "url"}:
            raise ValueError("Unexpected project link field")
        kind = link.get("kind")
        if not isinstance(kind, str) or kind not in LINK_LABELS:
            raise ValueError("Invalid project link kind")
        label = link.get("label", LINK_LABELS[kind])
        if not isinstance(label, str) or not label.strip() or not label.isprintable():
            raise ValueError("Project link label must be printable text")
        links.append({"kind": kind, "label": label, "url": validate_url(link.get("url"), label)})
    if len({link["kind"] for link in links}) != len(links):
        raise ValueError("Project link kinds must be unique")
    return sorted(links, key=lambda link: tuple(LINK_LABELS).index(link["kind"]))


def validate_project_fields(fields):
    if not isinstance(fields, dict) or set(fields) - {"name", *LINK_LABELS}:
        raise ValueError("Unexpected project field")
    name = validate_name(fields.get("name", ""))
    links = []
    for kind, label in LINK_LABELS.items():
        value = fields.get(kind, "")
        if not isinstance(value, str):
            raise ValueError(f"{label} URL must be text")
        if value.strip():
            links.append({"kind": kind, "label": label, "url": value})
    return name, validate_links(links)


def validate_updates(fields) -> ProjectUpdates:
    if not isinstance(fields, dict) or set(fields) - {"name", "description", "status", "links"}:
        raise ValueError("Unexpected project field")
    result = {}
    if "name" in fields:
        result["name"] = validate_name(fields["name"])
    if "description" in fields:
        result["description"] = validate_description(fields["description"])
    if "status" in fields:
        status = fields["status"]
        if not isinstance(status, str) or status not in REVIEW_STATUSES:
            raise ValueError("Project status must be listed or hidden")
        result["status"] = status
    if "links" in fields:
        result["links"] = validate_links(fields["links"])
    return result


def validate_service_port(value, host):
    if host != Host.WEB or isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 65536:
        raise ValueError("service_port must be the upstream port of a web listener")
    return value


def validate_listener(listener, hosts) -> ProjectListener:
    required = {"key", "host", "name", "description", "links"}
    if (not isinstance(listener, dict) or not required <= set(listener)
            or set(listener) - required - {"service_port"}):
        raise ValueError("Listener must contain key, host, name, description, and links")
    host, key = listener["host"], listener["key"]
    if not isinstance(host, str) or host not in hosts:
        raise ValueError("Listener host must be covered by this report")
    if (not isinstance(key, str) or not key.startswith(f"{host}:")
            or len(key) == len(host) + 1 or not key.isprintable()
            or any(c.isspace() for c in key)):
        raise ValueError("Listener key must be <host>:<unit>")
    result: ProjectListener = {
        "key": key, "host": host, "name": validate_name(listener["name"]),
        "description": validate_description(listener["description"]),
        "links": validate_links(listener["links"]),
    }
    kinds = {link["kind"] for link in result["links"]}
    if not kinds or not kinds <= HOST_LINK_KINDS[host]:
        raise ValueError("Listener links must be the link kinds its host reports")
    if "service_port" in listener:
        result["service_port"] = validate_service_port(listener["service_port"], host)
    return result


def validate_report(report) -> ScanReport:
    if not isinstance(report, dict) or set(report) != {"hosts", "scanned_at", "listeners"}:
        raise ValueError("Report must contain hosts, scanned_at, and listeners")
    hosts = report["hosts"]
    if (not isinstance(hosts, list)
            or any(not isinstance(host, str) or host not in SCAN_HOSTS for host in hosts)
            or len(set(hosts)) != len(hosts)):
        raise ValueError("Report hosts must be distinct mac, pi, or web hosts")
    timestamp = report["scanned_at"]
    if not isinstance(timestamp, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", timestamp
    ):
        raise ValueError("scanned_at must be a UTC ISO 8601 timestamp ending in Z")
    try:
        datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise ValueError("Invalid scanned_at timestamp") from exc
    listeners = report["listeners"]
    if not isinstance(listeners, list) or len(listeners) > 200:
        raise ValueError("Report must contain at most 200 listeners")
    validated = [validate_listener(listener, hosts) for listener in listeners]
    if len({listener["key"] for listener in validated}) != len(validated):
        raise ValueError("Report listener keys must be unique")
    return {"hosts": list(hosts), "scanned_at": timestamp, "listeners": validated}


def link_addresses(links):
    addresses = set()
    for link in links:
        parsed = urlsplit(link["url"])
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        addresses.add((parsed.hostname, port))
    return addresses


def link_ports(links, kind):
    return {port for link in links if link["kind"] == kind for _, port in link_addresses([link])}
