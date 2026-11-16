"""Batched deletion of unreferenced Time Machine cloud objects.

One `rclone delete --files-from-raw` call per chunk replaces a `deletefile`
per object: on iclouddrive each deletefile re-lists the whole objects folder
three times (~90 s), while one delete lists it once and then trashes every
listed object. The caller (time_machine_icloud.delete_unreferenced) owns the
listing that chose the names and the guarded `network` command runner.

Safety layers, all fail closed:
- rclone's files-from filter matches exact names only, and `--max-delete`
  caps each call at the names still unconfirmed in its list;
- a "Deleted" record for a name outside the chunk stops cleanup at once
  (`UnlistedDeletion`), also when it is only read after the client exited;
- after every chunk a fresh listing must show every name processed so far
  gone and every other object of the first listing still present
  (`InventoryMismatch`). That baseline lives only in this worker run: a
  disappearance between a chunk's delete and its check listing in a run that
  dies or defers there is not compared by the next run.
"""
import os
import time

import icloud_uplink_hold as uplink_hold
import time_machine_store as store

# Two folder listings (rclone's and the check, ~30 s each on 13k objects) per
# chunk; tune only through a reviewed change.
CHUNK_OBJECTS = 100
LIST_NAME = 'delete-objects.txt'


class UnlistedDeletion(RuntimeError):
    """rclone reported deleting an object outside the list it was given."""


class InventoryMismatch(RuntimeError):
    """A check listing contradicts the deletions made so far in this run."""


def delete_args(objects_remote, list_path, count):
    """The rclone argv after the program for one chunk (or its restart)."""
    return ['delete', objects_remote, '--files-from-raw', str(list_path),
            '--max-delete', str(count), '--log-level', 'INFO']


def write_list(list_path, names):
    fd = os.open(list_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(''.join(name + '\n' for name in names))


class Chunk:
    """One rclone delete call's names and confirmations, across its restarts."""

    def __init__(self, objects_remote, names, sizes, list_path, progress):
        self.objects_remote, self.names, self.sizes = objects_remote, names, sizes
        self.list_path, self.progress = list_path, progress
        self.members = frozenset(names)
        self.confirmed = set()

    def deleted(self, name):
        """The client's "Deleted" record for one object."""
        if name not in self.members:
            raise UnlistedDeletion('rclone reported deleting an unlisted cloud object; cleanup stopped')
        if name in self.confirmed:
            return
        self.confirmed.add(name)
        uplink_hold.note_advancement()
        self.progress.deleted(self.sizes[name])

    def unconfirmed(self):
        return [name for name in self.names if name not in self.confirmed]

    def restart(self, args):
        """Before a restart: list and cap only the names not yet confirmed."""
        remaining = self.unconfirmed()
        write_list(self.list_path, remaining)
        return [args[0], *delete_args(self.objects_remote, self.list_path, len(remaining))]


def check_listing(rows, names, processed, baseline, sizes, progress):
    """Settle counts from a fresh listing, then refuse any contradiction."""
    present = set(rows)
    remaining = [name for name in names if name in present]
    progress.settle(len(remaining), sum(sizes[name] for name in remaining))
    survivors = processed & present
    if survivors:
        raise InventoryMismatch(f'{len(survivors)} cloud objects still listed after batch delete '
                                '(failed removal or inventory inconsistency); cleanup stopped')
    vanished = baseline - set(names) - present
    if vanished:
        raise InventoryMismatch(f'{len(vanished)} cloud objects outside the delete list are no longer listed '
                                '(inventory inconsistency or unexpected disappearance); cleanup stopped')
    uplink_hold.note_advancement()  # every processed name is confirmed gone


def delete_orphans(cfg, objects_remote, names, sizes, baseline, *, network, relist, list_path, progress,
                   log=print, clock=time.monotonic):
    """Delete `names` (sorted, unreferenced, all in `baseline`, the listing that chose them).

    `network(cfg, *argv, **run_kwargs)` runs one guarded rclone command;
    `relist()` returns a fresh {name: row} listing of the objects folder.
    """
    if not names:
        return
    if any(not store.DIGEST.fullmatch(name) or name not in baseline for name in names):
        raise ValueError('invalid object deletion list; cleanup refused')
    chunks = [names[start:start + CHUNK_OBJECTS] for start in range(0, len(names), CHUNK_OBJECTS)]
    processed = set()
    try:
        for index, chunk_names in enumerate(chunks, 1):
            chunk = Chunk(objects_remote, chunk_names, sizes, list_path, progress)
            log(f'Retention: deleting {len(chunk_names):,} objects in one rclone call '
                f'(chunk {index}/{len(chunks)})', flush=True)
            started = clock()
            write_list(list_path, chunk_names)
            network(cfg, *delete_args(objects_remote, list_path, len(chunk_names)),
                    on_completed=chunk.deleted, progress=progress.tick, restart_args=chunk.restart)
            deleted_at = clock()
            processed.update(chunk_names)
            check_listing(relist(), names, processed, baseline, sizes, progress)
            log(f'Retention: chunk {index}/{len(chunks)} done in {deleted_at - started:.0f}s, '
                f'{len(chunk.confirmed):,} deletions reported, check listing {clock() - deleted_at:.0f}s',
                flush=True)
    finally:
        list_path.unlink(missing_ok=True)
