"""Rotation decisions and incomplete-clone evidence; never probes or writes a disk."""
from dataclasses import dataclass
from pathlib import Path
import argparse
import os
import re
import stat
import sys
import time

from time_machine_store import atomic_json


@dataclass(frozen=True)
class RotationPlan:
    target: str | None
    next_at: int | None
    message: str | None = None
    incomplete: tuple[str, ...] = ()


def evidence_path(directory, label, incomplete=False):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', label):
        raise ValueError('Invalid hotspare label.')
    return Path(directory) / ('clone_' + label + ('.incomplete' if incomplete else ''))


def recorded_time(path):
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError('Clone history is not a regular file.')
    return int(metadata.st_mtime)


def rotation_plan(directory, labels, interval_days, now=None):
    now = int(time.time()) if now is None else now
    if (len(labels) != 2 or len(set(labels)) != 2 or type(interval_days) is not int
            or not 1 <= interval_days <= 365):
        raise ValueError('Rotation requires two distinct cards and an interval of 1–365 days.')
    try:
        if Path(directory).is_symlink():
            raise ValueError('Unsafe clone history directory.')
        stamps = {label: recorded_time(evidence_path(directory, label)) for label in labels}
        incomplete = tuple(label for label in labels
                           if recorded_time(evidence_path(directory, label, True)) is not None)
        if any(value is not None and value > now for value in stamps.values()):
            raise ValueError('Clone history is in the future.')
    except (OSError, ValueError):
        return RotationPlan(None, None, 'Cannot verify clone history; automatic rotation paused.')
    good = {label: value for label, value in stamps.items()
            if value is not None and label not in incomplete}
    if not good:
        return RotationPlan(None, None, 'Automatic rotation needs a completed recovery card.', incomplete)
    if incomplete:
        return RotationPlan(incomplete[0], now, 'Retry incomplete clone; preserve the other card.', incomplete)
    target = min(labels, key=lambda label: stamps[label] if stamps[label] is not None else -1)
    return RotationPlan(target, max(good.values()) + interval_days * 86400)


def sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def mark_clone(directory, label, *, complete):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ValueError('Unsafe clone history directory.')
    marker = evidence_path(directory, label, True)
    if complete:
        # Flush the success stamp before clearing the durable incomplete marker.
        fd = os.open(evidence_path(directory, label), os.O_RDONLY | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError('Unsafe clone success stamp.')
            os.fsync(fd)
        finally:
            os.close(fd)
        marker.unlink()
    else:
        atomic_json(marker, {'version': 1})
    sync_directory(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('target', 'begin', 'complete'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('labels', nargs='+')
    parser.add_argument('--interval', type=int, default=7)
    args = parser.parse_args()
    if args.operation != 'target':
        if len(args.labels) != 1:
            parser.error('Clone evidence takes exactly one label.')
        mark_clone(args.directory, args.labels[0], complete=args.operation == 'complete')
        return 0
    plan = rotation_plan(args.directory, args.labels, args.interval)
    if plan.target is None or plan.next_at > time.time():
        print(plan.message or 'Rotation interval has not elapsed since the last successful clone.', file=sys.stderr)
        return 3
    print(plan.target)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        print(f'Hotspare rotation refused: {exc}', file=sys.stderr)
        sys.exit(1)
