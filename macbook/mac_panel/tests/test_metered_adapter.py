import json
import os
import tempfile
import unittest
from unittest.mock import patch

from macbook.tests.metered_support import MacFixture
from metered_adapter import COMMAND, MeteredAdapter
from panel_contracts import ApiError
from panel_routes import PanelRoutes
from panel_test_support import FakeRunner, auth, json_command, request, running_server


class MeteredPanelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = MacFixture(temporary.name)
        env = patch.dict(os.environ, {'JACOBR_CONFIG_DIR': str(self.fixture.paths.policy.parent)})
        env.start(); self.addCleanup(env.stop)
        self.status = self.fixture.controller.status()

    def test_read_uses_stable_read_only_cli(self):
        runner = FakeRunner([json_command(self.status)])
        self.assertEqual(MeteredAdapter(runner).read(), self.status)
        self.assertEqual(runner.calls[0][0], (*COMMAND, 'status', '--json'))

    def test_mutations_use_argv_not_shell_and_refresh(self):
        cases = [
            ({'action': 'ssid-add', 'ssid': '$(touch nope)'}, ('ssid', 'add', '--', '$(touch nope)')),
            ({'action': 'ssid-add', 'ssid': '-Cafe'}, ('ssid', 'add', '--', '-Cafe')),
            ({'action': 'ssid-remove', 'ssid': 'Cafe', 'revision': 7},
             ('ssid', 'remove', '--revision', '7', '--', 'Cafe')),
            ({'action': 'approve', 'consumer': 'mac.codex_update', 'duration': '1h', 'revision': 2},
             ('approve', 'mac.codex_update', '1h', '--revision', '2')),
            ({'action': 'mode', 'value': 'auto', 'duration': '', 'revision': 2},
             ('mode', 'auto', '--revision', '2')),
            ({'action': 'low-data-mode', 'enabled': True, 'revision': 'missing', 'confirmed': True},
             ('low-data-mode', 'on', '--revision', 'missing', '--confirm')),
        ]
        for body, argv in cases:
            with self.subTest(body=body):
                runner = FakeRunner([json_command({}), json_command(self.status)])
                self.assertEqual(MeteredAdapter(runner).mutate(body), self.status)
                self.assertEqual(runner.calls[0][0], (*COMMAND, *argv))

    def test_reject_unknown_fields_and_boolean_revision_before_execution(self):
        for body in ({'action': 'ssid-remove', 'ssid': 'Cafe', 'revision': True},
                     {'action': 'sync', 'shell': 'whoami'},
                     {'action': 'restore-low-data-mode', 'confirmed': False}):
            runner = FakeRunner()
            with self.assertRaises(ApiError) as error:
                MeteredAdapter(runner).mutate(body)
            self.assertEqual(error.exception.status, 400)
            self.assertEqual(runner.calls, [])

    def test_conflicts_are_http_409(self):
        runner = FakeRunner([json_command({'error': 'reload'}, 75)])
        with self.assertRaises(ApiError) as error:
            MeteredAdapter(runner).mutate({'action': 'ssid-remove', 'ssid': 'Cafe', 'revision': 2})
        self.assertEqual(error.exception.status, 409)

    def test_http_route_keeps_token_origin_and_json_guards(self):
        runner = FakeRunner([json_command(self.status), json_command({}), json_command(self.status)])
        routes = PanelRoutes(runner=runner)
        self.addCleanup(routes.close)
        with running_server(routes) as server:
            code, _, value = request(server, '/api/metered', headers=auth(server))
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(value), self.status)
            body = json.dumps({'action': 'ssid-add', 'ssid': 'Cafe'})
            code, _, _ = request(server, '/api/metered', 'POST', body,
                                 {**auth(server, True), 'Origin': 'https://foreign.invalid'})
            self.assertEqual(code, 403)
            code, _, _ = request(server, '/api/metered', 'POST', body, auth(server, True))
            self.assertEqual(code, 200)
            self.assertEqual(len(runner.calls), 3)


if __name__ == '__main__':
    unittest.main()
