"""Pure helpers for normalized ``lsblk`` device trees."""


def flatten_block_devices(devices, parent=None):
    """Return copied device rows with an explicit parent link."""
    rows = []
    for device in devices if isinstance(devices, list) else ():
        if not isinstance(device, dict):
            continue
        row = dict(device)
        row["_parent"] = parent
        rows.append(row)
        rows.extend(flatten_block_devices(device.get("children"), row))
    return rows


def root_block_device(row):
    """Return the top-level device for a flattened row."""
    current = row
    while current.get("_parent") is not None:
        current = current["_parent"]
    return current


def block_device_descendants(row):
    """Return a device and its nested children from the original tree."""
    values = [row]
    for child in row.get("children") or ():
        if isinstance(child, dict):
            values.extend(block_device_descendants(child))
    return values
