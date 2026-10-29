import os
import unittest
from unittest.mock import patch
from pi.apps.media_organizer import plans, records
from pi.apps.media_organizer.apply import apply_plan
from pi.apps.media_organizer.undo import undo_run
from pi.tests.media._media_organizer_support import Fixture


class UndoTests(Fixture, unittest.TestCase):
    def test_moved_again_torrent_is_refused_while_other_rows_revert(self):
        self.payload('A.Movie.2024')
        torrent = self.payload('B.Movie.2024')
        preferences = records.Settings(classifier='offline', run_alias_after_apply=False)
        run = apply_plan(plans.create_plan(preferences), 'manual', preferences, self.client)
        elsewhere = self.media / 'torrent/TV/Elsewhere'
        elsewhere.mkdir(parents=True)
        self.client.set_location(torrent['hash'], elsewhere)
        reverted = undo_run(run, self.client)
        self.assertEqual([item.status for item in reverted.items], ['reverted', 'failed'])
        self.assertIn('location', reverted.items[1].error)
        self.assertTrue((elsewhere / 'B.Movie.2024').exists())
        self.assertFalse(reverted.undoable)

    def test_untracked_missing_or_source_collision_is_refused(self):
        self.payload('A.Movie.2024', tracked=False)
        self.payload('B.Movie.2024', tracked=False)
        preferences = records.Settings(classifier='offline', run_alias_after_apply=False)
        run = apply_plan(plans.create_plan(preferences), 'manual', preferences, self.client)
        os.rename(self.media / 'torrent/Movies/A.Movie.2024', self.root / 'moved-again')
        (self.new / 'B.Movie.2024').mkdir()
        reverted = undo_run(run, self.client)
        self.assertEqual([item.status for item in reverted.items], ['failed', 'failed'])
        self.assertTrue((self.root / 'moved-again').exists())
        self.assertTrue((self.media / 'torrent/Movies/B.Movie.2024').exists())

    def test_interrupted_undo_recovers_mirror_after_primary_was_restored(self):
        from pi.apps.media_organizer import runs
        self.payload('Movie.2024', mirror=True)
        preferences = records.Settings(classifier='offline', run_alias_after_apply=False)
        run = apply_plan(plans.create_plan(preferences), 'manual', preferences, self.client)
        with patch('pi.apps.media_organizer.undo.undo_mirror', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                undo_run(run, self.client)
        saved = runs.load(run.id)
        self.assertTrue(saved.undoable)
        self.assertTrue((self.new / 'Movie.2024').exists())
        self.assertTrue((self.mirror / 'torrent/Movies/Movie.2024').exists())
        result = undo_run(saved, self.client)
        self.assertEqual(result.items[0].status, records.ITEM_REVERTED)
        self.assertTrue((self.mirror / 'torrent/New/Movie.2024').exists())
