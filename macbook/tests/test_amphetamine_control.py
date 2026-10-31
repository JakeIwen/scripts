"""Native preference round trips and controller behavior without locking the screen."""
from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import plistlib
import sys
import tempfile
import unittest

from amphetamine.control import configure, exclusive, read_status
from amphetamine.model import (DELAY, DISPLAY_SLEEP, ENABLED, Conflict, ControlError,
                               PermissionDenied, Session, revision)
from amphetamine.native import NativeApp

ENTRY = Path(__file__).resolve().parents[1] / 'scripts/amphetamine_control.py'
SANDBOX_EXEC = '/usr/bin/sandbox-exec'


class FixtureApp:
    """Session lifecycle boundary; settings and side effects remain observable."""
    def __init__(self, directory):
        self.state_dir = Path(directory)
        self.values = {ENABLED: False, DELAY: 60, DISPLAY_SLEEP: False,
                       'End Session On Low Battery': True, 'Low Battery Percent': 10,
                       'Allow Closed-Display Sleep': True}
        self.current = Session(running=True)
        self.backups = []
        self.change_on_quit = False
        self.fail_write = False
        self.fail_resume = False
        self.fail_read_after_quit = False

    def read_preferences(self):
        if self.fail_read_after_quit and not self.current.running:
            self.fail_read_after_quit = False
            raise ControlError('Preference read failed')
        return dict(self.values)

    def write_preferences(self, changes):
        for key, value in changes.items():
            if value is None:
                self.values.pop(key, None)
            else:
                self.values[key] = value
            if self.fail_write:
                self.fail_write = False
                raise ControlError('Partial native write failed')

    def backup(self, values):
        self.backups.append(deepcopy(values))

    def session(self):
        return self.current

    def quit(self):
        self.current = Session()
        if self.change_on_quit:
            self.values[DELAY] = 1800
            self.change_on_quit = False

    def resume(self, previous, start=False):
        if self.fail_resume:
            self.fail_resume = False
            raise ControlError('Relaunch failed')
        self.current = replace(previous, running=previous.running or start,
                               active=previous.active or start,
                               remaining=0 if previous.active or start else -3)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = FixtureApp(self.directory.name)

    def save(self, enabled=True, minutes=20):
        return configure(self.app, enabled, minutes, revision(self.app.values))

    def test_enable_twenty_minutes_and_disable_keep_session_and_battery_cutoff(self):
        result = self.save()
        self.assertEqual(result['delay_seconds'], 1200)
        self.assertTrue(result['enabled'])
        self.assertTrue(result['display_sleep_locked'])
        self.assertTrue(result['session']['active'])
        self.assertEqual(result['low_battery_percent'], 10)
        self.assertTrue(result['low_battery_enabled'])
        self.assertTrue(self.app.values['Allow Closed-Display Sleep'])
        self.assertFalse(self.app.backups[0][ENABLED])
        self.app.current = replace(self.app.current, display_sleep=True, screen_saver=True)
        result = self.save(False, 30)
        self.assertFalse(result['enabled'])
        self.assertEqual(result['delay_seconds'], 1800)
        self.assertTrue(result['session']['active'])
        self.assertTrue(result['session']['display_sleep'])
        self.assertTrue(result['session']['screen_saver'])

    def test_preserve_special_sessions_and_reject_stale_or_invalid_input(self):
        original = dict(self.app.values)
        for remaining, closed in ((300, False), (-1, False), (-2, False), (0, True)):
            with self.subTest(remaining=remaining, closed=closed):
                self.app.current = Session(True, True, remaining, closed_display=closed)
                with self.assertRaises(Conflict):
                    self.save()
                self.assertEqual(self.app.values, original)
                self.assertTrue(self.app.current.active)
        self.app.current = Session(running=True)
        with self.assertRaises(Conflict):
            configure(self.app, True, 20, '0' * 64)
        for enabled, minutes in ((1, 20), (True, True), (True, 0), (True, 241), (True, 2.5)):
            with self.assertRaises(ControlError):
                configure(self.app, enabled, minutes, revision(self.app.values))
        self.assertEqual(self.app.values, original)
        self.assertEqual(self.app.backups, [])

    def test_external_edit_during_quit_is_preserved_and_session_restored(self):
        self.app.current = Session(True, True, 0)
        self.app.change_on_quit = True
        with self.assertRaisesRegex(Conflict, 'newer settings'):
            self.save()
        self.assertEqual(self.app.values[DELAY], 1800)
        self.assertFalse(self.app.values[ENABLED])
        self.assertTrue(self.app.current.active)

    def test_partial_write_and_launch_failure_restore_native_keys_and_session(self):
        for failure in ('fail_write', 'fail_resume'):
            with self.subTest(failure=failure):
                self.app.values.pop(DISPLAY_SLEEP, None)
                before = dict(self.app.values)
                previous = Session(True, True, 0, True, True)
                self.app.current = previous
                setattr(self.app, failure, True)
                with self.assertRaisesRegex(ControlError, 'were restored'):
                    self.save()
                self.assertEqual(self.app.values, before)
                self.assertEqual(self.app.current, previous)

    def test_read_failure_after_quit_restores_session(self):
        self.app.current = Session(True, True, 0)
        self.app.fail_read_after_quit = True
        with self.assertRaisesRegex(ControlError, 'read failed'):
            self.save()
        self.assertTrue(self.app.current.active)
        self.assertFalse(self.app.values[ENABLED])

    def test_busy_lock_and_stopped_app_are_respected(self):
        with exclusive(self.app):
            with self.assertRaises(Conflict):
                self.save()
        self.app.current = Session()
        result = self.save(False)
        self.assertFalse(result['session']['running'])
        self.assertFalse(result['session']['active'])

    def test_preference_read_does_not_confuse_counter_updates_with_user_edits(self):
        before = read_status(self.app)
        self.app.values['Total Session Run Time'] = 1000
        self.assertEqual(read_status(self.app)['revision'], before['revision'])
        self.app.values[ENABLED] = True
        self.assertNotEqual(read_status(self.app)['revision'], before['revision'])


class PreferencesFileTests(unittest.TestCase):
    def test_reads_native_container_file_without_defaults_cache_or_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            app = NativeApp(directory)
            path = app.domain.parent / (app.domain.name + '.plist')
            path.parent.mkdir(parents=True)
            original = {ENABLED: True, DELAY: 1200, 'Low Battery Percent': 10}
            raw = plistlib.dumps(original, fmt=plistlib.FMT_BINARY)
            path.write_bytes(raw)
            self.assertEqual(app.read_preferences(), original)
            self.assertEqual(path.read_bytes(), raw)

    def test_missing_or_invalid_preferences_fail_instead_of_reporting_off(self):
        with tempfile.TemporaryDirectory() as directory:
            app = NativeApp(directory)
            with self.assertRaisesRegex(ControlError, 'Cannot read'):
                app.read_preferences()
            path = app.domain.parent / (app.domain.name + '.plist')
            path.parent.mkdir(parents=True)
            for raw in (b'broken plist', plistlib.dumps([])):
                path.write_bytes(raw)
                with self.assertRaises(ControlError):
                    app.read_preferences()

    def test_file_mode_denial_is_not_reported_as_a_privacy_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            app = NativeApp(directory)
            path = app.domain.parent / (app.domain.name + '.plist')
            path.parent.mkdir(parents=True)
            path.write_bytes(plistlib.dumps({ENABLED: True}))
            path.chmod(0)
            with self.assertRaisesRegex(ControlError, 'Cannot read') as caught:
                app.read_preferences()
            self.assertNotIsInstance(caught.exception, PermissionDenied)


@unittest.skipUnless(sys.platform == 'darwin', 'Native defaults integration requires macOS')
class NativePreferencesTests(unittest.TestCase):
    def test_real_defaults_round_trip_backup_and_delete_only_controlled_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            app = NativeApp(directory)
            app.domain.parent.mkdir(parents=True)
            try:
                app.write_preferences({'Low Battery Percent': 10, ENABLED: False})
            except ControlError as exc:
                if 'sandbox_extension_issue_file failed' in str(exc) and 'Operation not permitted' in str(exc):
                    self.skipTest('macOS sandbox denies cfprefsd file extensions for temporary domains')
                raise
            original = app.read_preferences()
            app.backup(original)
            app.write_preferences({ENABLED: True, DELAY: 1200, DISPLAY_SLEEP: True})
            saved = app.read_preferences()
            self.assertEqual(saved[DELAY], 1200)
            self.assertIs(saved[ENABLED], True)
            self.assertIs(saved[DISPLAY_SLEEP], True)
            self.assertEqual(saved['Low Battery Percent'], 10)
            app.write_preferences({DELAY: None, DISPLAY_SLEEP: None, ENABLED: False})
            self.assertEqual(app.read_preferences(), original)
            backup = next(app.state_dir.glob('*.plist'))
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)


class CliTests(unittest.TestCase):
    def test_help_and_invalid_configuration_from_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            help_result = subprocess.run([sys.executable, str(ENTRY), '--help'], cwd=directory,
                                         text=True, capture_output=True)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            result = subprocess.run([sys.executable, str(ENTRY), 'configure', '--enabled', 'on',
                                     '--minutes', '0', '--revision', '0' * 64], cwd=directory,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('between 1 and 240', result.stderr)


def deny_reads(directory):
    # The same kernel refusal (EPERM) macOS gives a background process reading
    # another app's container, so the real CLI can be run against it.
    return '(version 1)(allow default)(deny file-read-data (subpath "{}"))'.format(directory)


@unittest.skipUnless(os.path.exists(SANDBOX_EXEC), 'sandbox-exec ships only with macOS')
class PrivacyDenialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # Seatbelt matches resolved paths; /var/folders is a symlink on macOS.
        self.home = Path(os.path.realpath(temporary.name))
        probe = self.home / 'probe'
        probe.write_text('probe')
        result = subprocess.run([SANDBOX_EXEC, '-p', deny_reads(probe), '/bin/cat', str(probe)],
                                text=True, capture_output=True)
        if result.returncode == 0 or str(probe) not in result.stderr:
            self.skipTest('sandbox-exec cannot apply a profile inside an existing sandbox: '
                          + result.stderr.strip())

    def test_status_exits_4_with_settings_steps_when_macos_denies_the_container(self):
        app = NativeApp(self.home)
        path = app.domain.parent / (app.domain.name + '.plist')
        path.parent.mkdir(parents=True)
        path.write_bytes(plistlib.dumps({ENABLED: True, DELAY: 1200}))
        result = subprocess.run(
            [SANDBOX_EXEC, '-p', deny_reads(self.home / 'Library/Containers'),
             sys.executable, '-B', str(ENTRY), 'status'],
            cwd=str(self.home), env=dict(os.environ, HOME=str(self.home)),
            text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertIn('Privacy & Security > Full Disk Access', result.stderr)


if __name__ == '__main__':
    unittest.main()
