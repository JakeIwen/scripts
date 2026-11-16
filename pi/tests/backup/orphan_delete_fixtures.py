"""A fake `rclone delete --files-from-raw` for the Time Machine retention fixture.

It deletes from the fixture's `remote_objects` and reports each deletion
through the run's `on_completed`, like rclone's "Deleted" records. Optional
fixture attributes steer it: `fail_deletes_after` / `fail_after_server_delete`
(cumulative over `fixture.deleted`, as before batching), `noop_deletes`
(reported but left in place), `silent_deletes` (removed with no record) and
`on_batch_delete(call_index, names, on_completed)`, run after the call.
"""
from pathlib import Path
import stat

import icloud_backup as cloud
import time_machine_store as store


def batch_delete(fixture, path, *args, **kwargs):
    options = dict(zip(args[::2], args[1::2]))
    list_path = Path(options['--files-from-raw'])
    fixture.assertEqual(stat.S_IMODE(list_path.stat().st_mode), 0o600)
    names = list_path.read_text().splitlines()
    fixture.assertEqual(int(options['--max-delete']), len(names))
    fixture.assertEqual(options['--log-level'], 'INFO')
    fixture.assertTrue(all(store.DIGEST.fullmatch(name) for name in names))
    calls = fixture.__dict__.setdefault('delete_lists', [])
    calls.append(names)
    for name in names:
        if name not in fixture.remote_objects:
            continue  # rclone's listing no longer has it: skipped, exit 0
        if fixture.fail_deletes_after is not None and len(fixture.deleted) >= fixture.fail_deletes_after:
            fixture.fail_deletes_after = None
            if fixture.fail_after_server_delete:
                # The delete reached the server; only the client saw the failure.
                fixture.remote_objects.discard(name)
            raise cloud.Deferred('Waiting for a verified, permitted internet route.')
        fixture.deleted.append(name)
        if name not in getattr(fixture, 'noop_deletes', ()):
            fixture.remote_objects.discard(name)
        if name not in getattr(fixture, 'silent_deletes', ()):
            kwargs['on_completed'](name)
    hook = getattr(fixture, 'on_batch_delete', None)
    if hook is not None:
        hook(len(calls), names, kwargs['on_completed'])
    return ''
