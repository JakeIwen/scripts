"""Replay mirror renames that apply or undo could not make while bigboi was unmounted.

The backup's media rsync has no move detection: a moved title whose mirror copy
still sits at its old path is deleted and copied again in full. The backup job
runs ``mirror-sync`` right before that rsync, with the mirror drive mounted.
"""
import os
from . import records, runs, settings
from .activity_log import log
from .busy import note, operation
from .locking import backup_sync_excluded, media_lock
from .paths import require_media
from .relocation import mirror_available, mirror_paths, rollback_paths


def pending(item):
    """True when this journal item still owes the mirror a rename."""
    if item.mirror_state == records.MIRROR_PENDING:
        return True
    # Journals from before mirror_state: a moved item whose mirror copy was
    # never renamed (drive unmounted, or no copy existed) gets one check.
    return (item.mirror_state is None and item.status == records.ITEM_MOVED
            and item.mirror_old is None)


def pending_items():
    return [(run, item) for run in runs.list_runs() for item in run.items if pending(item)]


def _settle(run, item):
    item.mirror_state = records.MIRROR_SETTLED
    runs.save(run)


def sync_item(run, item):
    """Rename one mirror copy to match the payload; True when a rename happened."""
    old, new = rollback_paths(item)
    if item.status == records.ITEM_MOVED:
        source, target = old, new
    elif item.status == records.ITEM_REVERTED and item.mirror_old is not None:
        source, target = new, old
    else:
        _settle(run, item)  # a failed item, or an undo whose mirror never moved
        return False
    # Only follow a payload that is still where this journal left it; anything
    # else moved it again since, and rsync reconciles the mirror from scratch.
    if not os.path.lexists(target) or os.path.lexists(source):
        _settle(run, item)
        return False
    mirror_from, mirror_to = mirror_paths(source, target)
    if item.status == records.ITEM_REVERTED and (item.mirror_old, item.mirror_new) != (
            str(mirror_to), str(mirror_from)):
        raise ValueError('invalid mirror journal paths')
    if not os.path.lexists(mirror_from) or os.path.lexists(mirror_to):
        _settle(run, item)  # no copy to move, or both exist and rsync drops the stale one
        return False
    if item.status == records.ITEM_MOVED:
        # Intent first: undo reverses the mirror only when these are recorded.
        item.mirror_old, item.mirror_new = str(mirror_from), str(mirror_to)
        runs.save(run)
    mirror_to.parent.mkdir(parents=True, exist_ok=True)
    os.rename(mirror_from, mirror_to)
    _settle(run, item)
    return True


def sync():
    if not settings.load().mirror_rename:
        return {'disabled': True, 'renamed': [], 'settled': 0, 'failed': []}
    with operation(records.JOB_MIRROR):
        # The backup runs this before taking its exclusive hold, so waiting here
        # only outlasts an organizer move already under way.
        with backup_sync_excluded(wait=True), media_lock():
            require_media()
            if not mirror_available():
                raise ValueError('mirror drive is not mounted; mirror renames stay pending')
            work = pending_items()
            result = {'disabled': False, 'renamed': [], 'settled': 0, 'failed': []}
            for index, (run, item) in enumerate(work):
                note(detail=item.name, done=index, total=len(work))
                try:
                    if sync_item(run, item):
                        result['renamed'].append(item.name)
                    else:
                        result['settled'] += 1
                except (OSError, ValueError) as error:
                    # Left pending: the next backup retries before its rsync.
                    result['failed'].append({'name': item.name, 'error': str(error)})
            log('mirror-sync renamed=%d settled=%d failed=%d'
                % (len(result['renamed']), result['settled'], len(result['failed'])))
            if result['failed']:
                result['error'] = '%d mirror renames failed' % len(result['failed'])
            return result
