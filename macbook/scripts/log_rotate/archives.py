"""Archive naming (`<log>.N.gz` or `<log>.N`) and discovery beside a log."""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import stat
import time
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
        # Size may reflect an uncommitted catch-up or corruption; resume validates the bytes.
        keys = ("path", "st_ino", "st_birthtime")
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



def _remove_linked_temporaries(path: str, ledger: dict, home: str) -> None:
    """Only a proven archive inode authorizes removal of an interrupted publish link."""
    directory = os.path.dirname(path)
    if directory != home and eligibility.location_refusal(directory, home) is not None:
        return
    try:
        if eligibility.directory_problem(directory, home) is not None:
            return
        require_owned(path, ledger)
        archive = os.lstat(path)
        entries = list(os.scandir(directory))
    except (OSError, LogRotateError):
        return
    for entry in entries:
        if not entry.name.startswith(".") or ".rotating." not in entry.name:
            continue
        try:
            info = entry.stat(follow_symlinks=False)
            if (stat.S_ISREG(info.st_mode)
                    and (info.st_dev, info.st_ino) == (archive.st_dev, archive.st_ino)):
                os.unlink(entry.path)
        except OSError:
            continue


def prune(home: str) -> dict:
    """Prune absent records and temporary aliases, retaining inaccessible archive records."""
    ledger = state.read_json(home, state.ARCHIVES_FILE)
    retained = {}
    for path, record in ledger.items():
        try:
            os.lstat(path)
        except OSError as error:
            if isinstance(error, (FileNotFoundError, NotADirectoryError)):
                continue
        retained[path] = record
        _remove_linked_temporaries(path, ledger, home)
    if retained != ledger:
        state.write_json(home, state.ARCHIVES_FILE, retained)
    return retained


def cleanup_temporaries(paths: list, home: str, ledger: dict) -> None:
    """Reclaim abandoned unpublished copies, never an archive inode or an unsafe path."""
    recorded = {(item.get("st_dev"), item.get("st_ino"))
                for item in ledger.values() if isinstance(item, dict)}
    for archive in ledger:
        try:
            info = os.lstat(archive)
            recorded.add((info.st_dev, info.st_ino))
        except OSError:
            pass
    cutoff = time.time() - 3600
    for path in paths:
        directory, base = os.path.split(path)
        if (eligibility.location_refusal(path, home) is not None
                or eligibility.directory_problem(directory, home) is not None):
            continue
        pattern = re.compile(re.escape(".{}.rotating.".format(base)) + r"[a-z0-9_]{8}\Z")
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            if pattern.fullmatch(entry.name) is None:
                continue
            try:
                info = entry.stat(follow_symlinks=False)
                if (stat.S_ISREG(info.st_mode) and info.st_uid == eligibility.current_uid()
                        and info.st_nlink == 1 and info.st_mtime < cutoff
                        and (info.st_dev, info.st_ino) not in recorded):
                    os.unlink(entry.path)
            except OSError:
                continue


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
        recorded = ledger.get(archive, {})
        if isinstance(recorded, dict) and isinstance(recorded.get("pending_truncate"), dict):
            # Adoption cannot cancel the truncate still owed to this published generation.
            require_owned(archive, ledger)
            additions[archive] = recorded
        else:
            additions[archive] = identity(archive)
    ledger.update(additions)
    state.write_json(home, state.ARCHIVES_FILE, ledger)
