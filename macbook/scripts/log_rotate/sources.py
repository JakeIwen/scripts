"""Where logs are found: LaunchAgent plists, the tracked crontab, scan roots and extras."""
from __future__ import annotations

from dataclasses import dataclass
import os
import plistlib
from pathlib import Path
from typing import Iterator, List

import crontab_sync

from . import eligibility
from .records import LogReason, SOURCE_CRONTAB, SOURCE_EXTRA, SOURCE_LAUNCHAGENT, SOURCE_SCAN, THIRD_PARTY_LOG_DIR

# Relative to $HOME; the first is the Mac's job state, the second this repo's scratch logs.
SCAN_ROOTS = (".local/state", "dev/scripts/tmp", THIRD_PARTY_LOG_DIR)
SCAN_EXTENSIONS = (".log", ".out", ".err")
SCAN_MAX_DEPTH = 4
# Directories never walked: slow and not logs, or (.claude/.codex) agent state at any depth.
SCAN_SKIP_NAMES = frozenset((".git", "node_modules", "venv", ".venv", "__pycache__", ".claude", ".codex"))
LAUNCHAGENTS_DIR = "Library/LaunchAgents"
PLIST_MAX_BYTES = 1 << 20
PLIST_LOG_KEYS = ("StandardOutPath", "StandardErrorPath")


@dataclass(frozen=True)
class Candidate:
    path: str
    source: str


def launchagent_candidates(home: str) -> List[Candidate]:
    found = []  # type: List[Candidate]
    directory = Path(home) / LAUNCHAGENTS_DIR
    try:
        plists = sorted(directory.glob("*.plist"))
    except OSError:
        return found
    for plist in plists:
        try:
            if plist.stat().st_size > PLIST_MAX_BYTES:
                continue
            with plist.open("rb") as source:
                data = plistlib.load(source)
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
        if not isinstance(data, dict):
            continue
        label = data.get("Label") if isinstance(data.get("Label"), str) else plist.stem
        for key in PLIST_LOG_KEYS:
            path = eligibility.normalize(data.get(key), home) if isinstance(data.get(key), str) else None
            if path is not None and path != "/dev/null":
                found.append(Candidate(path, "{}:{}".format(SOURCE_LAUNCHAGENT, label)))
    return found


def crontab_candidates(home: str) -> List[Candidate]:
    repo_file, _state, _binary = crontab_sync.configuration()
    try:
        text = repo_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    found = []  # type: List[Candidate]
    for job_id, targets in crontab_sync.tracked_log_targets(text, Path(home)):
        for target in targets:
            path = eligibility.normalize(target, home)
            if path is not None:
                found.append(Candidate(path, "{}:{}".format(SOURCE_CRONTAB, job_id)))
    return found


def _walk(directory: str, depth: int, home: str) -> Iterator[str]:
    try:
        entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if (depth < SCAN_MAX_DEPTH and entry.name not in SCAN_SKIP_NAMES
                        and eligibility.location_refusal(entry.path, home) is None):
                    yield from _walk(entry.path, depth + 1, home)
            elif entry.name.endswith(SCAN_EXTENSIONS) and entry.is_file(follow_symlinks=False):
                yield entry.path
        except OSError:
            # Listing can succeed without search permission; inventory must show the refusal.
            if entry.name.endswith(SCAN_EXTENSIONS):
                yield entry.path


def scan_candidates(home: str) -> List[Candidate]:
    found = []  # type: List[Candidate]
    for relative in SCAN_ROOTS:
        root = os.path.join(home, relative)
        if eligibility.directory_problem(root, home) is not None:
            continue
        for path in _walk(root, 0, home):
            inspection = eligibility.inspect(path, home)
            if (inspection.refusal in (None, eligibility.HARD_LINK_NOTE)
                    or inspection.reason == LogReason.UNREADABLE_DIRECTORY):
                found.append(Candidate(path, SOURCE_SCAN))
    return found


def extra_candidates(extras: List[str], home: str) -> List[Candidate]:
    found = []  # type: List[Candidate]
    for extra in extras:
        path = eligibility.normalize(extra, home)
        if path is not None:
            found.append(Candidate(path, SOURCE_EXTRA))
    return found


def ordered_candidates(home: str, extras: List[str]) -> List[Candidate]:
    """LaunchAgents, then crontab, scan roots and extras; the first source of a path wins."""
    chosen = {}  # type: dict
    for candidate in (launchagent_candidates(home) + crontab_candidates(home)
                      + scan_candidates(home) + extra_candidates(extras, home)):
        chosen.setdefault(dedupe_key(candidate.path), candidate)
    return list(chosen.values())


def dedupe_key(path: str) -> str:
    """The file's resolved location without following the file itself (a symlink stays itself)."""
    return os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
