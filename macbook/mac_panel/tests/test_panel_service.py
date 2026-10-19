import json
import os
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import patch

from cli_runner import CliRunner, CommandResult, child_environment
from ownership_record import new_record, read_record, remove_record, write_record
from panel_paths import CHILD_PATH, HOST, LABEL, ROOT
from panel_test_support import FakeRunner
from service_configuration import assert_plist_identity, configuration, parse_enabled, supervision_status
from service_controller import PanelService
from service_http import ServiceHttp


class ServiceConfigurationTests(unittest.TestCase):
    def test_plist_lints_and_has_login_contract(self):
        config = configuration()
        self.assertEqual(config['Label'], LABEL)
        self.assertEqual(config['Umask'], 63)
        self.assertNotIn('UserName', config)
        self.assertEqual(config['EnvironmentVariables'], {'PATH': CHILD_PATH, 'PYTHONPATH': str(ROOT / 'shared/python')})
        self.assertTrue(all(Path(arg).is_absolute() for arg in config['ProgramArguments']))
        for key in ('RunAtLoad', 'KeepAlive'):
            self.assertIs(config[key], True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'panel.plist'
            path.write_bytes(plistlib.dumps(config))
            assert_plist_identity(path)
            if sys.platform == 'darwin':
                result = CliRunner().run(('/usr/bin/plutil', '-lint', str(path)))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            else:
                self.skipTest('plutil is a macOS-only plist validator')

    def test_foreign_plist_and_symlink_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'agent.plist'
            path.write_bytes(plistlib.dumps(configuration(root=Path('/foreign'))))
            with self.assertRaises(RuntimeError):
                assert_plist_identity(path)
            alias = Path(directory) / 'alias.plist'
            alias.symlink_to(path)
            with self.assertRaises(RuntimeError):
                assert_plist_identity(alias)

    def test_read_only_status_decision(self):
        record = {'pid': 42, 'instance': 'known'}
        health = {'ok': True, **record}
        args = dict(installed=True, configured=True, enabled=True, loaded=True,
                    job_pid=42, health=health, record=record)
        self.assertTrue(supervision_status(**args)['healthy'])
        for key in ('installed', 'configured', 'enabled', 'loaded'):
            self.assertFalse(supervision_status(**{**args, key: False})['healthy'])
        for change in ({'job_pid': 43}, {'health': None}, {'record': None},
                       {'health': {**health, 'instance': 'foreign'}}, {'record': {**record, 'pid': 43}}):
            self.assertFalse(supervision_status(**{**args, **change})['healthy'])
        self.assertTrue(parse_enabled('disabled services = {\n}\n'))
        self.assertFalse(parse_enabled(f'disabled services = {{\n "{LABEL}" => true\n}}'))
        with self.assertRaises(RuntimeError):
            parse_enabled('unexpected')

    def test_controller_status_uses_fakes_and_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plist, prepared, record_path = root / 'installed.plist', root / 'prepared.plist', root / 'owner.json'
            for path in (plist, prepared):
                path.write_bytes(plistlib.dumps(configuration()))
            record = new_record()
            write_record(record_path, record)
            runner = FakeRunner([CommandResult(0, f'state = running\npid = {record["pid"]}\n'),
                                 CommandResult(0, 'disabled services = {\n}\n')])
            http = ServiceHttp(record_path)
            with patch.object(http, 'health', return_value={'ok': True, 'pid': record['pid'], 'instance': record['instance']}):
                result = PanelService(runner, http, plist, prepared).status()
            self.assertTrue(result['healthy'])
            self.assertEqual([argv[1] for argv, _ in runner.calls], ['print', 'print-disabled'])


class OwnershipTests(unittest.TestCase):
    def test_private_record_identity_and_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data/ownership.json'
            record = new_record()
            self.assertIsNone(read_record(path))
            write_record(path, record)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(read_record(path), record)
            remove_record(path, {**record, 'instance': 'foreign'})
            self.assertTrue(path.exists())
            remove_record(path, record)
            self.assertFalse(path.exists())

    def test_foreign_listener_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            http = ServiceHttp(Path(directory) / 'absent.json')
            with patch.object(http, 'occupied', return_value=True), patch.object(http, 'health', return_value=None):
                with self.assertRaisesRegex(RuntimeError, 'foreign listener'):
                    http.assert_owned_listener()
            record = new_record()
            write_record(http.record_path, record)
            with patch.object(http, 'occupied', return_value=True), patch.object(http, 'health', return_value={
                    'pid': record['pid'], 'instance': 'foreign'}):
                with self.assertRaises(RuntimeError):
                    http.assert_owned_listener()

    def test_child_env_contains_no_inherited_secrets(self):
        with patch.dict(os.environ, {'SUPER_SECRET': 'hidden', 'PYTHONPATH': '/bad', 'PATH': '/bad'}):
            env = child_environment()
        self.assertEqual(set(env), {'HOME', 'USER', 'LOGNAME', 'LANG', 'PATH'})
        self.assertEqual(env['PATH'], CHILD_PATH)


if __name__ == '__main__':
    unittest.main()
