"""Bootable clone evidence from configured labels and discovered devices."""
import os

from .van_dashboard_block_devices import block_device_descendants, root_block_device


def build_hotspares(configuration, rows, stamp_dir, stamp, now, mountpoints):
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
                "mounted": bool(mounts),
                "mountpoints": mounts,
                "last_clone_at": last_clone_at,
                "due": last_clone_at is None or age_seconds >= interval_seconds,
                "stale": last_clone_at is None
                or age_seconds > interval_seconds * clone_factor,
            }
        )
    return hotswaps
