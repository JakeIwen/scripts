"""HTTP controls preserve CSRF checks and use real revision-checked stores."""
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from flask import Flask
from pi.apps.metered.config import Paths
from pi.apps.metered.controller import Controller
from pi.apps.van_dashboard.http import reject_cross_origin_mutations
from pi.apps.van_dashboard.routes import metered
from shared.metered.records import UplinkEvidence


class RouteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        paths = Paths(*(root / name for name in ('config', 'policy', 'state', 'mac')))
        controller = Controller(paths, collector=lambda _: UplinkEvidence(100, None, [], []),
                                clock=lambda: 100)
        patch = mock.patch.object(metered, 'metered', controller)
        patch.start(); self.addCleanup(patch.stop)
        app = Flask(__name__)
        app.before_request(reject_cross_origin_mutations)
        app.register_blueprint(metered.bp)
        self.client = app.test_client()

    def test_ssid_edits_are_revision_checked_and_exact(self):
        initial = self.client.get('/api/metered')
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.headers['Cache-Control'], 'no-store')
        first = self.client.post('/api/metered/ssids', json={'op': 'add', 'ssid': ' Denlink ', 'revision': 0})
        self.assertEqual(first.status_code, 200)
        self.assertIn(' Denlink ', first.json['config']['metered_ssids'])
        conflict = self.client.post('/api/metered/ssids', json={'op': 'remove', 'ssid': 'denlink', 'revision': 0})
        self.assertEqual(conflict.status_code, 409)

    def test_browser_mutations_keep_origin_and_header_checks(self):
        body = {'op': 'add', 'ssid': 'hotel', 'revision': 0}
        for headers in ({'Origin': 'https://evil.invalid', 'X-Van-Dashboard': '1'},
                        {'Origin': 'http://localhost'}):
            self.assertEqual(self.client.post('/api/metered/ssids', json=body, headers=headers).status_code, 403)
        response = self.client.post('/api/metered/ssids', json=body,
            headers={'Origin': 'http://localhost', 'X-Van-Dashboard': '1'})
        self.assertEqual(response.status_code, 200)

    def test_policy_cannot_edit_mac_or_create_indefinite_unmetered_mode(self):
        for body in ({'op': 'mode', 'value': 'unmetered', 'revision': 0},
                     {'op': 'exempt', 'consumer': 'mac.codex_update', 'enabled': True, 'revision': 0},
                     {'op': 'mode', 'value': 'unmetered', 'seconds': True, 'revision': 0}):
            self.assertEqual(self.client.post('/api/metered/policy', json=body).status_code, 400)
        result = self.client.post('/api/metered/policy', json={
            'op': 'mode', 'value': 'unmetered', 'seconds': 3600, 'revision': 0})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['policy']['mode']['until'], 3700)


if __name__ == '__main__':
    unittest.main()
