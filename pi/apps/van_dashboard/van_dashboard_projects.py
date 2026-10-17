"""Persistent hosted-project directory; never fetch or execute submitted URLs."""

import copy
import logging
import subprocess
import threading
import uuid

from .hosted_project_report import (
    HOST_LABELS, LINK_LABELS, Host, HostedProject, LinkKind, ReportCounts, Source, Status,
    link_addresses, link_ports, utc_timestamp, validate_project_fields, validate_report,
    validate_updates,
)
from .hosted_project_seed import seed_projects
from .van_dashboard_common import NTFY_SEND, run_command

__all__ = ["HostedProjectStore", "HostedProjectConflict"]

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 3


class HostedProjectConflict(ValueError):
    pass


def sorted_projects(projects):
    return copy.deepcopy(sorted(projects, key=lambda project: project["name"].casefold()))


def migrate_projects(projects, version, timestamp):
    migrated = copy.deepcopy(projects)
    if version < 2:
        for project in migrated:
            project["host"] = {
                "Custom": Host.CUSTOM, "This Mac": Host.MAC, "Vanpi": Host.PI,
            }.get(project.get("host"), project.get("host", Host.CUSTOM))
            for field, value in {
                "status": Status.LISTED, "source": Source.MANUAL,
                "live": True, "first_seen": None, "last_seen": None,
                "updated_at": timestamp, "description": "",
            }.items():
                project.setdefault(field, value)
        migrated.extend(seed_projects({project["name"].casefold() for project in migrated}, timestamp))
    for project in migrated:
        # Schema 3 replaces the single scanner key with a list, so one row can
        # stand for an app reached through several services.
        if "services" not in project:
            key = project.pop("key", None)
            project["services"] = [{"key": key, "live": project["live"]}] if key else []
    return migrated


def send_project_notification(names):
    try:
        result = run_command([
            NTFY_SEND, "Van dashboard", f"{len(names)} new hosted projects: {', '.join(names)}",
        ])
        if result.returncode:
            logger.warning("Hosted-project notification failed (exit %s)", result.returncode)
    except (OSError, subprocess.SubprocessError):
        logger.warning("Could not send hosted-project notification", exc_info=True)


def notify_new_projects(names):
    thread = threading.Thread(
        target=send_project_notification, args=(list(names),),
        name="hosted-project-notification", daemon=True,
    )
    try:
        thread.start()
    except RuntimeError:
        logger.warning("Could not start hosted-project notification", exc_info=True)
        return None
    return thread


def key_host(key):
    return key.split(":", 1)[0]


def key_slug(key):
    # The app's own name inside a scanner key, comparable across hosts:
    # mac:com.jacobr.fieldwork, pi:fieldwork.service and web:fieldwork all give "fieldwork".
    return key.split(":", 1)[1].removeprefix("com.jacobr.").removesuffix(".service")


def project_keys(project):
    return {service["key"] for service in project["services"]}


def foldable(project):
    # Only rows a scan created and Jacob has not kept may be folded into
    # another row; kept rows and manual rows are never removed by a scan.
    return project["source"] == Source.SCANNED and project["status"] != Status.LISTED


def merge_links(existing, reported):
    # Scans know their own addresses, not every link Jacob attached to a
    # project: kinds the scan did not report are kept, and a reported link at
    # an unchanged host and port keeps its stored URL, path and label.
    by_kind = {link["kind"]: link for link in existing}
    for link in reported:
        current = by_kind.get(link["kind"])
        if current is None or link_addresses([current]) != link_addresses([link]):
            by_kind[link["kind"]] = link
    return [by_kind[kind] for kind in LINK_LABELS if kind in by_kind]


def unique_scanned_name(projects, name, host):
    existing = {project["name"].casefold() for project in projects}
    if name.casefold() not in existing:
        return name
    suffix = f" ({HOST_LABELS[host]})"
    number = 1
    while True:
        ending = suffix if number == 1 else f"{suffix} {number}"
        candidate = name[:80 - len(ending)].rstrip() + ending
        if candidate.casefold() not in existing:
            return candidate
        number += 1


def listener_groups(listeners):
    # Listeners of one host that share an address are one app: the telemetry
    # dashboard runs one systemd unit per bind address on the same port.
    groups = []
    for listener in listeners:
        addresses = link_addresses(listener["links"])
        group = {"host": listener["host"], "addresses": addresses, "listeners": [listener]}
        for other in [other for other in groups
                      if other["host"] == listener["host"] and other["addresses"] & addresses]:
            groups.remove(other)
            group["addresses"] |= other["addresses"]
            group["listeners"] += other["listeners"]
        groups.append(group)
    ordered = [sorted(group["listeners"], key=lambda item: (len(item["key"]), item["key"]))
               for group in groups]
    # Web routes go last so they can join a Mac row created earlier in the report.
    return sorted(ordered, key=lambda group: group[0]["host"] == Host.WEB)


def correlated_project(projects, group, excluded):
    # The rules in HOSTED_PROJECTS.md, in order: a shared address, a
    # lionfeather.dev route forwarding to a Mac app's port, the same name on
    # another host. A row never gets two services' worth of one host this way.
    host = group[0]["host"]
    addresses = set().union(*(link_addresses(listener["links"]) for listener in group))
    ports = {listener["service_port"] for listener in group if "service_port" in listener}
    slugs = {key_slug(listener["key"]) for listener in group}
    skipped = {id(project) for project in excluded}
    candidates = sorted(
        (project for project in projects if id(project) not in skipped
         and host not in {key_host(key) for key in project_keys(project)}),
        key=foldable,
    )
    rules = (
        lambda project: addresses & link_addresses(project["links"]),
        lambda project: ports & link_ports(project["links"], LinkKind.LOCAL),
        lambda project: slugs & {key_slug(key) for key in project_keys(project)},
    )
    for rule in rules:
        match = next((project for project in candidates if rule(project)), None)
        if match is not None:
            return match
    return None


def refresh_live(project):
    if project["services"]:
        project["live"] = any(service["live"] for service in project["services"])


def fold_project(projects, duplicate, target):
    target["services"] = sorted(target["services"] + duplicate["services"],
                                key=lambda service: service["key"])
    by_kind = {link["kind"]: link for link in duplicate["links"]}
    by_kind.update({link["kind"]: link for link in target["links"]})
    target["links"] = [by_kind[kind] for kind in LINK_LABELS if kind in by_kind]
    seen = [value for value in (target["first_seen"], duplicate["first_seen"]) if value]
    target["first_seen"] = min(seen, default=None)
    seen = [value for value in (target["last_seen"], duplicate["last_seen"]) if value]
    target["last_seen"] = max(seen, default=None)
    refresh_live(target)
    projects[:] = [project for project in projects if project is not duplicate]


def claim_listener(project, listener, scanned_at):
    # Addresses are facts the scan owns; names, descriptions and status are Jacob's.
    service = next((item for item in project["services"] if item["key"] == listener["key"]), None)
    adopted = service is None
    if adopted:
        if not project["services"]:
            # The first scan to claim a row decides its host.
            project["host"] = listener["host"]
        service = {"key": listener["key"], "live": False}
        project["services"] = sorted(project["services"] + [service], key=lambda item: item["key"])
    links = merge_links(project["links"], listener["links"])
    changed = adopted or not service["live"] or not project["live"] or links != project["links"]
    service["live"] = True
    project.update({"links": links, "live": True})
    if changed:
        # Only a change touches the row, so an unchanged scan writes nothing.
        project["first_seen"] = project["first_seen"] or scanned_at
        project["last_seen"] = scanned_at
    return "adopted" if adopted else "updated" if changed else None


def reconcile_group(projects, group, scanned_at, timestamp, counts):
    """Apply one app's listeners; return the new row's name if one was created."""
    keys = {listener["key"] for listener in group}
    holders = [project for project in projects if keys & project_keys(project)]
    keepers = [project for project in holders if not foldable(project)]
    if keepers:
        target = keepers[0]
    else:
        target = correlated_project(projects, group, holders) or next(iter(holders), None)
    for holder in holders:
        if holder is not target and foldable(holder):
            fold_project(projects, holder, target)
            counts["merged"] += 1
    created: HostedProject | None = None
    if target is None:
        primary = group[0]
        created = {
            "id": primary["key"], "services": [], "host": primary["host"],
            "name": unique_scanned_name(projects, primary["name"], primary["host"]),
            "description": primary["description"], "links": [],
            "status": Status.NEW, "source": Source.SCANNED, "live": True,
            "first_seen": None, "last_seen": None, "updated_at": timestamp,
        }
        projects.append(created)
        counts["created"] += 1
    for listener in group:
        project = next((item for item in projects if listener["key"] in project_keys(item)),
                       target or created)
        outcome = claim_listener(project, listener, scanned_at)
        if outcome and project is not created:
            counts[outcome] += 1
    return created["name"] if created else None


def mark_stale(projects, hosts, reported):
    stale = 0
    for project in projects:
        for service in project["services"]:
            if key_host(service["key"]) in hosts and service["key"] not in reported:
                service["live"] = False
        was_live = project["live"]
        refresh_live(project)
        if was_live and not project["live"]:
            stale += 1
    return stale


class HostedProjectStore:
    KEY = "hosted_projects"
    SCHEMA_KEY = "hosted_projects_schema"

    def __init__(self, store):
        self.store = store
        with self.store.lock:
            if self.store.get(self.SCHEMA_KEY, 0) >= SCHEMA_VERSION:
                return
            # Migrate in memory only: constructing the store (including importing
            # runtime in tests) must never create or rewrite a state file. The
            # next save of any key persists both, and the seed is deterministic.
            self.store.data[self.KEY] = migrate_projects(
                self.store.get(self.KEY, []), self.store.get(self.SCHEMA_KEY, 0), utc_timestamp())
            self.store.data[self.SCHEMA_KEY] = SCHEMA_VERSION

    def snapshot(self):
        with self.store.lock:
            return sorted_projects(self.store.get(self.KEY, []))

    def _save(self, projects):
        previous = self.store.get(self.KEY)
        if projects == previous:
            return
        try:
            self.store.set(self.KEY, projects)
        except OSError:
            # StateStore mutates memory first; a failed request must not look saved.
            if previous is None:
                self.store.data.pop(self.KEY, None)
            else:
                self.store.data[self.KEY] = previous
            raise

    def add(self, fields):
        name, links = validate_project_fields(fields)
        with self.store.lock:
            projects = copy.deepcopy(self.store.get(self.KEY, []))
            for project in projects:
                if project["name"].casefold() == name.casefold():
                    if project["links"] == links:
                        # A repeated submission after a lost response is safe.
                        return sorted_projects(projects)
                    raise HostedProjectConflict("A project with that name already exists")
            if len(projects) >= 100:
                raise ValueError("Project directory is full (100 saved projects)")
            projects.append({
                "id": f"custom-{uuid.uuid4().hex}", "services": [], "name": name,
                "host": Host.CUSTOM, "description": "", "links": links,
                "status": Status.LISTED, "source": Source.MANUAL, "live": True,
                "first_seen": None, "last_seen": None, "updated_at": utc_timestamp(),
            })
            self._save(projects)
            return sorted_projects(projects)

    def update(self, entry_id, fields):
        changes = validate_updates(fields)
        with self.store.lock:
            projects = copy.deepcopy(self.store.get(self.KEY, []))
            entry = next((project for project in projects if project["id"] == entry_id), None)
            if entry is None:
                raise KeyError(entry_id)
            if "name" in changes and any(
                project["id"] != entry_id and project["name"].casefold() == changes["name"].casefold()
                for project in projects
            ):
                raise HostedProjectConflict("A project with that name already exists")
            if any(entry[field] != value for field, value in changes.items()):
                entry.update(changes)
                entry["updated_at"] = utc_timestamp()
                self._save(projects)
            return sorted_projects(projects)

    def apply_report(self, report) -> ReportCounts:
        report = validate_report(report)
        counts: ReportCounts = {"created": 0, "adopted": 0, "merged": 0, "updated": 0, "stale": 0}
        with self.store.lock:
            projects = copy.deepcopy(self.store.get(self.KEY, []))
            timestamp = utc_timestamp()
            new_names = [
                reconcile_group(projects, group, report["scanned_at"], timestamp, counts)
                for group in listener_groups(report["listeners"])
            ]
            reported = {listener["key"] for listener in report["listeners"]}
            counts["stale"] = mark_stale(projects, report["hosts"], reported)
            self._save(projects)
        new_names = [name for name in new_names if name]
        if new_names:
            notify_new_projects(new_names)
        return counts
