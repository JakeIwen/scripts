import io
import json
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from pi.apps.media_organizer import cli, mirror_sync, plans, records, runs, status
from pi.apps.media_organizer.apply import apply_plan
from pi.apps.media_organizer.undo import undo_run
from pi.tests.media._media_organizer_support import Fixture


class MirrorSyncTests(Fixture, unittest.TestCase):
    def preferences(self, **kwargs):
        return records.Settings(classifier='offline', run_alias_after_apply=False, **kwargs)

    @contextmanager
    def mirror_unmounted(self):
        with patch.object(Path, 'is_mount', lambda path: path == self.media):
            yield

    def apply_all(self):
        plan = plans.create_plan(self.preferences())
        return apply_plan(plan, records.RUN_MANUAL, self.preferences(), self.client)

    def test_unmounted_apply_is_queued_then_replayed_and_undoable(self):
        self.payload('Movie.2024', mirror=True)
        with self.mirror_unmounted():
            run = self.apply_all()
            self.assertEqual(run.items[0].mirror_state, records.MIRROR_PENDING)
            self.assertEqual(status.status(self.client).mirror_pending, 1)
            with self.assertRaises(ValueError):
                mirror_sync.sync()
        self.assertTrue((self.mirror / 'torrent/New/Movie.2024').exists())
        result = mirror_sync.sync()
        self.assertEqual(result['renamed'], ['Movie.2024'])
        self.assertFalse((self.mirror / 'torrent/New/Movie.2024').exists())
        self.assertEqual((self.mirror / 'torrent/Movies/Movie.2024/video.mkv').read_bytes(),
                         b'backup video data')
        saved = runs.load(run.id).items[0]
        self.assertEqual(saved.mirror_state, records.MIRROR_SETTLED)
        self.assertEqual(saved.mirror_new, str(self.mirror / 'torrent/Movies/Movie.2024'))
        self.assertEqual(status.status(self.client).mirror_pending, 0)
        self.assertEqual(mirror_sync.sync()['renamed'], [])
        undone = undo_run(runs.load(run.id), self.client)
        self.assertEqual(undone.items[0].status, records.ITEM_REVERTED)
        self.assertTrue((self.mirror / 'torrent/New/Movie.2024').exists())

    def test_legacy_journal_without_mirror_state_gets_one_check(self):
        self.payload('Movie.2024', mirror=True)
        with self.mirror_unmounted():
            run = self.apply_all()
        journal = runs.load(run.id)
        journal.items[0].mirror_state = None  # written before mirror_state existed
        runs.save(journal)
        self.assertEqual(mirror_sync.sync()['renamed'], ['Movie.2024'])
        self.assertTrue((self.mirror / 'torrent/Movies/Movie.2024').exists())

    def test_payload_moved_again_is_settled_without_touching_the_mirror(self):
        torrent = self.payload('Movie.2024', mirror=True)
        with self.mirror_unmounted():
            run = self.apply_all()
        elsewhere = self.media / 'torrent/Documentaries/Else'
        elsewhere.mkdir(parents=True)
        self.client.set_location(torrent['hash'], elsewhere)
        result = mirror_sync.sync()
        self.assertEqual((result['renamed'], result['settled']), ([], 1))
        self.assertTrue((self.mirror / 'torrent/New/Movie.2024').exists())
        self.assertEqual(runs.load(run.id).items[0].mirror_state, records.MIRROR_SETTLED)

    def test_existing_destination_copy_is_left_for_rsync(self):
        self.payload('Movie.2024', mirror=True)
        with self.mirror_unmounted():
            self.apply_all()
        (self.mirror / 'torrent/Movies/Movie.2024').mkdir(parents=True)
        result = mirror_sync.sync()
        self.assertEqual((result['renamed'], result['settled']), ([], 1))
        self.assertTrue((self.mirror / 'torrent/New/Movie.2024/video.mkv').exists())

    def test_undo_while_unmounted_reverses_a_renamed_mirror_later(self):
        self.payload('Movie.2024', mirror=True)
        run = self.apply_all()  # mounted: the mirror is renamed immediately
        self.assertEqual(run.items[0].mirror_state, records.MIRROR_SETTLED)
        with self.mirror_unmounted():
            undone = undo_run(run, self.client)
        self.assertEqual(undone.items[0].status, records.ITEM_REVERTED)
        self.assertEqual(undone.items[0].mirror_state, records.MIRROR_PENDING)
        self.assertTrue((self.mirror / 'torrent/Movies/Movie.2024').exists())
        self.assertEqual(mirror_sync.sync()['renamed'], ['Movie.2024'])
        self.assertTrue((self.mirror / 'torrent/New/Movie.2024').exists())
        self.assertFalse((self.mirror / 'torrent/Movies/Movie.2024').exists())

    def test_undo_of_a_still_pending_rename_settles_it(self):
        self.payload('Movie.2024', mirror=True)
        with self.mirror_unmounted():
            run = self.apply_all()
            undone = undo_run(run, self.client)
        self.assertEqual(undone.items[0].mirror_state, records.MIRROR_SETTLED)
        self.assertEqual(mirror_sync.sync()['renamed'], [])
        self.assertTrue((self.mirror / 'torrent/New/Movie.2024').exists())

    def test_invalid_journal_stays_pending_and_reports_an_error(self):
        self.payload('Movie.2024', mirror=True)
        with self.mirror_unmounted():
            run = self.apply_all()
        journal = runs.load(run.id)
        journal.items[0].old_path = str(self.media / 'torrent/Movies')
        runs.save(journal)
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(['mirror-sync'])
        result = json.loads(output.getvalue())
        self.assertEqual((code, len(result['failed'])), (1, 1))
        self.assertIn('error', result)
        self.assertEqual(runs.load(run.id).items[0].mirror_state, records.MIRROR_PENDING)

    def test_disabled_mirror_renames_are_never_queued(self):
        self.payload('Movie.2024', mirror=True)
        with self.mirror_unmounted():
            plan = plans.create_plan(self.preferences())
            run = apply_plan(plan, records.RUN_MANUAL, self.preferences(mirror_rename=False),
                             self.client)
        self.assertEqual(run.items[0].mirror_state, records.MIRROR_SETTLED)
        self.assertEqual(status.status(self.client).mirror_pending, 0)
