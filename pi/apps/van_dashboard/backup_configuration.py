"""Literal backup configuration parsing shared with the dashboard manager."""
import re
import shlex


def parse_configuration(cls, text, error):
    match = re.search(r"^\s*CLONE_TARGETS=\(([^)]*)\)\s*$", text, re.MULTILINE)
    if not match:
        raise error("backup configuration has no CLONE_TARGETS")
    try:
        entries = shlex.split(match.group(1), comments=True, posix=True)
    except ValueError as exc:
        raise error(f"could not parse CLONE_TARGETS: {exc}") from exc
    targets = []
    seen = set()
    for entry in entries:
        label, separator, raw_interval = entry.rpartition(":")
        if (
            not separator
            or not cls.CLONE_TARGET_RE.fullmatch(label)
            or not raw_interval.isdigit()
            or not 1 <= int(raw_interval) <= 3650
            or label in seen
        ):
            raise error("backup configuration has an invalid clone target")
        seen.add(label)
        targets.append({"label": label, "interval_days": int(raw_interval)})
    if not targets:
        raise error("backup configuration has no clone targets")

    def integer(name, default):
        value = re.search(rf"^\s*{re.escape(name)}=(\d+)\s*(?:#.*)?$", text, re.MULTILINE)
        return int(value.group(1)) if value else default

    clone_card_nominal_gb = integer("CLONE_CARD_NOMINAL_GB", 64)
    if clone_card_nominal_gb not in cls.CLONE_CARD_NOMINAL_GB_OPTIONS:
        raise error(
            "backup configuration has an unsupported clone card capacity"
        )

    return {
        "targets": targets,
        "borg_stale_hours": integer("BORG_STALE_HOURS", 48),
        "exfat_snapshot_stale_hours": integer(
            "EXFAT_SNAPSHOT_STALE_HOURS", 48
        ),
        "openwrt_backup_stale_hours": integer(
            "OPENWRT_BACKUP_STALE_HOURS", 72
        ),
        "clone_stale_factor": integer("CLONE_STALE_FACTOR", 2),
        "clone_card_nominal_gb": clone_card_nominal_gb,
    }
