"""Resolve durable filesystem IDs without retaining kernel device-number caches."""

from __future__ import annotations

import os
from pathlib import Path
import stat as stat_mode
import sys


UUID_PREFIX = "fsuuid:"
UUID_DIRECTORY = Path("/dev/disk/by-uuid")


def filesystem_device_id(path: str, stat: os.stat_result) -> str:
    """Match the observed mount's device to its udev filesystem UUID.

    A missing/ambiguous Linux mapping is an unavailable observation, not permission
    to downgrade to a recycled kernel number. Non-Linux hosts retain the legacy
    numeric format, without claiming reboot-stable identity there.
    """
    if sys.platform != "linux":
        return str(stat.st_dev)
    matches = []
    for entry in UUID_DIRECTORY.iterdir():
        try:
            device = entry.stat()
        except FileNotFoundError:
            continue  # An unrelated device may disappear during enumeration.
        if stat_mode.S_ISBLK(device.st_mode) and device.st_rdev == stat.st_dev:
            matches.append(entry.name)
    if len(matches) != 1:
        raise OSError(f"filesystem UUID unavailable or ambiguous for {path}")
    if os.stat(path).st_dev != stat.st_dev:
        raise OSError(f"filesystem changed during observation: {path}")
    return UUID_PREFIX + matches[0]


def file_stat_values(
    path: str, *, size: int | None, device_id: str | None,
    inode: int | None, mtime_ns: int | None,
) -> tuple[int | None, str | None, int | None, int | None]:
    """Fill missing observations; explicit values remain usable for imports/tests."""
    if size is None or device_id is None or inode is None or mtime_ns is None:
        stat = os.stat(path)
        size = stat.st_size if size is None else size
        device_id = filesystem_device_id(path, stat) if device_id is None else str(device_id)
        inode = stat.st_ino if inode is None else inode
        mtime_ns = stat.st_mtime_ns if mtime_ns is None else mtime_ns
    return size, device_id, inode, mtime_ns


def verified_legacy_path(path: str, device_id: str, inode: int, size: int, mtime_ns: int) -> bool:
    """A legacy promotion requires live path evidence, even for explicit inputs."""
    stat = os.stat(path)
    return (
        (stat.st_ino, stat.st_size, stat.st_mtime_ns) == (inode, size, mtime_ns)
        and filesystem_device_id(path, stat) == device_id
    )
