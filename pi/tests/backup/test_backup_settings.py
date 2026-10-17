"""User preferences preserve private config and apply through existing workers."""
import json
import io
import os
import runpy
import sys
from contextlib import redirect_stdout
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock

import backup_settings as settings
import backup_settings_control as cli
from backup_schedule import scheduled_crontab, migrate


class SettingsTests(unittest.TestCase):
    def test_cli_conflict_category_survives_a_different_human_message(self):
        output=io.StringIO()
        with mock.patch.object(settings,'save',side_effect=settings.SettingsConflict('New wording.')), \
             mock.patch.object(sys,'argv',[cli.__file__,'--save']), \
             mock.patch.object(sys,'stdin',io.StringIO('{"revision":"old","changes":{}}')), \
             redirect_stdout(output), self.assertRaises(SystemExit) as exit:
            runpy.run_path(cli.__file__,run_name='__main__')
        self.assertEqual(exit.exception.code,os.EX_TEMPFAIL)
        self.assertEqual(json.loads(output.getvalue())['message'],'New wording.')

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.paths = settings.SettingsPaths(root/'preferences.json', root/'backup_conf.sh',
            root/'pi.json', root/'mac.json', root/'lock')
        self.paths.local.write_text('KEEP_DAILY=21\nCLONE_TARGETS=(hotspare-a:7 hotspare-b:14)\n')
        self.paths.pi.write_text(json.dumps({'bandwidth_limit':'8M','keep_generations':8,'rclone_config':'PRIVATE'}))
        self.paths.mac.write_text(json.dumps({'keep_generations':2,'max_source_age_hours':72}))

    def save(self, changes, revision=None):
        return settings.save({'revision':revision or settings.snapshot(self.paths)['revision'],
                              'changes':changes}, self.paths)

    def test_current_values_are_loaded_without_exposing_private_configuration(self):
        result = settings.snapshot(self.paths)
        self.assertEqual(result['values']['borg.daily'], 21)
        self.assertEqual(result['values']['mac.keep'], 2)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('rclone_config', json.dumps(result))

    def test_save_is_separate_and_worker_limits_apply_only_to_selected_cloud(self):
        before = self.paths.pi.read_bytes(), self.paths.mac.read_bytes()
        self.save({'pi.interval':3,'pi.bandwidth':12,'mac.timeout':20,'mac.overlap':False})
        self.assertEqual((self.paths.pi.read_bytes(), self.paths.mac.read_bytes()), before)
        self.assertEqual(settings.cloud_config({'interval_days':7}, 'pi', self.paths),
                         {'interval_days':3,'bandwidth_limit':'12M'})
        self.assertEqual(settings.cloud_config({}, 'mac', self.paths),
                         {'no_progress_timeout_seconds':1200,'overlap_verification':False})
        self.assertEqual(self.paths.preferences.stat().st_mode & 0o777, 0o600)

    def test_invalid_values_and_retention_relationships_do_not_write(self):
        for changes in ({'pi.interval':True}, {'pi.interval':0}, {'unknown':3},
                        {'mac.keep':1}, {'exfat.daily':90}, {'spare.interval':0},
                        {'disk.start':9}, {'alerts.success':1}, {'pi.bandwidth':'8M; id'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.save(changes)
            self.assertFalse(self.paths.preferences.exists())

    def test_mac_upload_limit_does_not_silently_change_verification_limit(self):
        self.save({'mac.bandwidth':12})
        cfg=settings.cloud_config({'bandwidth_limit':'8M'},'mac',self.paths)
        self.assertEqual(cfg['bandwidth_limit'],'12M')
        self.assertEqual(cfg['verification_bandwidth_limit'],'8M')
        self.assertEqual(settings.snapshot(self.paths)['values']['mac.download'],8)

    def test_fractional_bandwidth_is_encoded_in_whole_kibibytes(self):
        self.save({'pi.bandwidth':.5})
        self.assertEqual(settings.cloud_config({},'pi',self.paths)['bandwidth_limit'],'512k')

    def test_stale_revision_cannot_overwrite_another_save_or_native_config_edit(self):
        old = settings.snapshot(self.paths)['revision']
        self.save({'pi.interval':3})
        with self.assertRaisesRegex(ValueError, 'changed elsewhere'): self.save({'pi.keep':4}, old)
        old = settings.snapshot(self.paths)['revision']
        self.paths.local.write_text('KEEP_DAILY=15\n')
        with self.assertRaisesRegex(ValueError, 'changed elsewhere'): self.save({'pi.keep':4}, old)

    def test_corrupt_and_symlink_preferences_fail_closed(self):
        self.paths.preferences.symlink_to(self.paths.pi)
        with self.assertRaises(ValueError): settings.snapshot(self.paths)
        self.paths.preferences.unlink()
        self.paths.preferences.write_text('{')
        self.paths.preferences.chmod(0o600)
        with self.assertRaises(ValueError): settings.snapshot(self.paths)

    def test_local_projection_and_shell_output_use_the_same_preferences(self):
        saved = self.save({'borg.daily':8,'spare.interval':10,'alerts.borg':72})
        shell = settings.shell_overrides(self.paths)
        self.assertIn('KEEP_DAILY=8', shell)
        self.assertIn('CLONE_ROTATION_DAYS=10', shell)
        self.assertTrue(all(re.fullmatch(r'[A-Z][A-Z0-9_]*=[0-9]+', line) for line in shell.splitlines()))
        projection = settings.local_projection(saved['values'])
        self.assertEqual(projection['borg_stale_hours'], 72)
        self.assertEqual(projection['targets'][1]['interval_days'], 20)

    def test_old_cadences_migrate_on_read_and_next_save_without_losing_other_settings(self):
        self.paths.preferences.write_text(json.dumps({'version':1, 'values':
            {'spare.a':10,'spare.b':21,'borg.daily':8}}))
        self.paths.preferences.chmod(0o600)
        current = settings.snapshot(self.paths)
        self.assertEqual(current['values']['spare.interval'],10)
        self.assertNotIn('spare.a',current['values'])
        self.assertEqual(settings.local_projection(current['values'], self.paths.local.parent)['targets'],
            [{'label':'hotspare-a','interval_days':20},{'label':'hotspare-b','interval_days':20}])
        self.save({'alerts.borg':72})
        stored=json.loads(self.paths.preferences.read_text())['values']
        self.assertEqual(stored,{'spare.interval':10,'borg.daily':8,'alerts.borg':72})
        self.assertNotIn('spare.b',stored)

    def test_invalid_legacy_cadence_is_not_silently_discarded(self):
        for old in ({'spare.a':None}, {'spare.a':True}, {'spare.b':0},
                    {'spare.a':10,'spare.b':14}):
            self.paths.preferences.write_text(json.dumps({'version':1,'values':old}))
            self.paths.preferences.chmod(0o600)
            with self.assertRaises(ValueError): settings.snapshot(self.paths)

    def test_window_handles_midnight_and_excludes_end_hour(self):
        for start, end, hour, expected in ((3,9,3,0),(3,9,9,3),(22,5,23,0),(22,5,4,0),(22,5,5,3)):
            with mock.patch.object(cli.settings,'effective',return_value={'disk.start':start,'disk.end':end}), \
                 mock.patch.object(cli.dt,'datetime') as date:
                date.now.return_value.hour = hour
                self.assertEqual(cli.main(['--window']), expected)


class ScheduleTests(unittest.TestCase):
    CRON = ('# keep this comment\n17 2 * * * /unrelated/task\n'
            '0 3-8 * * *  "$scripts/backup/backup_window.sh" >> "$cronlog/pi_backup.log" 2>&1\n'
            '10 3-8 * * * "$scripts/backup/openwrt_backup.sh" --daily >> "$cronlog/openwrt_backup.log" 2>&1\n')

    def test_only_owned_entries_change_and_migration_is_idempotent(self):
        result = scheduled_crontab(self.CRON)
        self.assertTrue(result.startswith('# keep this comment\n17 2 * * * /unrelated/task\n'))
        self.assertIn('0 * * * *  "$scripts/backup/backup_window.sh" --scheduled', result)
        self.assertEqual(scheduled_crontab(result), result)

    def test_custom_or_duplicate_cron_entries_are_not_overwritten(self):
        for text in (self.CRON.replace('0 3-8', '30 3-8'), self.CRON+self.CRON, ''):
            with self.assertRaises(ValueError): scheduled_crontab(text)

    def test_failed_install_retains_the_original_table(self):
        with tempfile.TemporaryDirectory() as root:
            table = [self.CRON]
            def command(args, **kwargs):
                if args[-1] == '-l': return subprocess.CompletedProcess(args,0,table[0],'')
                if args[-1] == '-': raise subprocess.CalledProcessError(1,args)
                table[0] = Path(args[-1]).read_text()
                return subprocess.CompletedProcess(args,0,'','')
            with self.assertRaises(subprocess.CalledProcessError): migrate(command,Path(root))
            self.assertEqual(table[0],self.CRON)
