import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from panel_contracts import Reply
from panel_test_support import auth, request, running_server
from settings_adapter import SettingsAdapter


class SettingsRoutes:
    def __init__(self):
        self.adapter = SettingsAdapter()

    def dispatch(self, method, path, body=None):
        return Reply(self.adapter.read() if method == 'GET' else self.adapter.save(body))


def fixture_declaration(tool='fixture'):
    return {'tool': tool, 'title': tool.title(), 'description': 'Test', 'settings': [
        {'name': 'count', 'type': 'integer', 'default': 2, 'minimum': 1, 'maximum': 5,
         'description': 'A bounded count'}]}


class SettingsEndpointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.declarations, self.config = self.root / 'declarations', self.root / 'values'
        self.private = self.config / 'declarations'
        self.declarations.mkdir()
        self.private.mkdir(parents=True)
        environment = patch.dict(os.environ, JACOBR_SETTINGS_DIR=str(self.declarations),
                                 JACOBR_CONFIG_DIR=str(self.config))
        environment.start()
        self.addCleanup(environment.stop)

    def declare(self, tool, directory=None):
        (directory or self.declarations).joinpath(tool + '.json').write_text(
            json.dumps(fixture_declaration(tool)))

    def get(self, server):
        status, _, raw = request(server, '/api/settings', headers=auth(server))
        self.assertEqual(status, 200)
        return {record['tool']: record for record in json.loads(raw)['tools']}

    @staticmethod
    def post(server, body):
        return request(server, '/api/settings', 'POST', json.dumps(body), auth(server, True))[0]

    def test_declarations_save_validation_conflict_and_invalid_file(self):
        self.declare('fixture')
        with running_server(SettingsRoutes()) as server:
            record = self.get(server)['fixture']
            self.assertIsNone(record['error'])
            self.assertEqual(record['declaration']['title'], 'Fixture')
            body = {'tool': 'fixture', 'values': {'count': 8}, 'revision': record['state']['revision']}
            self.assertEqual(self.post(server, body), 400)
            body['values']['count'] = 3
            self.assertEqual(self.post(server, body), 200)
            body['values']['count'] = 4
            self.assertEqual(self.post(server, body), 409)
            # An unreadable values file stays on the form as a state error, not a tool error.
            (self.config / 'fixture.json').write_text('{broken')
            record = self.get(server)['fixture']
            self.assertIsNone(record['error'])
            self.assertEqual(record['state']['values'], {'count': 2})
            self.assertTrue(record['state']['error'])

    def test_each_broken_declaration_is_its_own_error_and_other_forms_still_work(self):
        self.declare('working')
        (self.declarations / 'malformed.json').write_text('{broken')
        self.declare('twice')
        self.declare('twice', self.private)
        dangling = self.private / 'dangling.json'
        dangling.symlink_to(self.root / 'elsewhere' / 'dangling.json')
        with running_server(SettingsRoutes()) as server:
            records = self.get(server)
            self.assertEqual(sorted(records), ['dangling', 'malformed', 'twice', 'working'])
            working = records.pop('working')
            self.assertIsNone(working['error'])
            self.assertEqual(working['declaration']['tool'], 'working')
            self.assertEqual(working['state']['values'], {'count': 2})
            for tool, record in records.items():
                with self.subTest(tool=tool):
                    self.assertIsNone(record['declaration'])
                    self.assertIsNone(record['state'])
                    self.assertIn(tool + '.json', record['error'])
            self.assertIn('invalid JSON', records['malformed']['error'])
            self.assertIn(str(self.private / 'twice.json'), records['twice']['error'])
            self.assertIn(str(self.root / 'elsewhere' / 'dangling.json'), records['dangling']['error'])
            for tool in records:
                with self.subTest(save=tool):
                    self.assertEqual(self.post(server, {'tool': tool, 'values': {'count': 3},
                                                        'revision': None}), 400)
            self.assertEqual(self.post(server, {'tool': 'working', 'values': {'count': 3},
                                                'revision': working['state']['revision']}), 200)
        self.assertEqual(sorted(path.name for path in self.config.glob('*.json')), ['working.json'])


if __name__ == '__main__':
    unittest.main()
