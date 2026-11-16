"""Time Machine worker runs record advisory orphan-cleanup progress across interruptions."""
import hashlib
import json
import unittest

import icloud_backup as cloud
import time_machine_store as store
from pi.tests.backup import test_time_machine_icloud as tm
from pi.tests.backup.test_time_machine_icloud import GEN, job


class RetentionProgressTests(tm.RetentionFixture):
    def progress(self):
        return store.read_json(self.state_dir / 'state.json')['retention_progress']

    def test_cleanup_progress_accumulates_across_an_interrupted_run(self):
        self.write_state()
        self.object_size = 1024**3
        self.fail_deletes_after = 1
        self.run_worker()
        first = self.progress()
        self.assertEqual((first['generation'], first['keep_generations'], first['step']), (GEN, 2, 'deleting'))
        self.assertEqual((first['planned_objects'], first['deleted_objects'], first['remaining_objects']), (3, 1, 2))
        self.assertEqual((first['planned_bytes'], first['remaining_bytes']), (3 * 1024**3, 2 * 1024**3))
        self.assertIsNotNone(first['listing_finished_at'])
        self.assertEqual(job.history.reason({'phase': 'retention', 'last_generation': GEN,
                                             'retention_progress': first}),
                         'Deleting unused cloud files: 1 / 3 (2.0 GiB left).')
        state = self.run_worker()
        done = state['retention_progress']
        self.assertEqual(state['retention_done_for'], GEN)
        self.assertEqual((done['step'], done['planned_objects'], done['deleted_objects'], done['remaining_objects']),
                         ('complete', 3, 3, 0))
        self.assertEqual((done['listings'], done['episode_started_at']), (2, first['episode_started_at']))

    def test_a_delete_lost_after_reaching_the_server_is_counted_by_the_next_listing(self):
        self.write_state()
        self.fail_deletes_after = 1
        self.fail_after_server_delete = True
        self.run_worker()
        self.assertEqual(self.progress()['deleted_objects'], 1)  # the client never saw its second delete finish
        self.inventory_failures = [cloud.Deferred('ignition is on')]
        self.run_worker()
        self.assertEqual(self.progress()['step'], 'listing')
        self.run_worker()
        done = self.progress()
        self.assertEqual((done['planned_objects'], done['deleted_objects'], done['step']), (3, 3, 'complete'))

    def test_a_changed_retention_setting_or_a_grown_set_starts_a_new_count(self):
        self.write_state()
        self.fail_deletes_after = 1
        self.run_worker()
        started = self.progress()['episode_started_at']
        self.cfg['keep_generations'] = 3
        self.fail_deletes_after = 2  # cumulative: one more delete, then the uplink drops
        self.run_worker()
        changed = self.progress()
        self.assertEqual((changed['keep_generations'], changed['planned_objects'], changed['deleted_objects']), (3, 2, 1))
        self.assertGreaterEqual(changed['episode_started_at'], started)
        extra = hashlib.sha256(b'late orphan').hexdigest()
        self.remote_objects.add(extra)
        self.run_worker()
        grown = self.progress()
        self.assertEqual((grown['planned_objects'], grown['deleted_objects'], grown['step']), (2, 2, 'complete'))

    def test_interruption_after_metadata_retirement_keeps_the_episode(self):
        oldest = 'tm-20260801T010203Z-12345678'
        retired_only = hashlib.sha256(b'only in the retired copy').hexdigest()
        data = json.dumps({**self.manifest, 'generation': oldest,
                           'files': {**self.manifest['files']}})
        self.documents[oldest] = {'manifest.json': data, '_COMPLETE.json': json.dumps({
            'owner': store.OWNER, 'generation': oldest, 'verification': 'sha256-download-compared',
            'manifest_sha256': hashlib.sha256(data.encode()).hexdigest()})}
        self.remote_objects.add(retired_only)
        self.write_state()
        self.inventory_failures = [cloud.Deferred('ignition is on')]
        self.run_worker()
        self.assertNotIn(oldest, self.documents)  # its metadata is gone before the listing failed
        first = self.progress()
        self.assertEqual((first['step'], first['planned_objects']), ('listing', None))
        state = self.run_worker()
        done = state['retention_progress']
        self.assertEqual(state['retention_done_for'], GEN)
        self.assertEqual((done['episode_started_at'], done['planned_objects'], done['deleted_objects']),
                         (first['episode_started_at'], 4, 4))
