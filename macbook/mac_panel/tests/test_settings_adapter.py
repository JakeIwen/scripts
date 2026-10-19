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


class SettingsEndpointTests(unittest.TestCase):
    def test_declarations_save_validation_conflict_and_invalid_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            declarations, config = root / 'declarations', root / 'values'
            declarations.mkdir()
            declaration = {'tool': 'fixture', 'title': 'Fixture', 'description': 'Test', 'settings': [
                {'name': 'count', 'type': 'integer', 'default': 2, 'minimum': 1, 'maximum': 5,
                 'description': 'A bounded count'}]}
            (declarations / 'fixture.json').write_text(json.dumps(declaration))
            with patch.dict(os.environ, JACOBR_SETTINGS_DIR=str(declarations), JACOBR_CONFIG_DIR=str(config)):
                with running_server(SettingsRoutes()) as server:
                    status, _, raw = request(server, '/api/settings', headers=auth(server))
                    self.assertEqual(status, 200)
                    state = json.loads(raw)['tools'][0]['state']
                    body = {'tool': 'fixture', 'values': {'count': 8}, 'revision': state['revision']}
                    self.assertEqual(request(server, '/api/settings', 'POST', json.dumps(body), auth(server, True))[0], 400)
                    body['values']['count'] = 3
                    self.assertEqual(request(server, '/api/settings', 'POST', json.dumps(body), auth(server, True))[0], 200)
                    body['values']['count'] = 4
                    self.assertEqual(request(server, '/api/settings', 'POST', json.dumps(body), auth(server, True))[0], 409)
                    (config / 'fixture.json').write_text('{broken')
                    _, _, raw = request(server, '/api/settings', headers=auth(server))
                    state = json.loads(raw)['tools'][0]['state']
                    self.assertEqual(state['values'], {'count': 2})
                    self.assertTrue(state['error'])


if __name__ == '__main__':
    unittest.main()
