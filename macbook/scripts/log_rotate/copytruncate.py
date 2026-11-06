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
import zlib
from typing import List, Optional, Tuple

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
    # Position closes gaps left by an interrupted shift without aging survivors twice.
    for position, item in reversed(list(enumerate(existing, 1))):
        archives.require_owned(item.path, ledger)
        if position + 1 > keep:
            os.unlink(item.path)
        else:
            destination = archive_path(directory, base, position + 1, item.gzipped)
            if destination == item.path:
                continue
            _publish_recorded(item.path, destination, ledger, home)
        ledger.pop(item.path, None)
        state.write_json(home, state.ARCHIVES_FILE, ledger)


def _publish_recorded(source: str, destination: str, ledger: dict, home: str,
                      pending: Optional[dict] = None) -> None:
    # Record before linking: a failed write never leaves a published archive foreign.
    ledger[destination] = {**archives.identity(source), "path": destination}
    if pending is not None:
        ledger[destination]["pending_truncate"] = pending
    state.write_json(home, state.ARCHIVES_FILE, ledger)
    archives.publish(source, destination)


def _check_source(path: str, descriptor: int, original: os.stat_result, copied: Optional[int]) -> None:
    current, opened = os.lstat(path), os.fstat(descriptor)
    if opened.st_nlink != 1:
        raise _fail("refused: " + eligibility.HARD_LINK_NOTE)
    if ((current.st_dev, current.st_ino) != (original.st_dev, original.st_ino)
            or (opened.st_dev, opened.st_ino) != (original.st_dev, original.st_ino)):
        raise _fail("file changed before publication")
    if copied is not None and copied != opened.st_size:
        raise _fail("grows faster than it can be copied")


def _catch_up(source: int, destination: int, copied: int, compress: bool) -> int:
    # A failed member must not corrupt an already published, recorded archive.
    size = os.fstat(source).st_size
    if size <= copied:
        return copied
    recorded_size = os.fstat(destination).st_size
    try:
        with os.fdopen(destination, "ab", closefd=False) as output:
            offset = _copy_pass(source, output, copied, size, compress)
            if offset != size:
                raise _fail("grows faster than it can be copied")
        return offset
    except Exception:
        os.ftruncate(destination, recorded_size)
        os.fsync(destination)
        raise


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


def _finish_pending(source: int, output: int, archive: str, copied: int,
                    compress: bool, ledger: dict, home: str) -> None:
    copied = _catch_up(source, output, copied, compress)
    pending = {**ledger[archive]["pending_truncate"], "source_size": copied}
    ledger[archive] = {**archives.identity(archive, os.fstat(output)), "pending_truncate": pending}
    state.write_json(home, state.ARCHIVES_FILE, ledger)
    # No durable write between the last catch-up and truncation: record writers may append.
    _catch_up(source, output, copied, compress)
    os.ftruncate(source, 0)
    os.fsync(source)
    ledger[archive] = archives.identity(archive, os.fstat(output))
    state.write_json(home, state.ARCHIVES_FILE, ledger)


def _matches_prefix(source: int, archive: str, copied: int, compress: bool) -> bool:
    reader = gzip.open if compress else open
    with reader(archive, "rb") as recorded:
        offset = 0
        while offset < copied:
            # read1 bounds decompression too: a damaged late member is not prefix damage.
            chunk = recorded.read1(min(CHUNK_BYTES, copied - offset))
            if not chunk:
                raise EOFError("pending archive is shorter than its recorded source")
            if chunk != os.pread(source, len(chunk), offset):
                return False
            offset += len(chunk)
    return True


def _keep_pending(archive: str, ledger: dict, home: str, warnings: List[str], reason: str) -> None:
    # Uncertainty cannot prove the source still holds these bytes; retain this generation.
    archives.require_owned(archive, ledger)
    ledger[archive] = archives.identity(archive)
    state.write_json(home, state.ARCHIVES_FILE, ledger)
    warnings.append(reason)


def _resume_pending(source: int, info: os.stat_result, existing: list,
                    ledger: dict, home: str, warnings: List[str]) -> Optional[str]:
    for item in list(existing):
        pending = ledger[item.path].get("pending_truncate")
        if not isinstance(pending, dict):
            continue
        original = pending["source"]
        if (info.st_ino, getattr(info, "st_birthtime", None)) != (original["st_ino"], original["st_birthtime"]):
            # The archive still belongs to the old inode; the replacement has its own bytes.
            ledger[item.path] = archives.identity(item.path)
            state.write_json(home, state.ARCHIVES_FILE, ledger)
            continue
        copied = pending["source_size"]
        try:
            matches = info.st_size >= copied and _matches_prefix(source, item.path, copied, item.gzipped)
        except (OSError, EOFError, gzip.BadGzipFile, zlib.error):
            _keep_pending(item.path, ledger, home, warnings, "pending archive unreadable; kept")
            continue
        if not matches:
            _keep_pending(item.path, ledger, home, warnings,
                          "pending archive prefix differs from source; kept")
            continue
        output = os.open(item.path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            archives.require_owned(item.path, ledger)
            if archives.identity(item.path) != archives.identity(item.path, os.fstat(output)):
                raise _fail("pending archive changed while opening")
            # Discard only an uncommitted catch-up; its bytes still exist in the source.
            os.ftruncate(output, ledger[item.path]["size"])
            os.fsync(output)
            _finish_pending(source, output, item.path, copied, item.gzipped, ledger, home)
        finally:
            os.close(output)
        return item.path
    return None


def rotate_file(path: str, home: str, keep: int, compress: bool,
                warnings: Optional[List[str]] = None) -> Tuple[int, Optional[str]]:
    """Archive and truncate `path`; returns (bytes before, archive or None for keep 0)."""
    descriptor, info = _open_checked(path, home)
    directory, base = os.path.split(path)
    temporary = None
    temporary_fd = None
    try:
        ledger = archives.prune(home)
        existing = archives.rotation_plan(directory, base, keep, compress, ledger)
        existing = archives.unique_archives(existing, ledger, home)
        resumed = _resume_pending(descriptor, info, existing, ledger, home,
                                  warnings if warnings is not None else [])
        if resumed is not None:
            return info.st_size, resumed
        if keep == 0:
            _check_source(path, descriptor, info, None)
            _shift_archives(existing, directory, base, 0, ledger, home)
            os.ftruncate(descriptor, 0)
            os.fsync(descriptor)
            return info.st_size, None
        _require_space(directory, info.st_size)
        temporary_fd, temporary = tempfile.mkstemp(dir=directory, prefix=".{}.rotating.".format(base))
        try:
            copied = _write_archive(descriptor, temporary_fd, compress)
            if not _check_temporary(temporary, temporary_fd):
                raise _fail("foreign file at {}".format(temporary))
            _check_source(path, descriptor, info, copied)
            _shift_archives(existing, directory, base, keep, ledger, home)
            archive = archive_path(directory, base, 1, compress)
            pending = {"source": archives.identity(path, info), "source_size": copied}
            _publish_recorded(temporary, archive, ledger, home, pending)
            temporary = None
        except OSError as error:
            raise _fail("archive failed: {}".format(error.strerror or error)) from None
        _sync_directory(directory)
        _finish_pending(descriptor, temporary_fd, archive, copied, compress, ledger, home)
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
