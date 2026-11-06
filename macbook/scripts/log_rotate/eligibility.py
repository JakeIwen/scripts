"""Which files the rotator may touch: regular, user-owned, inside $HOME, reached without symlinks."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import Optional, Union

from .records import LogReason

# Credentials and synced state live here; never listed, never opened.
HARD_LINK_NOTE = "hard-linked file"
EXCLUDED_HOME_DIRS = (".claude", ".codex", "Library/Mobile Documents")


def current_uid() -> int:
    return os.getuid()


def real_home() -> str:
    return os.path.realpath(str(Path.home()))


def normalize(raw: str, home: str) -> Optional[str]:
    """An absolute, ~-expanded, dot-free path under the resolved home, or None when unusable."""
    if not isinstance(raw, str) or not raw or "\0" in raw:
        return None
    path = os.path.expanduser(raw)
    if not os.path.isabs(path):
        return None
    path = os.path.normpath(path)
    # A temporary or symlinked $HOME is addressed by its resolved spelling everywhere.
    lexical_home = os.path.normpath(str(Path.home()))
    if path == lexical_home or path.startswith(lexical_home + os.sep):
        path = home + path[len(lexical_home):]
    return path


def _inside(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory.rstrip(os.sep) + os.sep)


def location_refusal(path: str, home: str) -> Optional[str]:
    """Why a normalized path is off limits by name alone (nothing is stat'ed)."""
    if not path.startswith(home + os.sep):
        return "outside your home folder"
    relative = path[len(home) + 1:].casefold()
    for excluded in EXCLUDED_HOME_DIRS:
        if _inside(relative, excluded.casefold()):
            return "excluded location (~/{})".format(excluded)
    return None


@dataclass
class Inspection:
    """refusal set: off limits; info None without a refusal: the file does not exist."""
    refusal: Optional[str]
    info: Optional[os.stat_result]
    reason: Optional[LogReason] = None


def _lstat(path: str) -> Union[os.stat_result, OSError, None]:
    try:
        return os.lstat(path)
    except OSError as error:
        return None if isinstance(error, (FileNotFoundError, NotADirectoryError)) else error


def inspect(path: str, home: str) -> Inspection:
    refusal = location_refusal(path, home)
    if refusal is not None:
        return Inspection(refusal, None, LogReason.OUTSIDE_HOME if not path.startswith(home + os.sep)
                          else LogReason.EXCLUDED_LOCATION)
    current = home
    parts = path[len(home) + 1:].split(os.sep)
    for part in parts[:-1]:
        current = os.path.join(current, part)
        info = _lstat(current)
        if isinstance(info, OSError):
            return Inspection(current, None, LogReason.UNREADABLE_DIRECTORY)
        if info is None:
            return Inspection(None, None)
        if stat.S_ISLNK(info.st_mode):
            return Inspection("path goes through a symlink", None, LogReason.SYMLINK_DIRECTORY)
        if not stat.S_ISDIR(info.st_mode):
            return Inspection(None, None)
        if not os.access(current, os.X_OK):
            return Inspection(current, None, LogReason.UNREADABLE_DIRECTORY)
    info = _lstat(path)
    if isinstance(info, OSError):
        return Inspection(os.path.dirname(path), None, LogReason.UNREADABLE_DIRECTORY)
    if info is None:
        return Inspection(None, None)
    if stat.S_ISLNK(info.st_mode):
        return Inspection("symlink", None, LogReason.SYMLINK)
    if not stat.S_ISREG(info.st_mode):
        return Inspection("not a regular file", None, LogReason.NOT_REGULAR_FILE)
    if info.st_nlink > 1:
        return Inspection(HARD_LINK_NOTE, None, LogReason.HARD_LINK)
    if info.st_uid != current_uid():
        return Inspection("not owned by you", None, LogReason.NOT_OWNED)
    return Inspection(None, info)


def directory_problem(path: str, home: str) -> Optional[str]:
    """Why a scan root cannot be walked (absent, a symlink anywhere on its path), else None."""
    current = home
    for part in path[len(home) + 1:].split(os.sep):
        current = os.path.join(current, part)
        info = _lstat(current)
        if isinstance(info, OSError):
            return current
        if info is None:
            return "missing"
        if stat.S_ISLNK(info.st_mode):
            return "symlink"
        if not stat.S_ISDIR(info.st_mode):
            return "not a directory"
        if not os.access(current, os.X_OK):
            return current
    return None
