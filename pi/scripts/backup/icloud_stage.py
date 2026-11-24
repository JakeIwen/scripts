"""Rejecting a staged repository copy that failed its check or a read, and when to copy again.

A pending stage is resumed with rsync, which trusts size and mtime, so a staged
file on a bad sector is never recopied and every later check fails the same
way. A stage that fails its Borg check or cannot be read while hashing is
therefore discarded and copied again from the live repository. Only an
unpublished stage (no payload/manifest.json) is discarded: nothing of it has
been uploaded, so it is private to this job.

Two rejections in a row may mean the live repository or the disk is at fault,
not one bad copy, so from the second on a new copy waits for a newer local Borg
backup (its success stamp) instead of rereading the whole repository hourly.
"""
import errno
import shutil
import time

from icloud_status import BackupFailure

# From the second consecutive rejection, copy again only after a newer Borg stamp.
IMMEDIATE_RESTAGES = 1
WAIT_MESSAGE = 'a discarded staged copy waits for the next local Borg backup before copying again'


class StageRejected(RuntimeError):
    """The staged copy is unusable; weekly() discards it before reporting the failure."""

    def __init__(self, message, failure_code, detail=''):
        super().__init__(message)
        self.failure_code = failure_code
        self.detail = detail


def disk_read_error(exc):
    """An EIO from Python, or a command whose stderr reported one."""
    return (isinstance(exc, OSError) and exc.errno == errno.EIO) or getattr(exc, 'disk_io', False) is True


def rejection(exc):
    """The StageRejected for a failure of the stage's check or hashing, or None to propagate exc.

    Only a finished `borg check` that reported a problem, or an EIO reading a
    staged file, condemns the copy. Stops (Deferred), watchdog kills, a missing
    disk and safety refusals say nothing about the copy and leave it in place.
    """
    if disk_read_error(exc) and (isinstance(exc, OSError) or getattr(exc, 'command', None) == ('borg', 'check')):
        return StageRejected('staged repository copy could not be read and was discarded',
                             BackupFailure.STAGE_UNREADABLE, getattr(exc, 'detail', ''))
    if getattr(exc, 'command', None) == ('borg', 'check'):
        return StageRejected('staged repository copy failed its Borg check and was discarded',
                             BackupFailure.STAGE_CHECK_FAILED, getattr(exc, 'detail', ''))
    return None


def discard(generation):
    """Delete one unpublished staging generation; the caller has verified its path and mount."""
    if (generation / 'payload' / 'manifest.json').exists() or (generation / '_COMPLETE.json').exists():
        raise RuntimeError('refusing to discard a staged copy that was published for upload')
    if generation.exists():
        shutil.rmtree(generation)


def record(state, generation, failure_code, borg_stamp_mtime, now=None):
    """Remember the rejection; success clears it (see clear)."""
    previous = state.get('stage_rejected')
    count = previous.get('count') if isinstance(previous, dict) else 0
    state['stage_rejected'] = {
        'generation': generation, 'failure_code': failure_code,
        'rejected_at': time.time() if now is None else now,
        'borg_stamp_mtime': borg_stamp_mtime,
        'count': (count if type(count) is int and count > 0 else 0) + 1}


def must_wait(state, read_borg_stamp):
    """True when a new copy should wait for a Borg backup newer than the last rejected copy's.

    read_borg_stamp() returns the stamp's mtime or None; it is read only after a rejection.
    """
    record = state.get('stage_rejected')
    if not isinstance(record, dict):
        return False
    count, stamp = record.get('count'), record.get('borg_stamp_mtime')
    if type(count) is not int or count <= IMMEDIATE_RESTAGES:
        return False
    if not isinstance(stamp, (int, float)) or isinstance(stamp, bool):
        return False
    current = read_borg_stamp()
    return current is None or current <= stamp


def clear(state):
    state.pop('stage_rejected', None)
