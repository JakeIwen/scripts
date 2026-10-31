"""Regressions from the Oct 31 review of the organizer's apply, mirror and progress paths."""
import fcntl
import os
import threading
import unittest
from unittest.mock import patch
from pi.apps.media_organizer import busy, config, hook, locking, mirror_sync, plans, records, runs
from pi.apps.media_organizer import relocation
from pi.apps.media_organizer.apply import apply_plan
from pi.apps.media_organizer.scan import scan
from pi.apps.media_organizer.undo import undo_run
from pi.tests.media._media_organizer_support import Fixture


class BackupSyncExclusionTests(Fixture, unittest.TestCase):
    def preferences(self, **kwargs):
        return records.Settings(classifier='offline', run_alias_after_apply=False, **kwargs)

    def hold_backup_lock(self):
        fd = os.open(config.MIRROR_LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def test_interactive_apply_and_undo_refuse_while_the_backup_syncs(self):
        self.payload('Movie.2024')
        plan = plans.create_plan(self.preferences())
        run = apply_plan(plan, records.RUN_MANUAL, self.preferences(), self.client)
        self.payload('Other.2024')
        later = plans.create_plan(self.preferences())
        fd = self.hold_backup_lock()
        try:
            with self.assertRaises(locking.BackupSyncRunning):
                apply_plan(later, records.RUN_MANUAL, self.preferences(), self.client)
            with self.assertRaises(locking.BackupSyncRunning):
                undo_run(run, self.client)
        finally:
            os.close(fd)
        self.assertTrue((self.new / 'Other.2024').exists())
        self.assertTrue((self.media / 'torrent/Movies/Movie.2024').exists())
        self.assertEqual(plans.load(later.id).status, records.PLAN_PROPOSED)

    def test_completion_hook_waits_for_the_sync_then_applies(self):
        self.show()
        self.payload('The.Example.S01')  # auto-eligible: confident, existing show
        fd = self.hold_backup_lock()
        seen = []
        real_note = busy.note
        def note(**fields):
            real_note(**fields)
            seen.append(busy.current())
        threading.Timer(0.3, os.close, (fd,)).start()
        with patch.object(locking, 'BACKUP_POLL_SECONDS', 0.05), \
                patch('pi.apps.media_organizer.apply.note', side_effect=note):
            result = hook.post_download(self.preferences())
        self.assertIsNone(result['error'])
        self.assertEqual(result['run']['moved_count'], 1)
        self.assertEqual(seen[0]['detail'], 'waiting for the backup sync to finish')
        self.assertEqual(seen[0]['stage'], records.JOB_APPLY)


class UnownedFileTests(Fixture, unittest.TestCase):
    def test_folder_with_files_the_torrent_does_not_own_is_refused(self):
        self.payload('Movie.2024')
        (self.new / 'Movie.2024' / '._video.mkv').write_bytes(b'finder metadata')
        settings = records.Settings(classifier='offline', run_alias_after_apply=False)
        run = apply_plan(plans.create_plan(settings), records.RUN_MANUAL, settings, self.client)
        self.assertEqual(run.items[0].status, records.ITEM_FAILED)
        self.assertIn('not part of the torrent', run.items[0].error)
        self.assertEqual(self.transport.moves, [])
        self.assertTrue((self.new / 'Movie.2024' / 'video.mkv').exists())


class MirrorStateOrderTests(Fixture, unittest.TestCase):
    def test_failed_live_mirror_rename_stays_pending_for_mirror_sync(self):
        self.payload('Movie.2024', mirror=True)
        settings = records.Settings(classifier='offline', run_alias_after_apply=False)
        real_rename = os.rename
        def rename(source, target):
            if str(source).startswith(str(self.mirror)):
                raise PermissionError('mirror busy')
            return real_rename(source, target)
        with patch.object(relocation.os, 'rename', side_effect=rename):
            run = apply_plan(plans.create_plan(settings), records.RUN_MANUAL, settings, self.client)
        item = runs.load(run.id).items[0]
        self.assertEqual((item.status, item.mirror_state), (records.ITEM_MOVED, records.MIRROR_PENDING))
        self.assertEqual(mirror_sync.sync()['renamed'], ['Movie.2024'])
        self.assertTrue((self.mirror / 'torrent/Movies/Movie.2024').exists())


class ProgressStageTests(Fixture, unittest.TestCase):
    def test_hook_moves_show_the_apply_stage_then_restore_propose(self):
        self.show()
        self.payload('The.Example.S01')
        stages = []
        from pi.apps.media_organizer import apply as apply_module
        real_move = apply_module.move_payload
        def move(item, client):
            stages.append(busy.current()['stage'])
            return real_move(item, client)
        def schedule():
            stages.append(busy.current()['stage'])
            return records.ALIAS_SCHEDULED
        settings = records.Settings(classifier='offline', run_alias_after_apply=False)
        with patch.object(apply_module, 'move_payload', side_effect=move), \
                patch.object(hook.link_rebuild, 'schedule', side_effect=schedule):
            hook.post_download(settings)
        self.assertEqual(stages, [records.JOB_APPLY, records.JOB_PROPOSE])
        self.assertIsNone(busy.current())


class ViewAndScanTests(Fixture, unittest.TestCase):
    def test_unconfirmed_rows_are_locked_but_not_reported_moved(self):
        self.payload('A.Movie.2024')
        bad = self.payload('B.Movie.2024')
        self.transport.fail.add(bad['hash'])
        settings = records.Settings(classifier='offline', run_alias_after_apply=False)
        plan = plans.create_plan(settings)
        apply_plan(plan, records.RUN_MANUAL, settings, self.client)
        view = plans.view(plans.load(plan.id))
        self.assertEqual(view['moved_rows'], ['A.Movie.2024'])
        self.assertEqual(view['settled_rows'], ['A.Movie.2024', 'B.Movie.2024'])

    def test_not_media_floor_matches_the_link_builder(self):
        for name, size in (('below.mkv', config.MIN_MEDIA_BYTES - 1),
                           ('equal.mkv', config.MIN_MEDIA_BYTES),
                           ('above.mkv', config.MIN_MEDIA_BYTES + 1)):
            with open(self.new / name, 'wb') as stream:
                stream.truncate(size)  # sparse: no real disk use
        rows = {entry.name: entry.skip_reason for entry in scan(self.client)[0]}
        self.assertEqual(rows, {'below.mkv': records.SKIP_NOT_MEDIA,
                                'equal.mkv': records.SKIP_NOT_MEDIA, 'above.mkv': None})
