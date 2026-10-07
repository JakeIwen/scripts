"""Bootable clone evidence from configured labels and discovered devices."""
import os
import re
import subprocess

from .van_dashboard_block_devices import block_device_descendants, root_block_device
from .van_dashboard_common import SUDO


def hotspare_used_bytes(row, label, command, timeout):
    """Read ext4 allocation without mounting a spare or replaying its journal."""
    if (row is None or row.get('fstype') != 'ext4' or
            not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', label)):
        return None
    capacity = row.get('size')
    if type(capacity) is not int or capacity <= 0:
        return None
    if any(row.get('mountpoints') or []):
        used = row.get('fsused')
        return used if type(used) is int and 0 <= used <= capacity else None
    try:
        result = command([SUDO, '-n', '/usr/bin/env', 'LC_ALL=C', '/usr/sbin/dumpe2fs',
                          '-h', '/dev/disk/by-label/' + label], timeout=min(timeout, 2))
        if result.returncode:
            return None
        header = dict(line.split(':', 1) for line in result.stdout.splitlines() if ':' in line)
        header = {key.strip(): value.strip() for key, value in header.items()}
        if header.get('Filesystem volume name') != label or header.get('Filesystem state') != 'clean':
            return None
        blocks, free, size = (int(header[key]) for key in ('Block count', 'Free blocks', 'Block size'))
        if not (0 <= free <= blocks and 1024 <= size <= 65536 and size & (size - 1) == 0
                and 0 < blocks * size <= capacity):
            return None
        return (blocks - free) * size
    except (KeyError, ValueError, OSError, subprocess.SubprocessError):
        return None


def build_hotspares(configuration, rows, stamp_dir, stamp, now, mountpoints, command, timeout):
    labels = {row.get("label"): row for row in rows if row.get("label")}
    clone_factor = configuration["clone_stale_factor"]
    hotswaps = []
    for target in configuration["targets"]:
        label = target["label"]
        row = labels.get(label)
        root = root_block_device(row) if row is not None else None
        mounts = [] if root is None else [
            point
            for device in block_device_descendants(root)
            for point in mountpoints(device)
        ]
        last_clone_at = stamp(os.path.join(stamp_dir, f"clone_{label}"))
        interval_seconds = target["interval_days"] * 86400
        age_seconds = None if last_clone_at is None else max(0, now - last_clone_at)
        hotswaps.append(
            {
                **target,
                "attached": row is not None,
                "device": root.get("path") if root is not None else None,
                "size_bytes": root.get("size") if root is not None else None,
                "used_bytes": (hotspare_used_bytes(row, label, command, timeout)
                               if sum(device.get('label') == label for device in rows) == 1 else None),
                "mounted": bool(mounts),
                "mountpoints": mounts,
                "last_clone_at": last_clone_at,
                "due": last_clone_at is None or age_seconds >= interval_seconds,
                "stale": last_clone_at is None
                or age_seconds > interval_seconds * clone_factor,
            }
        )
    return hotswaps
