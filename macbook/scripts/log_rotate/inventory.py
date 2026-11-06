"""The log inventory: every candidate file with its size, archives, policy, growth and state."""
from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Any, Dict, List, Optional

from . import eligibility, growth, policy, sources, state
from .archives import Archive, scan_directory
from .records import (MB, STATE_DISABLED, STATE_DUE, STATE_MISSING, STATE_OK, STATE_REFUSED,
                      LogEntry)


@dataclass
class Snapshot:
    generated_at: float
    config: policy.FilesConfig
    defaults: Dict[str, Any]
    defaults_error: Optional[str]
    entries: List[LogEntry]
    ignored: List[str]

    def live_entries(self) -> List[LogEntry]:
        return [entry for entry in self.entries if entry.state not in (STATE_MISSING, STATE_REFUSED)]

    def totals(self) -> Dict[str, int]:
        counted = [entry for entry in self.entries if entry.state != STATE_REFUSED]
        return {"files": len(self.live_entries()),
                "bytes": sum(entry.bytes for entry in counted),
                "archived_bytes": sum(entry.archived_bytes for entry in counted)}

    def entry(self, path: str) -> Optional[LogEntry]:
        return next((entry for entry in self.entries if entry.path == path), None)

    def as_dict(self) -> Dict[str, Any]:
        value = {"generated_at": self.generated_at, "revision": self.config.revision,
                 "defaults": dict(self.defaults), "totals": self.totals(),
                 "logs": [entry.as_dict() for entry in self.entries],
                 "ignored": list(self.ignored)}
        if self.defaults_error is not None:
            value["defaults_error"] = self.defaults_error
        return value


def is_due(size: int, max_mb: int) -> bool:
    return size > max_mb * MB


def _entry_state(size: int, existing: bool, entry_policy) -> str:
    if not existing:
        return STATE_MISSING
    if not entry_policy.enabled:
        return STATE_DISABLED
    return STATE_DUE if is_due(size, entry_policy.max_mb) else STATE_OK


def _build_entry(candidate: sources.Candidate, home: str, defaults: Dict[str, Any],
                 config: policy.FilesConfig, ledger: Dict[str, Any], rotated: Dict[str, Any],
                 listings: Dict[str, Dict[str, List[Archive]]], now: float) -> LogEntry:
    path = candidate.path
    entry_policy = policy.effective_policy(defaults, config, path)
    inspection = eligibility.inspect(path, home)
    last_rotated = rotated.get(path) if isinstance(rotated.get(path), (int, float)) else None
    if inspection.refusal is not None:
        return LogEntry(path, candidate.source, 0, 0, 0, None, None, entry_policy, last_rotated,
                        STATE_REFUSED, inspection.refusal)
    directory, base = os.path.split(path)
    if directory not in listings:
        listings[directory] = scan_directory(directory)
    archives = listings[directory].get(base, [])
    info = inspection.info
    size = info.st_size if info is not None else 0
    return LogEntry(path, candidate.source, size, sum(item.size for item in archives), len(archives),
                    info.st_mtime if info is not None else None,
                    growth.rate_for(ledger.get(path), now), entry_policy, last_rotated,
                    _entry_state(size, info is not None, entry_policy))


def take_snapshot(home: str, now: Optional[float] = None) -> Snapshot:
    now = time.time() if now is None else now
    defaults, defaults_error = policy.load_defaults()
    config = policy.read_files_config()
    ledger = state.read_json(home, state.SIZES_FILE).get("paths")
    ledger = ledger if isinstance(ledger, dict) else {}
    rotated = state.read_json(home, state.ROTATED_FILE)
    ignored = [path for path in (eligibility.normalize(raw, home) for raw in config.ignored) if path]
    listings = {}  # type: Dict[str, Dict[str, List[Archive]]]
    entries = [_build_entry(candidate, home, defaults, config, ledger, rotated, listings, now)
               for candidate in sources.ordered_candidates(home, config.extras)
               if candidate.path not in ignored]
    entries.sort(key=lambda entry: (-entry.bytes, entry.path))
    return Snapshot(round(now, 3), config, defaults, defaults_error, entries, sorted(ignored))
