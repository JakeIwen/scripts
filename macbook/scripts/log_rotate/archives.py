"""Archive naming (`<log>.N.gz` or `<log>.N`) and discovery beside a log."""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import stat
from typing import Dict, List

from .records import EXIT_ERROR, LogRotateError

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


def identity(path: str) -> dict:
    """lstat never follows an archive symlink, even if its target was once ours."""
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise LogRotateError(EXIT_ERROR, "foreign file at {}".format(path))
    return {"path": path, "st_dev": info.st_dev, "st_ino": info.st_ino, "size": info.st_size}


def require_owned(path: str, ledger: dict) -> None:
    if identity(path) != ledger.get(path):
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
    destinations = [archive_path(directory, base, item.index + 1, item.gzipped)
                    for item in existing if item.index + 1 <= keep]
    if keep:
        destinations.append(archive_path(directory, base, 1, compress))
    for path in destinations:
        if os.path.lexists(path):
            require_owned(path, ledger)
    return sorted(existing, key=lambda item: -item.index)


def publish(source: str, destination: str) -> None:
    """Exclusive publication refuses a destination that appeared since preflight."""
    try:
        os.link(source, destination, follow_symlinks=False)
    except FileExistsError:
        raise LogRotateError(EXIT_ERROR, "foreign file at {}".format(destination)) from None
    os.unlink(source)
