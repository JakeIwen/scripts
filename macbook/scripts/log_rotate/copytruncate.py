"""Rotate one log in place: archive its bytes, then truncate the same inode.

launchd and cron keep the log open for appending, so the file must stay the same
inode. The archive is written completely and fsynced before the original is touched;
any failure up to then leaves the original as it was.
"""
from __future__ import annotations

import gzip
import os
import stat
import tempfile
from typing import Optional, Tuple

from . import archives, eligibility, state
from .archives import archive_path
from .records import EXIT_ERROR, LogRotateError

CHUNK_BYTES = 1 << 20
# Bound the work, but never truncate bytes this invocation has not copied.
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
    if info.st_nlink > 1:
        os.close(descriptor)
        raise _fail("refused: " + eligibility.HARD_LINK_NOTE)
    # The path could have been swapped for another file between the check and the open.
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != eligibility.current_uid()
            or (info.st_dev, info.st_ino) != (inspection.info.st_dev, inspection.info.st_ino)):
        os.close(descriptor)
        raise _fail("file changed while opening")
    return descriptor, info


def _copy_pass(source: int, output, start: int, end: int, compress: bool) -> int:
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
    return offset


def _write_archive(source: int, descriptor: int, compress: bool) -> int:
    """Copy all observed bytes or refuse; the caller owns the exclusive temporary."""
    with os.fdopen(descriptor, "wb", closefd=False) as output:
        copied = 0
        for _ in range(MAX_PASSES):
            size = os.fstat(source).st_size
            copied = _copy_pass(source, output, copied, size, compress)
            if copied < size:
                raise _fail("grows faster than it can be copied")
            if copied == os.fstat(source).st_size:
                return copied
        raise _fail("grows faster than it can be copied")


def _shift_archives(existing: list, directory: str, base: str, keep: int,
                    ledger: dict, home: str) -> None:
    for item in existing:
        archives.require_owned(item.path, ledger)
        if item.index + 1 > keep:
            os.unlink(item.path)
        else:
            destination = archive_path(directory, base, item.index + 1, item.gzipped)
            archives.publish(item.path, destination)
            ledger[destination] = archives.identity(destination)
        ledger.pop(item.path, None)
        state.write_json(home, state.ARCHIVES_FILE, ledger)


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


def _check_temporary(path: str, descriptor: int) -> bool:
    """Never publish or clean up a replacement for the temporary we created."""
    try:
        current, created = os.lstat(path), os.fstat(descriptor)
    except FileNotFoundError:
        return False
    return (stat.S_ISREG(current.st_mode)
            and (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino))


def rotate_file(path: str, home: str, keep: int, compress: bool) -> Tuple[int, Optional[str]]:
    """Archive and truncate `path`; returns (bytes before, archive or None for keep 0)."""
    descriptor, info = _open_checked(path, home)
    directory, base = os.path.split(path)
    temporary = None
    temporary_fd = None
    archive = None
    try:
        ledger = state.read_json(home, state.ARCHIVES_FILE)
        existing = archives.rotation_plan(directory, base, keep, compress, ledger)
        if keep > 0:
            _require_space(directory, info.st_size)
            temporary_fd, temporary = tempfile.mkstemp(dir=directory, prefix=".{}.rotating.".format(base))
            try:
                copied = _write_archive(descriptor, temporary_fd, compress)
                if copied != os.fstat(descriptor).st_size:
                    raise _fail("grows faster than it can be copied")
                if not _check_temporary(temporary, temporary_fd):
                    raise _fail("foreign file at {}".format(temporary))
                _shift_archives(existing, directory, base, keep, ledger, home)
                archive = archive_path(directory, base, 1, compress)
                archives.publish(temporary, archive)
                temporary = None
                ledger[archive] = archives.identity(archive)
                state.write_json(home, state.ARCHIVES_FILE, ledger)
            except OSError as error:
                raise _fail("archive failed: {}".format(error.strerror or error)) from None
            _sync_directory(directory)
            if copied != os.fstat(descriptor).st_size:
                raise _fail("grows faster than it can be copied")
        else:
            _shift_archives(existing, directory, base, 0, ledger, home)
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
        return info.st_size, archive
    except OSError as error:
        raise _fail("rotation failed: {}".format(error.strerror or error)) from None
    finally:
        os.close(descriptor)
        if temporary_fd is not None:
            try:
                if temporary is not None and _check_temporary(temporary, temporary_fd):
                    os.unlink(temporary)
            except OSError:
                pass
            finally:
                os.close(temporary_fd)
