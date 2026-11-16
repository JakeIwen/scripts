"""Batched orphan deletion: worker runs against a fake cloud, and real rclone semantics."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import icloud_backup as cloud
from icloud_progress import RcloneStats
import time_machine_orphan_delete as orphan_delete
import time_machine_store as store
from pi.tests.backup import test_time_machine_icloud as tm
from pi.tests.backup.test_time_machine_icloud import GEN, job


class BatchDeleteRunTests(tm.RetentionFixture):
    """Five orphans in chunks of two: the worker's whole cleanup through job.main()."""

    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.object(orphan_delete, 'CHUNK_OBJECTS', 2))
        # The weekly leftover audit lists the store too; keep it out of the listing count.
        self.enterContext(mock.patch.object(cloud, 'orphan_audit_due', return_value=False))
        extra = {hashlib.sha256(bytes([n])).hexdigest() for n in (3, 4)}
        for digest in extra:
            (self.local / 'objects' / digest).write_bytes(b'stale')
        self.orphans |= extra
        self.remote_objects |= extra
        self.listings = 0
        self.list_path = job.DELETE_LIST_DIR / orphan_delete.LIST_NAME

    def inventory(self, cfg, path, progress=None):
        self.listings += 1
        return super().inventory(cfg, path, progress)

    def progress(self):
        return store.read_json(self.state_dir / 'state.json')['retention_progress']

    def run_failing(self):
        self.assertEqual(job.main(), 1)
        return store.read_json(self.state_dir / 'state.json')

    def assert_stopped_with_alert(self, state):
        self.assertEqual((state['phase'], state['failure_code'], state.get('retry_at')), ('error', None, None))
        self.assertNotIn('retention_done_for', state)
        self.assertIn('needs attention', self.notify.call_args.args[1])
        self.assertTrue(self.orphans <= self.local_objects())  # the local unlink step never ran

    def test_one_delete_per_chunk_lists_only_unreferenced_names(self):
        self.write_state()
        state = self.run_worker()
        self.assertEqual(state['retention_done_for'], GEN)
        self.assertEqual([len(names) for names in self.delete_lists], [2, 2, 1])
        listed = [name for names in self.delete_lists for name in names]
        self.assertEqual((len(listed), set(listed)), (len(self.orphans), self.orphans))
        self.assertFalse(set(listed) & self.kept)
        self.assertNotIn('deletefile', self.calls)
        self.assertEqual(self.listings, 1 + 3)  # the first listing, then one check per chunk
        self.assertEqual(self.remote_objects, self.kept)
        self.assertFalse(self.list_path.exists())
        done = state['retention_progress']
        self.assertEqual((done['planned_objects'], done['deleted_objects'], done['remaining_objects']), (5, 5, 0))

    def test_an_object_only_an_unfinished_generation_references_is_kept(self):
        keep = sorted(self.orphans)[0]
        files = {**self.manifest['files'], 'bands/unfinished': {'sha256': keep, 'bytes': 5}}
        # An interrupted upload's manifest without _COMPLETE.json still protects its objects.
        self.documents[self.UNFINISHED] = {'manifest.json': json.dumps(
            {**self.manifest, 'generation': self.UNFINISHED, 'files': files})}
        self.write_state()
        self.assertEqual(self.run_worker()['retention_done_for'], GEN)
        self.assertNotIn(keep, [name for names in self.delete_lists for name in names])
        self.assertEqual(self.remote_objects, self.kept | {keep})

    def test_a_reported_deletion_outside_the_chunk_stops_cleanup(self):
        kept = sorted(self.kept)[0]
        self.on_batch_delete = lambda call, names, completed: completed(kept)
        self.write_state()
        state = self.run_failing()
        self.assert_stopped_with_alert(state)
        self.assertEqual(len(self.delete_lists), 1)
        self.assertFalse(self.list_path.exists())

    def test_a_kept_object_missing_from_a_check_listing_stops_cleanup(self):
        kept = sorted(self.kept)[0]
        self.on_batch_delete = lambda call, names, completed: self.remote_objects.discard(kept)
        self.write_state()
        self.assert_stopped_with_alert(self.run_failing())
        self.assertEqual(len(self.delete_lists), 1)

    def test_a_reported_delete_that_left_the_object_settles_counts_then_fails(self):
        survivor = sorted(self.orphans)[0]
        self.noop_deletes = {survivor}
        self.write_state()
        state = self.run_failing()
        self.assert_stopped_with_alert(state)
        counts = state['retention_progress']
        self.assertEqual((counts['planned_objects'], counts['remaining_objects'], counts['deleted_objects']),
                         (5, 4, 1))  # the survivor plus the three never attempted, not a false zero

    def test_an_earlier_chunks_object_reappearing_later_fails(self):
        first = sorted(self.orphans)[0]
        self.on_batch_delete = lambda call, names, completed: call == 2 and self.remote_objects.add(first)
        self.write_state()
        state = self.run_failing()
        self.assert_stopped_with_alert(state)
        self.assertEqual(len(self.delete_lists), 2)
        self.assertEqual(state['retention_progress']['remaining_objects'], 2)  # the reappeared one and chunk 3

    def test_unreported_deletions_settle_from_the_listing_and_count_as_advancement(self):
        self.silent_deletes = set(self.orphans)
        self.write_state(uplink_quick_retries=2)
        state = self.run_worker()
        self.assertEqual(state['retention_done_for'], GEN)
        self.assertEqual(state['retention_progress']['deleted_objects'], 5)
        self.assertEqual(state['uplink_quick_retries'], 0)

    def test_the_list_file_is_private_holds_the_chunk_and_is_removed_after_a_failure(self):
        seen = []
        self.on_batch_delete = lambda call, names, completed: seen.append(names)
        self.fail_deletes_after = 3
        self.write_state()
        self.assertEqual(self.run_worker()['phase'], 'deferred')
        self.assertEqual([len(names) for names in seen], [2])  # chunk 2 failed before its hook
        self.assertFalse(self.list_path.exists())

    def test_nothing_unreferenced_runs_no_delete_and_no_check_listing(self):
        self.remote_objects -= self.orphans
        self.write_state()
        state = self.run_worker()
        self.assertEqual(state['retention_done_for'], GEN)
        self.assertNotIn('delete', self.calls)
        self.assertEqual(self.listings, 1)


class DeleteOrphansTests(unittest.TestCase):
    """delete_orphans directly: input validation and restart argv."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.list_path = Path(temp.name) / orphan_delete.LIST_NAME
        self.progress = mock.Mock()

    def test_refuses_names_that_are_not_digests_or_not_from_the_listing(self):
        good = hashlib.sha256(b'x').hexdigest()
        for names, baseline in ((['a/b'], {'a/b'}), ([good], set()), ([good + '\n'], {good + '\n'})):
            with self.assertRaises(ValueError):
                orphan_delete.delete_orphans({}, 'r:objects', names, {}, baseline, network=mock.Mock(),
                                             relist=mock.Mock(), list_path=self.list_path, progress=self.progress)

    def test_a_restart_lists_and_caps_only_unconfirmed_names(self):
        names = sorted(hashlib.sha256(bytes([n])).hexdigest() for n in range(3))
        chunk = orphan_delete.Chunk('r:objects', names, dict.fromkeys(names, 1), self.list_path, self.progress)
        with mock.patch.object(orphan_delete.uplink_hold, 'note_advancement'):
            chunk.deleted(names[1])
            chunk.deleted(names[1])
        args = chunk.restart(['/usr/local/bin/rclone', *orphan_delete.delete_args('r:objects', self.list_path, 3)])
        self.assertEqual(args, ['/usr/local/bin/rclone', 'delete', 'r:objects', '--files-from-raw',
                                str(self.list_path), '--max-delete', '2', '--log-level', 'INFO'])
        self.assertEqual(self.list_path.read_text().splitlines(), [names[0], names[2]])
        self.progress.deleted.assert_called_once_with(1)
        with self.assertRaises(orphan_delete.UnlistedDeletion):
            chunk.deleted('f' * 64)


@unittest.skipUnless(shutil.which('rclone'), 'real rclone runs in the Linux lane and on the Pi')
class RealRcloneDeleteTests(unittest.TestCase):
    """The worker's exact delete argv against a local folder with the Pi's rclone version."""

    FLAGS = ['--checkers', '2', '--retries', '1', '--low-level-retries', '2', '--stats', '5s',
             '--stats-log-level', 'NOTICE', '--use-json-log', '--config', '/dev/null']

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.objects = self.root / 'objects'
        (self.objects / 'sub').mkdir(parents=True)
        self.a, self.b, self.c = (hashlib.sha256(bytes([n])).hexdigest() for n in range(3))
        for name in (self.a, self.b, self.c, 'e1', 'sp ace', self.a + '.x', 'sub/' + self.a):
            (self.objects / name).write_bytes(b'x')
        self.list_path = self.root / 'list.txt'

    def delete(self, lines, count=None):
        self.list_path.write_text(''.join(line + '\n' for line in lines))
        args = orphan_delete.delete_args(str(self.objects), self.list_path, len(lines) if count is None else count)
        with tempfile.TemporaryFile() as log:
            rc = subprocess.run([shutil.which('rclone'), *args, *self.FLAGS], stderr=log,
                                stdout=subprocess.DEVNULL, timeout=60).returncode
            events = []
            RcloneStats(events.append, 'delete').drain(log.fileno())
        return rc, events

    def present(self):
        return {str(p.relative_to(self.objects)) for p in self.objects.rglob('*') if p.is_file()}

    def test_only_exact_listed_files_are_deleted_and_reported(self):
        missing = 'f' * 64
        rc, events = self.delete([self.a, self.b, missing, 'e*', 'sub', '/' + self.c])
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(events), sorted([self.a, self.b, self.c]))
        self.assertEqual(self.present(), {'e1', 'sp ace', self.a + '.x', 'sub/' + self.a})
        self.assertEqual(self.delete([self.a, self.b]), (0, []))  # a restart finds them gone

    def test_the_cap_stops_with_exit_seven_and_an_empty_list_deletes_nothing(self):
        rc, events = self.delete([self.a, self.b], count=1)
        self.assertEqual((rc, len(events)), (7, 1))
        self.assertEqual(len({self.a, self.b} & self.present()), 1)
        before = self.present()
        self.assertEqual(self.delete([], count=0), (0, []))
        self.assertEqual(self.present(), before)


if __name__ == '__main__':
    unittest.main()
