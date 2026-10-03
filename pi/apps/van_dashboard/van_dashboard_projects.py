"""Persistent user-added project links; never fetch or execute submitted URLs."""

import copy
import uuid
from urllib.parse import urlsplit

__all__ = ["HostedProjectStore", "HostedProjectConflict"]

LINK_LABELS = {"local": "Local", "lan": "LAN", "ts": "Tailscale", "web": "Web"}


class HostedProjectConflict(ValueError):
    pass


def validate_project_fields(fields):
    if set(fields) - {"name", *LINK_LABELS}:
        raise ValueError("Unexpected project field")
    name = fields.get("name", "")
    if not isinstance(name, str):
        raise ValueError("Project name must be text")
    name = name.strip()
    if not name or len(name) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("Enter a project name of 1–80 characters")
    links = []
    for kind, label in LINK_LABELS.items():
        value = fields.get(kind, "")
        if not isinstance(value, str):
            raise ValueError(f"{label} URL must be text")
        value = value.strip()
        if not value:
            continue
        if len(value) > 2048 or "\\" in value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError(f"Invalid {label} URL")
        try:
            parsed = urlsplit(value)
            valid = (
                parsed.scheme in ("http", "https") and parsed.hostname
                and parsed.username is None and parsed.password is None
            )
            parsed.port  # Reject malformed or out-of-range port numbers.
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(f"{label} URL must use http:// or https:// without credentials")
        links.append({"kind": kind, "label": label, "url": value})
    if not links:
        raise ValueError("Enter at least one URL")
    return name, links


class HostedProjectStore:
    KEY = "hosted_projects"

    def __init__(self, store):
        self.store = store

    def snapshot(self):
        with self.store.lock:
            return copy.deepcopy(self.store.get(self.KEY, []))

    def add(self, fields):
        name, links = validate_project_fields(fields)
        with self.store.lock:
            previous = self.store.get(self.KEY)
            projects = list(previous or [])
            for project in projects:
                if project["name"].casefold() == name.casefold():
                    if project["links"] == links:
                        # A repeated submission after a lost response is safe.
                        return copy.deepcopy(projects)
                    raise HostedProjectConflict("A project with that name already exists")
            if len(projects) >= 100:
                raise ValueError("Project directory is full (100 saved projects)")
            projects.append({
                "id": f"custom-{uuid.uuid4().hex}", "name": name,
                "host": "Custom", "description": "", "links": links,
            })
            try:
                self.store.set(self.KEY, projects)
            except OSError:
                # StateStore updates memory before saving; roll it back if disk
                # persistence failed so GET cannot claim an unsaved success.
                if previous is None:
                    self.store.data.pop(self.KEY, None)
                else:
                    self.store.data[self.KEY] = previous
                raise
            return copy.deepcopy(projects)
