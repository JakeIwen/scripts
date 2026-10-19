from pathlib import Path
import plistlib
import tempfile
import unittest

from cli_runner import CommandResult
from panel_contracts import ApiError, LogStream, ServiceKind
from panel_routes import PanelRoutes
from panel_test_support import FakeRunner
from services_adapter import ServicesAdapter, parse_list, parse_print

LIST = 'PID\tStatus\tLabel\n20\t0\tcom.jacobr.running\n-\t3\tcom.jacobr.idle\n10\t0\tcom.apple.other\n'
MISSING = 'Could not find service "com.jacobr.daemon" in domain for system'


def fixture(root):
    agents, daemons = root / 'agents', root / 'daemons'
    agents.mkdir()
    daemons.mkdir()
    log = root / 'output.log'
    log.write_text('ordinary\ntoken=topsecret\n')
    (agents / 'com.jacobr.unloaded.plist').write_bytes(plistlib.dumps({
        'Label': 'com.jacobr.unloaded', 'StandardOutPath': str(log), 'StandardErrorPath': str(log)}))
    (daemons / 'com.jacobr.daemon.plist').write_bytes(plistlib.dumps({
        'Label': 'com.jacobr.daemon', 'StandardOutPath': str(log), 'StandardErrorPath': '/dev/null'}))
    return agents, daemons


class ServicesTests(unittest.TestCase):
    def test_parsers(self):
        self.assertEqual(parse_list(LIST), {'com.jacobr.running': {'pid': 20, 'last_exit': 0},
                                          'com.jacobr.idle': {'pid': None, 'last_exit': 3}})
        self.assertEqual(parse_print('state = running\n pid = 32\n last exit code = -9\n'),
                         {'state': 'running', 'pid': 32, 'last_exit': -9})
        with self.assertRaises(ApiError):
            parse_list('bad 0 com.jacobr.bad')

    def test_inventory_carries_log_metadata_without_tails(self):
        with tempfile.TemporaryDirectory() as directory:
            agents, daemons = fixture(Path(directory))
            runner = FakeRunner([CommandResult(0, LIST), CommandResult(1, stderr=MISSING)])
            rows = ServicesAdapter(runner, agents, daemons, uid=501).read()['services']
            self.assertEqual(len(rows), 4)
            unloaded = next(row for row in rows if row['label'].endswith('unloaded'))
            self.assertFalse(unloaded['loaded'])
            self.assertEqual(len(unloaded['logs']), 1)
            self.assertEqual(set(unloaded['logs'][0]), {'path', 'stream', 'modified', 'error'})
            self.assertEqual(unloaded['logs'][0]['stream'], LogStream.STDOUT)
            self.assertIsInstance(unloaded['logs'][0]['modified'], float)
            daemon = next(row for row in rows if row['kind'] == ServiceKind.DAEMON)
            self.assertEqual(daemon['state'], 'unknown')
            self.assertIsNone(daemon['loaded'])
            self.assertIn('Could not find service', daemon['error'])
            self.assertEqual([log['stream'] for log in daemon['logs']], [LogStream.STDOUT])

    def test_log_tails_are_fetched_per_label_and_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            agents, daemons = fixture(Path(directory))
            runner = FakeRunner([])
            adapter = ServicesAdapter(runner, agents, daemons, uid=501)
            payload = adapter.logs('com.jacobr.unloaded')
            self.assertEqual(payload['label'], 'com.jacobr.unloaded')
            self.assertEqual(len(payload['logs']), 1)
            self.assertIn('ordinary', payload['logs'][0]['text'])
            self.assertNotIn('topsecret', payload['logs'][0]['text'])
            self.assertEqual(adapter.logs('com.jacobr.daemon')['logs'][0]['stream'], LogStream.STDOUT)
            self.assertEqual(runner.calls, [])
            for label, status in (('com.jacobr.missing', 404), ('../etc/passwd', 400), ('com.apple.other', 400)):
                with self.assertRaises(ApiError) as raised:
                    adapter.logs(label)
                self.assertEqual(raised.exception.status, status)
            routes = PanelRoutes(runner=runner)
            try:
                routes.services.agents, routes.services.daemons = agents, daemons
                reply = routes.dispatch('GET', '/api/services/logs/com.jacobr.unloaded')
                self.assertEqual(reply.body['label'], 'com.jacobr.unloaded')
            finally:
                routes.close()

    def test_restart_rechecks_inventory_and_rejects_system_or_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = FakeRunner([CommandResult(0, LIST), CommandResult(0)])
            adapter = ServicesAdapter(runner, root, root, uid=501)
            adapter.restart({'label': 'com.jacobr.running', 'confirmed': True})
            self.assertEqual(runner.calls[-1][0], ('/bin/launchctl', 'kickstart', '-k', 'gui/501/com.jacobr.running'))
            for label in ('com.jacobr.unknown', 'com.jacobr.system'):
                runner.results.append(CommandResult(0, LIST))
                with self.assertRaises(ApiError) as raised:
                    adapter.restart({'label': label, 'confirmed': True})
                self.assertEqual(raised.exception.status, 403)
            with self.assertRaises(ApiError):
                adapter.restart({'label': 'com.jacobr.running', 'confirmed': False})
            with self.assertRaises(ApiError):
                adapter.restart({'label': 'com.jacobr.bad; rm', 'confirmed': True})


if __name__ == '__main__':
    unittest.main()
