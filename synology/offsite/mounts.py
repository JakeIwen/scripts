"""Discover only exact mounted DSM USB filesystems, then pin their repository inode."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat

from offsite_config import MARKER, REPO_PATH, validate_marker

USB_TARGET = re.compile(r'/volumeUSB[0-9]+/usbshare[A-Za-z0-9_-]*\Z')


@dataclass(frozen=True)
class Drive:
    target: Path
    mount_identity: tuple
    marker: dict

    @property
    def identity(self):
        return (self.mount_identity, self.marker['repo_id'], self.marker['priority'])


def unescape(value):
    return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value)


def mounted_usb(text, pattern=USB_TARGET):
    mounts = []
    for line in text.splitlines():
        left, right = line.split(' - ', 1)
        fields, fs = left.split(), right.split()
        if len(fields) < 6 or len(fs) < 3:
            raise ValueError('Malformed mount inventory')
        target = unescape(fields[4])
        if not pattern.fullmatch(target):
            continue
        mounts.append((Path(target), (fields[0], fields[2], unescape(fs[1]), fs[0], fields[3],
                                      'rw' if 'rw' in fields[5].split(',') else 'ro')))
    if len({str(row[0]) for row in mounts}) != len(mounts) or len({row[1][1] for row in mounts}) != len(mounts):
        raise ValueError('Ambiguous USB mount inventory')
    return mounts


def marker_at(target):
    marker_path = target / MARKER
    try:
        fd = os.open(marker_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            raise ValueError('Unsafe USB marker')
        marker = json.load(source)
    validate_marker(marker, marker.get('label', '') if isinstance(marker, dict) else '')
    return marker


def discover(mountinfo=Path('/proc/self/mountinfo'), pattern=USB_TARGET):
    drives = []
    for target, identity in mounted_usb(mountinfo.read_text(), pattern):
        if target.resolve() != target or not target.is_dir():
            raise ValueError('Unsafe USB mount path')
        marker = marker_at(target)
        if marker is not None:
            # Do not reject or traverse an unrelated unmarked NTFS USB disk.
            # A marked seed, however, must be an exact writable ext4 root mount.
            if identity[3:] != ('ext4', '/', 'rw') or not identity[2].startswith('/dev/'):
                raise ValueError('Unsupported or unsafe marked USB filesystem')
            drives.append(Drive(target, identity, marker))
    if not drives:
        return None
    if len({row.marker['repo_id'] for row in drives}) != len(drives):
        raise ValueError('Duplicate marked repository identity')
    priority = max(row.marker['priority'] for row in drives)
    winners = [row for row in drives if row.marker['priority'] == priority]
    if len(winners) != 1:
        raise ValueError('Ambiguous highest-priority offsite drives')
    return winners[0]


def pin(drive):
    path = drive.target / REPO_PATH
    if path.resolve() != path:
        raise ValueError('Unsafe repository path')
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        device = os.fstat(fd).st_dev
        if '{}:{}'.format(os.major(device), os.minor(device)) != drive.mount_identity[1]:
            raise ValueError('Repository is not on the exact USB mount')
        for name in ('config', 'data', 'index', 'keys', 'locks', 'snapshots'):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            expected = stat.S_ISREG if name == 'config' else stat.S_ISDIR
            if not expected(info.st_mode) or info.st_dev != device:
                raise ValueError('Invalid seeded repository layout')
        # A seeded drive must not hide symlinked/nested paths behind the marker.
        for root, directories, files in os.walk('/proc/self/fd/{}'.format(fd), followlinks=False):
            for name in directories + files:
                info = (Path(root) / name).lstat()
                if info.st_dev != device or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    raise ValueError('Repository contains an unsafe entry')
        return fd
    except BaseException:
        os.close(fd)
        raise
