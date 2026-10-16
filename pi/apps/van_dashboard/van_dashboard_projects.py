"""Persistent hosted-project directory; never fetch or execute submitted URLs."""

import copy
import logging
import subprocess
import threading
import uuid

from .hosted_project_report import (
    HOST_LABELS, LINK_LABELS, Host, HostedProject, ReportCounts, Source, Status,
    link_addresses, utc_timestamp, validate_project_fields, validate_report, validate_updates,
)
from .hosted_project_seed import seed_projects
from .van_dashboard_common import NTFY_SEND, run_command

__all__ = ["HostedProjectStore", "HostedProjectConflict"]

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 2


class HostedProjectConflict(ValueError):
    pass


def sorted_projects(projects):
    return copy.deepcopy(sorted(projects, key=lambda project: project["name"].casefold()))


def migrate_projects(projects, timestamp):
    migrated = copy.deepcopy(projects)
    for project in migrated:
        project["host"] = {
            "Custom": Host.CUSTOM, "This Mac": Host.MAC, "Vanpi": Host.PI,
        }.get(project.get("host"), project.get("host", Host.CUSTOM))
        for field, value in {
            "key": None, "status": Status.LISTED, "source": Source.MANUAL,
            "live": True, "first_seen": None, "last_seen": None,
            "updated_at": timestamp, "description": "",
        }.items():
            project.setdefault(field, value)
    migrated.extend(seed_projects({project["name"].casefold() for project in migrated}, timestamp))
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


def adoptable_project(projects, listener):
    addresses = link_addresses(listener["links"])
    return next((project for project in projects if project["key"] is None
                 and addresses.intersection(link_addresses(project["links"]))), None)


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


def reconcile_listener(projects, by_key, listener, scanned_at, timestamp):
    key = listener["key"]
    entry = by_key.get(key)
    if entry is not None:
        fields = {"live": True, "last_seen": scanned_at,
                  "links": merge_links(entry["links"], listener["links"])}
        changed = any(entry[field] != value for field, value in fields.items())
        entry.update(fields)
        return "updated" if changed else None
    entry = adoptable_project(projects, listener)
    if entry is not None:
        entry.update({
            "key": key, "host": listener["host"], "live": True,
            "first_seen": scanned_at, "last_seen": scanned_at,
            "links": merge_links(entry["links"], listener["links"]),
        })
        outcome = "adopted"
    else:
        entry: HostedProject = {
            **listener, "id": key, "status": Status.NEW, "source": Source.SCANNED,
            "name": unique_scanned_name(projects, listener["name"], listener["host"]),
            "live": True, "first_seen": scanned_at, "last_seen": scanned_at,
            "updated_at": timestamp,
        }
        projects.append(entry)
        outcome = "created"
    by_key[key] = entry
    return outcome


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
            self.store.data[self.KEY] = migrate_projects(self.store.get(self.KEY, []), utc_timestamp())
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
                "id": f"custom-{uuid.uuid4().hex}", "key": None, "name": name,
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
        counts: ReportCounts = {"created": 0, "adopted": 0, "updated": 0, "stale": 0}
        new_names = []
        with self.store.lock:
            projects = copy.deepcopy(self.store.get(self.KEY, []))
            by_key = {project["key"]: project for project in projects if project["key"] is not None}
            timestamp = utc_timestamp()
            for listener in report["listeners"]:
                outcome = reconcile_listener(projects, by_key, listener, report["scanned_at"], timestamp)
                if outcome:
                    counts[outcome] += 1
                if outcome == "created":
                    new_names.append(by_key[listener["key"]]["name"])
            reported = {listener["key"] for listener in report["listeners"]}
            for project in projects:
                # Any entry a scan has claimed (created or adopted) belongs to the
                # host named in its key; manual entries without a key never go stale.
                key_host = project["key"].split(":", 1)[0] if project["key"] else None
                if (key_host in report["hosts"] and project["key"] not in reported
                        and project["live"]):
                    project["live"] = False
                    counts["stale"] += 1
            self._save(projects)
        if new_names:
            notify_new_projects(new_names)
        return counts
