import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
from pi.apps.media_organizer import cli
from pi.tests.media._media_organizer_support import Fixture


class CliTests(Fixture, unittest.TestCase):
    def call(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main(list(args))
        return code, json.loads(output.getvalue())

    def test_public_command_shapes(self):
        self.show()
        self.payload('The.Example.S01')
        code, result = self.call('scan')
        self.assertEqual(code, 0)
        self.assertEqual(set(result), {'entries', 'existing_shows'})
        code, plan = self.call('propose', '--classifier', 'offline')
        self.assertEqual(code, 0)
        self.assertIn('rows', plan)
        self.assertEqual(self.call('plan', plan['id'])[1]['id'], plan['id'])
        self.assertEqual(self.call('plans')[1]['plans'][0]['id'], plan['id'])
        self.assertEqual(self.call('status')[1]['new_count'], 1)
        self.assertEqual(self.call('settings', '--set', 'classifier=offline',
                                   'auto_apply_new_shows=false')[1]['classifier'], 'offline')
        code, run = self.call('apply', plan['id'], '--auto')
        self.assertEqual(code, 0)
        self.assertEqual(self.call('run', run['id'])[1]['id'], run['id'])
        self.assertEqual(self.call('runs')[1]['runs'][0]['id'], run['id'])
        self.assertEqual(self.call('undo', run['id'])[1]['items'][0]['status'], 'reverted')
        self.assertEqual(set(self.call('post-download')[1]), {'plan', 'run', 'alias', 'error'})

    def test_bad_inputs_are_one_json_error(self):
        for args in ([], ['unknown'], ['apply'], ['plan', '../bad'],
                     ['settings', '--set', 'auto_apply=maybe'], ['scan', '--wrong']):
            code, result = self.call(*args)
            self.assertEqual(code, 1)
            self.assertEqual(set(result), {'error'})

    def test_alias_delegates_without_polluting_json(self):
        def fake_main(args):
            self.assertEqual(args, ['new'])
            print('diagnostic')
            return 0
        with patch('pi.apps.media_organizer.alias_main.main', side_effect=fake_main), \
                contextlib.redirect_stderr(io.StringIO()):
            code, result = self.call('alias', 'new')
        self.assertEqual((code, result), (0, {'status': 'scheduled'}))

    def test_package_subprocess_json_and_exit(self):
        environment = dict(os.environ, VAN_MEDIA_ORGANIZER_STATE=str(self.state))
        result = subprocess.run([sys.executable, '-m', 'pi.apps.media_organizer', 'settings'],
                                env=environment, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['auto_apply'])
        result = subprocess.run([sys.executable, '-m', 'pi.apps.media_organizer', 'unknown'],
                                env=environment, text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('error', json.loads(result.stdout))
