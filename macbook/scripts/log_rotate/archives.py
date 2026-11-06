"""Archive naming (`<log>.N.gz` or `<log>.N`) and discovery beside a log."""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import stat
from typing import Dict, List

from . import eligibility, state
from .records import EXIT_ERROR, LogRotateError

UNRECORDED_NOTE = "unrecorded archives; adopt to continue"

_ARCHIVE_SUFFIX = re.compile(r"\.(\d+)(\.gz)?\Z")


@dataclass(frozen=True)
class Archive:
    index: int
    gzipped: bool
    path: str
    size: int


def archive_path(directory: str, base: str, index: int, gzipped: bool) -> str:
    return os.path.join(directory, "{}.{}{}".format(base, index, ".gz" if gzipped else ""))


def scan_directory(directory: str) -> Dict[str, List[Archive]]:
    """Archives in one directory grouped by the log name they belong to."""
    found = {}  # type: Dict[str, List[Archive]]
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return found
    for entry in entries:
        match = _ARCHIVE_SUFFIX.search(entry.name)
        if match is None or match.start() == 0:
            continue
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            size = entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
        found.setdefault(entry.name[:match.start()], []).append(
            Archive(int(match.group(1)), match.group(2) is not None, entry.path, size))
    return found


def identity(path: str, info: os.stat_result = None) -> dict:
    """lstat never follows an archive symlink, even if its target was once ours."""
    info = os.lstat(path) if info is None else info
    if not stat.S_ISREG(info.st_mode):
        raise LogRotateError(EXIT_ERROR, "foreign file at {}".format(path))
    return {"path": path, "st_dev": info.st_dev, "st_ino": info.st_ino, "size": info.st_size,
            "st_birthtime": getattr(info, "st_birthtime", None)}


def require_owned(path: str, ledger: dict) -> None:
    current, recorded = identity(path), ledger.get(path)
    # macOS can assign a different device number after reboot.
    keys = ("path", "st_ino", "size", "st_birthtime")
    if isinstance(recorded, dict) and isinstance(recorded.get("pending_truncate"), dict):
        # A late catch-up may have been fsynced before its size record could be saved.
        keys = ("path", "st_ino", "st_birthtime")
        if current["size"] < recorded.get("size", 0):
            raise LogRotateError(EXIT_ERROR, "foreign file at {}".format(path))
    if not isinstance(recorded, dict) or any(current[key] != recorded.get(key) for key in keys):
        raise LogRotateError(EXIT_ERROR, "foreign file at {}".format(path))


def rotation_plan(directory: str, base: str, keep: int, compress: bool, ledger: dict) -> list:
    """Check the whole namespace before changing anything, including unretained names."""
    existing = []
    for name in os.listdir(directory):
        if not name.startswith(base + "."):
            continue
        match = _ARCHIVE_SUFFIX.fullmatch(name[len(base):])
        if match is None:
            continue
        path = os.path.join(directory, name)
        require_owned(path, ledger)
        existing.append(Archive(int(match[1]), match[2] is not None, path, os.lstat(path).st_size))
    existing.sort(key=lambda item: item.index)
    destinations = [archive_path(directory, base, position + 1, item.gzipped)
                    for position, item in enumerate(existing, 1) if position + 1 <= keep]
    if keep:
        destinations.append(archive_path(directory, base, 1, compress))
    for path in destinations:
        if os.path.lexists(path):
            require_owned(path, ledger)
    return existing


def unique_archives(existing: list, ledger: dict, home: str) -> list:
    """An interrupted link/unlink is one generation at its higher-numbered position."""
    seen, survivors = set(), []
    for item in reversed(existing):
        require_owned(item.path, ledger)
        info = os.lstat(item.path)
        key = (info.st_dev, info.st_ino)
        if key in seen:
            os.unlink(item.path)
            ledger.pop(item.path, None)
            state.write_json(home, state.ARCHIVES_FILE, ledger)
        else:
            seen.add(key)
            survivors.append(item)
    return list(reversed(survivors))


def publish(source: str, destination: str) -> None:
    """Exclusive publication refuses a destination that appeared since preflight."""
    try:
        os.link(source, destination, follow_symlinks=False)
    except FileExistsError:
        raise LogRotateError(EXIT_ERROR, "foreign file at {}".format(destination)) from None
    os.unlink(source)


def prune(home: str) -> dict:
    """Remove only absent paths; publication records may deliberately precede their files."""
    ledger = state.read_json(home, state.ARCHIVES_FILE)
    retained = {}
    for path, record in ledger.items():
        try:
            os.lstat(path)
        except OSError as error:
            if isinstance(error, (FileNotFoundError, NotADirectoryError)):
                continue
        retained[path] = record
    if retained != ledger:
        state.write_json(home, state.ARCHIVES_FILE, retained)
    return retained


def adopt(path: str, home: str) -> None:
    """Explicitly trust eligible existing archives, without changing any archive bytes."""
    directory, base = os.path.split(path)
    ledger = prune(home)
    additions = {}
    for name in os.listdir(directory):
        if not name.startswith(base + ".") or _ARCHIVE_SUFFIX.fullmatch(name[len(base):]) is None:
            continue
        archive = os.path.join(directory, name)
        inspection = eligibility.inspect(archive, home)
        if inspection.refusal is not None or inspection.info is None:
            raise LogRotateError(EXIT_ERROR, "cannot adopt {}: {}".format(
                archive, inspection.refusal or "file is missing"))
        additions[archive] = identity(archive)
    ledger.update(additions)
    state.write_json(home, state.ARCHIVES_FILE, ledger)
