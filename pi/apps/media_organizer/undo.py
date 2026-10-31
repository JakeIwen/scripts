"""Reverse a run without guessing after another process changes its payloads."""
from . import records, runs, link_rebuild
from .busy import note, operation
from .locking import backup_sync_excluded, media_lock
from .paths import require_media
from .qbittorrent_client import Client
from .relocation import undo_payload, undo_mirror, verify_undo, PENDING, UNDO_PENDING
from .storage import now


def undo_run(run, client=None):
    with operation(records.JOB_UNDO):
        with backup_sync_excluded(), media_lock():
            require_media()
            run = runs.load(run.id)
            if not run.undoable or run.undone_at:
                raise ValueError('run is not undoable')
            client = client or Client()
            pending_items = [item for item in reversed(run.items)
                             if item.status == records.ITEM_MOVED
                             or (item.error or '').startswith(PENDING) or item.error == UNDO_PENDING]
            for index, item in enumerate(pending_items):
                note(detail=item.name, done=index, total=len(pending_items))
                try:
                    recovering = item.error == UNDO_PENDING
                    verify_undo(item, client, recovering)
                    # Keep both reverse renames recoverable if the process dies
                    # between restoring the payload and restoring its mirror.
                    item.error = UNDO_PENDING
                    runs.save(run)
                    old, new = undo_payload(item, client, recovering)
                    undo_mirror(item, old, new)
                    item.status, item.error = records.ITEM_REVERTED, None
                except (OSError, ValueError, RuntimeError) as error:
                    item.status, item.error = records.ITEM_FAILED, str(error)
                runs.save(run)
            note(detail='finishing', done=len(pending_items), total=len(pending_items))
            run.undone_at, run.undoable = now(), False
            runs.save(run)
        run.alias_status = link_rebuild.schedule()
        runs.save(run)
        return run
