"""Rotate one log in place: archive its bytes, then truncate the same inode.

launchd and cron keep the log open for appending, so the file must stay the same
inode. The archive is written completely and fsynced before the original is touched;
any failure up to then leaves the original as it was.
"""
from __future__ import annotations

import gzip
import os
import stat
from typing import Optional, Tuple

from . import eligibility
from .archives import archive_path, scan_directory
from .records import EXIT_ERROR, LogRotateError

CHUNK_BYTES = 1 << 20
# A log still growing after this many catch-up passes is truncated anyway: it would
# otherwise never rotate, and copy-truncate cannot be lossless against a live writer.
MAX_PASSES = 8
SPACE_MARGIN_BYTES = 1 << 20


def _fail(message: str) -> LogRotateError:
    return LogRotateError(EXIT_ERROR, message)


def _open_checked(path: str, home: str) -> Tuple[int, os.stat_result]:
    inspection = eligibility.inspect(path, home)
    if inspection.refusal is not None:
        raise _fail("refused: {}".format(inspection.refusal))
    if inspection.info is None:
        raise _fail("file is missing")
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    except OSError as error:
        raise _fail("cannot open for rotation: {}".format(error.strerror or error)) from None
    info = os.fstat(descriptor)
    # The path could have been swapped for another file between the check and the open.
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != eligibility.current_uid()
            or (info.st_dev, info.st_ino) != (inspection.info.st_dev, inspection.info.st_ino)):
        os.close(descriptor)
        raise _fail("file changed while opening")
    return descriptor, info


def _copy_pass(source: int, output, start: int, end: int, compress: bool) -> None:
    sink = gzip.GzipFile(fileobj=output, mode="wb", mtime=0) if compress else output
    offset = start
    while offset < end:
        chunk = os.pread(source, min(CHUNK_BYTES, end - offset), offset)
        if not chunk:
            break
        sink.write(chunk)
        offset += len(chunk)
    if compress:
        sink.close()  # a gzip member per pass; the fileobj stays open
    output.flush()
    os.fsync(output.fileno())


def _write_archive(source: int, temporary: str, compress: bool) -> None:
    """Copy the log into `temporary`, catching up with writers until it stops growing."""
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        copied = 0
        for _ in range(MAX_PASSES):
            size = os.fstat(source).st_size
            if size <= copied:
                return
            _copy_pass(source, output, copied, size, compress)
            copied = size


def _shift_archives(directory: str, base: str, keep: int) -> None:
    existing = scan_directory(directory).get(base, [])
    for item in sorted(existing, key=lambda archive: -archive.index):
        if item.index + 1 > keep:
            os.unlink(item.path)
        else:
            os.rename(item.path, archive_path(directory, base, item.index + 1, item.gzipped))


def _sync_directory(directory: str) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_space(directory: str, size: int) -> None:
    status = os.statvfs(directory)
    if status.f_bavail * status.f_frsize < size + SPACE_MARGIN_BYTES:
        raise _fail("not enough free space for the archive")


def rotate_file(path: str, home: str, keep: int, compress: bool) -> Tuple[int, Optional[str]]:
    """Archive and truncate `path`; returns (bytes before, archive path or None when keep is 0)."""
    descriptor, info = _open_checked(path, home)
    directory, base = os.path.split(path)
    temporary = os.path.join(directory, ".{}.rotating.{}".format(base, os.getpid()))
    archive = None
    try:
        if keep > 0:
            _require_space(directory, info.st_size)
            try:
                _write_archive(descriptor, temporary, compress)
                _shift_archives(directory, base, keep)
                archive = archive_path(directory, base, 1, compress)
                os.rename(temporary, archive)
            except OSError as error:
                raise _fail("archive failed: {}".format(error.strerror or error)) from None
            _sync_directory(directory)
        else:
            _shift_archives(directory, base, 0)
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
        return info.st_size, archive
    except OSError as error:
        raise _fail("rotation failed: {}".format(error.strerror or error)) from None
    finally:
        os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        except OSError:
            pass
