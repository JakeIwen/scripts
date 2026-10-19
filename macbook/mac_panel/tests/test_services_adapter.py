from pathlib import Path
import plistlib
import tempfile
import unittest

from cli_runner import CommandResult
from panel_contracts import ApiError, ServiceKind
from panel_test_support import FakeRunner
from services_adapter import ServicesAdapter, parse_list, parse_print

LIST = 'PID\tStatus\tLabel\n20\t0\tcom.jacobr.running\n-\t3\tcom.jacobr.idle\n10\t0\tcom.apple.other\n'


class ServicesTests(unittest.TestCase):
    def test_parsers(self):
        self.assertEqual(parse_list(LIST), {'com.jacobr.running': {'pid': 20, 'last_exit': 0},
                                          'com.jacobr.idle': {'pid': None, 'last_exit': 3}})
        self.assertEqual(parse_print('state = running\n pid = 32\n last exit code = -9\n'),
                         {'state': 'running', 'pid': 32, 'last_exit': -9})
        with self.assertRaises(ApiError):
            parse_list('bad 0 com.jacobr.bad')

    def test_union_unloaded_plists_logs_and_status_only_daemons(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agents, daemons = root / 'agents', root / 'daemons'
            agents.mkdir(); daemons.mkdir()
            log = root / 'output.log'
            log.write_text('ordinary\ntoken=topsecret\n')
            for parent, label in ((agents, 'com.jacobr.unloaded'), (daemons, 'com.jacobr.daemon')):
                (parent / f'{label}.plist').write_bytes(plistlib.dumps({'Label': label,
                    'StandardOutPath': str(log), 'StandardErrorPath': str(log)}))
            runner = FakeRunner([CommandResult(0, LIST), CommandResult(1, stderr='not permitted')])
            rows = ServicesAdapter(runner, agents, daemons, uid=501).read()['services']
            self.assertEqual(len(rows), 4)
            unloaded = next(row for row in rows if row['label'].endswith('unloaded'))
            self.assertFalse(unloaded['loaded'])
            self.assertEqual(len(unloaded['logs']), 1)
            self.assertNotIn('topsecret', unloaded['logs'][0]['text'])
            daemon = next(row for row in rows if row['kind'] == ServiceKind.DAEMON)
            self.assertEqual(daemon['state'], 'unknown')
            self.assertIsNone(daemon['loaded'])

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
