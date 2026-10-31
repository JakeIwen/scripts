import unittest
from pathlib import Path
from unittest.mock import patch
from pi.apps.media_organizer import busy, plans, records, runs
from pi.apps.media_organizer.apply import apply_plan
from pi.apps.media_organizer.undo import undo_run
from pi.tests.media._media_organizer_support import Fixture


class ApplyTests(Fixture, unittest.TestCase):
    def preferences(self, **kwargs):
        return records.Settings(classifier='offline', run_alias_after_apply=False, **kwargs)

    def test_tracked_untracked_mirror_and_reverse_undo(self):
        first = self.payload('A.Movie.2024', mirror=True)
        second = self.payload('B.Movie.2024', mirror=True)
        self.payload('C.Movie.2024', tracked=False, mirror=True)
        plan = plans.create_plan(self.preferences())
        run = apply_plan(plan, records.RUN_MANUAL, self.preferences(), self.client)
        self.assertTrue(run.undoable)
        self.assertEqual([item.status for item in run.items], ['moved'] * 3)
        self.assertEqual(plans.load(plan.id).status, records.PLAN_APPLIED)
        for item in run.items:
            self.assertTrue((self.media / 'torrent/Movies' / item.name).exists())
            self.assertTrue((self.mirror / 'torrent/Movies' / item.name).exists())
            self.assertFalse((self.new / item.name).exists())
        undone = undo_run(run, self.client)
        self.assertFalse(undone.undoable)
        self.assertIsNotNone(undone.undone_at)
        self.assertEqual([item.status for item in undone.items], ['reverted'] * 3)
        self.assertEqual([move[0] for move in self.transport.moves],
                         [first['hash'], second['hash'], second['hash'], first['hash']])
        for item in run.items:
            self.assertTrue((self.new / item.name).exists())
            self.assertTrue((self.mirror / 'torrent/New' / item.name).exists())

    def test_failed_middle_row_is_journaled_and_next_row_moves(self):
        self.payload('A.Movie.2024')
        bad = self.payload('B.Movie.2024')
        self.payload('C.Movie.2024', tracked=False)
        self.transport.fail.add(bad['hash'])
        plan = plans.create_plan(self.preferences())
        run = apply_plan(plan, 'manual', self.preferences(), self.client)
        saved = runs.load(run.id)
        self.assertEqual([i.status for i in saved.items], ['moved', 'failed', 'moved'])
        self.assertEqual(plans.load(plan.id).status, records.PLAN_PARTIAL)
        self.assertTrue((self.new / 'B.Movie.2024').exists())
        self.assertTrue(saved.undoable)
        with self.assertRaises(ValueError):
            apply_plan(plan, 'manual', self.preferences(), self.client)

    def test_crash_after_move_keeps_recoverable_write_ahead_intent(self):
        torrent = self.payload('A.Movie.2024')
        self.transport.crash.add(torrent['hash'])
        plan = plans.create_plan(self.preferences())
        with self.assertRaises(KeyboardInterrupt):
            apply_plan(plan, 'manual', self.preferences(), self.client)
        saved = runs.list_runs()[0]
        self.assertTrue(saved.undoable)
        self.assertIsNone(saved.finished_at)
        self.assertEqual(len(saved.items), 1)
        self.assertTrue((self.media / 'torrent/Movies/A.Movie.2024').exists())
        self.transport.crash.clear()
        undo_run(saved, self.client)
        self.assertTrue((self.new / 'A.Movie.2024').exists())

    def test_conflict_never_overwrites_and_stale_untracked_owner_is_refused(self):
        self.payload('A.Movie.2024', tracked=False)
        self.payload('B.Movie.2024')
        plan = plans.create_plan(self.preferences())
        source = self.new / 'A.Movie.2024'
        self.transport.torrents.append(dict(hash='e' * 40, name=source.name,
            save_path=str(source.parent), content_path=str(source), state='forcedUP', progress=1, amount_left=0))
        target = self.media / 'torrent/Movies/B.Movie.2024'
        target.mkdir(parents=True)
        (target / 'keep').write_text('keep')
        run = apply_plan(plan, 'manual', self.preferences(), self.client)
        self.assertEqual([item.status for item in run.items], ['failed', 'failed'])
        self.assertEqual((target / 'keep').read_text(), 'keep')
        self.assertFalse(run.undoable)

    def test_auto_selects_confident_existing_shows_only(self):
        self.show()
        self.payload('The.Example.S01')
        self.payload('Movie.2024')
        self.payload('Brand.New.S01')
        plan = plans.create_plan(self.preferences())
        run = apply_plan(plan, 'auto', self.preferences(), self.client)
        self.assertEqual([item.name for item in run.items], ['The.Example.S01'])
        self.assertTrue((self.new / 'Movie.2024').exists())
        self.assertEqual(plans.load(plan.id).status, records.PLAN_PARTIAL)

    def test_alias_scheduled_after_shared_lock_released(self):
        import fcntl
        from pi.apps.media_organizer import config
        self.payload('Movie.2024')
        preferences = records.Settings(classifier='offline')
        plan = plans.create_plan(preferences)
        def check_lock():
            with config.LOCK_PATH.open('r+') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return records.ALIAS_SCHEDULED
        with patch('pi.apps.media_organizer.link_rebuild.schedule', side_effect=check_lock):
            run = apply_plan(plan, 'manual', preferences, self.client)
        self.assertEqual(run.alias_status, records.ALIAS_SCHEDULED)

    def test_progress_note_tracks_rows_while_applying(self):
        self.payload('A.Movie.2024')
        self.payload('B.Movie.2024')
        seen = []
        from pi.apps.media_organizer import apply as apply_module
        real = apply_module.move_payload
        def spy(item, client):
            seen.append(busy.current())
            return real(item, client)
        plan = plans.create_plan(self.preferences())
        with patch.object(apply_module, 'move_payload', side_effect=spy):
            apply_plan(plan, 'manual', self.preferences(), self.client)
        self.assertEqual([(n['stage'], n['detail'], n['done'], n['total']) for n in seen],
                         [('apply', 'A.Movie.2024', 0, 2), ('apply', 'B.Movie.2024', 1, 2)])
        self.assertIsNone(busy.current())

    def test_single_file_torrent_inside_its_folder_moves_the_folder(self):
        torrent = self.payload('Max.Payne.2008')
        torrent['content_path'] = str(self.new / 'Max.Payne.2008' / 'video.mkv')
        plan = plans.create_plan(self.preferences())
        run = apply_plan(plan, 'manual', self.preferences(), self.client)
        self.assertEqual(run.items[0].status, records.ITEM_MOVED)
        self.assertTrue((self.media / 'torrent/Movies/Max.Payne.2008/video.mkv').exists())
        self.assertFalse((self.new / 'Max.Payne.2008').exists())
        undone = undo_run(run, self.client)
        self.assertEqual(undone.items[0].status, records.ITEM_REVERTED)
        self.assertTrue((self.new / 'Max.Payne.2008/video.mkv').exists())

    def test_mirror_conflict_keeps_primary_undoable(self):
        self.payload('Movie.2024', mirror=True)
        target = self.mirror / 'torrent/Movies/Movie.2024'
        target.mkdir(parents=True)
        plan = plans.create_plan(self.preferences())
        run = apply_plan(plan, 'manual', self.preferences(), self.client)
        self.assertEqual(run.items[0].status, records.ITEM_MOVED)
        self.assertIn('mirror', run.items[0].error)
        self.assertTrue(run.undoable)
        self.assertIsNone(run.items[0].mirror_old)


class PartialPlanTests(Fixture, unittest.TestCase):
    def preferences(self, **kwargs):
        return records.Settings(classifier='offline', run_alias_after_apply=False, **kwargs)

    def test_partial_plan_stays_reviewable_and_applies_only_open_rows(self):
        self.show()
        self.payload('The.Example.S01')
        self.payload('Brand.New.S01E01')
        plan = plans.create_plan(self.preferences())
        first = apply_plan(plan, records.RUN_AUTO, self.preferences(), self.client)
        self.assertEqual([item.name for item in first.items], ['The.Example.S01'])
        self.assertEqual(plans.load(plan.id).status, records.PLAN_PARTIAL)
        with self.assertRaises(ValueError):
            plans.decide(plan, {'The.Example.S01': {'category': 'TV', 'show': 'Other', 'accepted': True}})
        edited = plans.decide(plan, {'Brand.New.S01E01': {'category': 'TV', 'show': 'Brand New', 'accepted': True}})
        self.assertEqual(edited.status, records.PLAN_PARTIAL)
        second = apply_plan(edited, records.RUN_MANUAL, self.preferences(), self.client)
        self.assertEqual([item.name for item in second.items], ['Brand.New.S01E01'])
        self.assertTrue((self.media / 'torrent/TV/Brand New/Brand.New.S01E01').exists())
        self.assertEqual(plans.load(plan.id).status, records.PLAN_APPLIED)
        self.assertEqual(plans.load(plan.id).run_ids, [first.id, second.id])
