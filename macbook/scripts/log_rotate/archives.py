"""Archive naming (`<log>.N.gz` or `<log>.N`) and discovery beside a log."""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Dict, List

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
