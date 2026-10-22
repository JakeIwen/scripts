"""Exercise Amphetamine configuration through the guarded HTTP surface."""
import json
import unittest

from cli_runner import CommandResult
from panel_routes import PanelRoutes
from panel_test_support import FakeRunner, auth, json_command, request, running_server


def saved_state():
    return {'enabled': True, 'delay_seconds': 1200, 'display_sleep_locked': True,
            'low_battery_enabled': True, 'low_battery_percent': 10, 'revision': 'a' * 64,
            'session': {'running': True, 'active': True, 'remaining': 0, 'display_sleep': False,
                        'screen_saver': False, 'closed_display': False}, 'error': None,
            'minimum_minutes': 1, 'maximum_minutes': 240}


class AmphetamineHttpTests(unittest.TestCase):
    def test_read_save_and_cli_conflict_through_authenticated_routes(self):
        runner = FakeRunner([json_command(saved_state()), json_command(saved_state()),
                             CommandResult(3, stderr='Settings changed; refresh')])
        routes = PanelRoutes(runner=runner)
        self.addCleanup(routes.close)
        body = {'enabled': True, 'minutes': 20, 'revision': 'a' * 64}
        with running_server(routes) as server:
            code, _, raw = request(server, '/api/amphetamine', headers=auth(server))
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(raw)['delay_seconds'], 1200)
            code, _, raw = request(server, '/api/amphetamine', 'POST', json.dumps(body), auth(server, True))
            self.assertEqual(code, 200)
            self.assertTrue(json.loads(raw)['enabled'])
            self.assertEqual(runner.calls[-1][0][-6:],
                             ('--enabled', 'on', '--minutes', '20', '--revision', 'a' * 64))
            code, _, raw = request(server, '/api/amphetamine', 'POST', json.dumps(body), auth(server, True))
            self.assertEqual(code, 409)
            self.assertIn('refresh', raw)

    def test_invalid_browser_values_and_untrusted_cli_json_are_rejected(self):
        runner = FakeRunner()
        routes = PanelRoutes(runner=runner)
        self.addCleanup(routes.close)
        with running_server(routes) as server:
            for body in ({'enabled': 'yes', 'minutes': 20, 'revision': 'a' * 64},
                         {'enabled': True, 'minutes': True, 'revision': 'a' * 64},
                         {'enabled': True, 'minutes': 20, 'revision': 'a' * 64, 'argv': []}):
                code, _, _ = request(server, '/api/amphetamine', 'POST', json.dumps(body), auth(server, True))
                self.assertEqual(code, 400)
            self.assertEqual(runner.calls, [])
            runner.results.append(json_command({**saved_state(), 'enabled': 'yes'}))
            code, _, _ = request(server, '/api/amphetamine', headers=auth(server))
            self.assertEqual(code, 502)


if __name__ == '__main__':
    unittest.main()
