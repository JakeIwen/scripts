import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from pi.apps.iptorrents_watch import cli, config, state
from pi.tests.media.iptorrents_watch_support import WatcherTest, candidate


class CliTests(WatcherTest):
    def invoke(self, args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(args)
        return code, json.loads(out.getvalue())

    def test_status_shape_and_offline(self):
        code, result = self.invoke(['status'])
        self.assertEqual(code, 0)
        self.assertEqual(set(result), {'login', 'last_check', 'last_error', 'checking', 'wanted_count',
                                      'new_candidate_count', 'tfiles_writable', 'library_available', 'wanted', 'candidates'})
        self.assertEqual(result['login'], 'unknown')

    def test_wantlist_commands_and_candidates(self):
        code, result = self.invoke(['wantlist', 'add', 'Silo', '--season', '1', '--episode', '2'])
        self.assertEqual(code, 0)
        self.assertEqual(result['wanted'][0]['have_episode'], 2)
        for action, expected in [('pause', True), ('resume', False)]:
            code, result = self.invoke(['wantlist', action, 'Silo'])
            self.assertEqual(code, 0)
            self.assertEqual(result['wanted'][0]['paused'], expected)
        with patch('pi.apps.iptorrents_watch.transport.get_json', side_effect=self.library):
            code, result = self.invoke(['wantlist'])
        self.assertEqual(code, 0)
        self.assertEqual(len(result['wanted']), 1)
        state.save_candidates([candidate()])
        self.assertEqual(self.invoke(['candidates'])[1]['candidates'][0]['torrent_id'], '123')
        self.assertEqual(self.invoke(['dismiss', '123'])[1]['status'], 'dismissed')
        self.assertEqual(self.invoke(['candidates'])[1], {'candidates': []})
        self.assertEqual(self.invoke(['wantlist', 'remove', 'Silo']), (0, {'wanted': []}))

    def test_error_shapes(self):
        for args in ([], ['nope'], ['wantlist', 'add'], ['wantlist', 'add', 'Silo', '--season', '-1'],
                     ['download', '999'], ['dismiss', '../bad']):
            with self.subTest(args=args):
                code, result = self.invoke(args)
                self.assertEqual(code, 1)
                self.assertEqual(set(result), {'error'})

    def test_package_entry_from_other_directory_and_json_help(self):
        root = Path(__file__).resolve().parents[3]
        env = {**os.environ, 'PYTHONPATH': str(root), 'PYTHONDONTWRITEBYTECODE': '1',
               'VAN_IPT_STATE': str(config.STATE_DIR), 'VAN_IPT_TFILES': str(config.TFILES_DIR)}
        for args, expected in [(['status'], 0), (['--help'], 0), (['bogus'], 1)]:
            result = subprocess.run([sys.executable, '-P', '-m', 'pi.apps.iptorrents_watch', *args],
                                    cwd=self.root, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stderr)
            self.assertIsInstance(json.loads(result.stdout), dict)
            self.assertEqual(result.stderr, '')

    def test_check_download_success_and_failure_documents(self):
        self.invoke(['wantlist', 'add', 'Silo', '--season', '1', '--episode', '1'])
        with patch('pi.apps.iptorrents_watch.transport.get_json', side_effect=self.library), \
                patch('pi.apps.iptorrents_watch.site.Site.search', return_value=[candidate()]):
            code, result = self.invoke(['check'])
        self.assertEqual(code, 0)
        self.assertEqual(result['new_candidate_count'], 1)
        self.assertFalse(result['checking'])
        with patch('pi.apps.iptorrents_watch.site.Site.download', return_value=b'<html>login</html>'):
            code, result = self.invoke(['download', '123'])
        self.assertEqual(code, 1)
        self.assertEqual(set(result), {'error'})
        self.assertEqual(list(config.TFILES_DIR.iterdir()), [])
        with patch('pi.apps.iptorrents_watch.site.Site.download', return_value=b'd4:infod4:name4:testee'):
            code, result = self.invoke(['download', '123'])
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'downloaded')
        self.assertEqual(result['torrent_id'], '123')
