"""The metered sync's private SSH master: pinned route, options, and teardown rules."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from shared.metered.store import RevisionConflict
from macbook.metered import config, ssh_master
from macbook.metered.ssh_master import Route, tear_down_master
from macbook.metered.sync import PiTransport


class FakeSsh:
    """Answers -G (a new route per call from `routes`), -O exit, ps and the request."""

    def __init__(self, routes, request_code=0, request_error=None, exit_code=0, exit_error=None,
                 ps_lines=None, ps_error=None, g_code=0, request_stderr=''):
        self.routes = list(routes)
        self.request_code, self.request_error = request_code, request_error
        self.request_stderr = request_stderr
        self.exit_code, self.exit_error = exit_code, exit_error
        self.ps_lines, self.ps_error, self.g_code = ps_lines or [], ps_error, g_code
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if '-G' in argv:
            host, path = self.routes.pop(0) if len(self.routes) > 1 else self.routes[0]
            return subprocess.CompletedProcess(
                argv, self.g_code, 'user pi\nhostname %s\ncontrolpath %s\n' % (host, path), '')
        if '-O' in argv:
            assert kwargs['timeout'] == config.TEARDOWN_TIMEOUT
            if self.exit_error:
                raise self.exit_error
            return subprocess.CompletedProcess(argv, self.exit_code, '', '')
        if argv[0] == ssh_master.PS:
            if self.ps_error:
                raise self.ps_error
            return subprocess.CompletedProcess(argv, 0, '\n'.join(self.ps_lines), '')
        if self.request_error:
            raise self.request_error
        return subprocess.CompletedProcess(argv, self.request_code, '{}', self.request_stderr)

    def of(self, flag):
        return [call for call in self.calls if flag in call]

    def requests(self):
        return [c for c in self.calls if c[0] == ssh_master.SSH and '-G' not in c and '-O' not in c]


class SocketCase(unittest.TestCase):
    def setUp(self):
        # AF_UNIX paths are length-limited, so keep the socket under /tmp.
        self.directory = tempfile.TemporaryDirectory(dir='/tmp')
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, 'cm')
        self.listeners = []
        self.bind()

    def bind(self):
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(self.path)
        self.listeners.append(listener)
        self.addCleanup(listener.close)

    def title(self, path=None):
        return 'ssh: %s [mux]' % (path or self.path)


class RequestTest(SocketCase):
    def transport(self, **kwargs):
        ssh = FakeSsh([('192.168.6.103', self.path)], **kwargs)
        return ssh, PiTransport(ssh)

    def test_request_pins_route_and_carries_the_mux_options(self):
        ssh, transport = self.transport()
        transport.request('sync', {})
        argv = ssh.requests()[0]
        for option in ('HostName=192.168.6.103', 'ControlPath=' + self.path, 'ControlMaster=auto',
                       'ControlPersist=%d' % config.CONTROL_PERSIST,
                       'ServerAliveInterval=%d' % config.ALIVE_INTERVAL,
                       'ServerAliveCountMax=%d' % config.ALIVE_COUNT_MAX, 'BatchMode=yes'):
            self.assertIn(option, argv)
        self.assertEqual(ssh.of('-O'), [])

    def test_resolution_failure_runs_plain_and_skips_teardown(self):
        for code in ({'g_code': 255, 'request_code': 255}, {'g_code': 255, 'request_error':
                     subprocess.TimeoutExpired('ssh', 20)}):
            ssh, transport = self.transport(**code)
            with self.assertRaises(RuntimeError):
                transport.request('sync', {})
            self.assertNotIn('ControlMaster=auto', ssh.requests()[0])
            self.assertEqual(ssh.of('-O'), [])

    def test_timeout_and_255_tear_down_the_requests_own_socket_after_a_route_flip(self):
        other = os.path.join(self.directory.name, 'other')
        for kwargs in ({'request_error': subprocess.TimeoutExpired('ssh', 20)}, {'request_code': 255}):
            ssh = FakeSsh([('192.168.6.103', self.path), ('100.64.0.1', other)], exit_code=0, **kwargs)
            with self.assertRaises(RuntimeError):
                PiTransport(ssh).request('sync', {})
            self.assertEqual(len(ssh.of('-G')), 1)  # teardown never re-resolves
            exit_call = ssh.of('-O')[0]
            self.assertIn('ControlPath=' + self.path, exit_call)
            self.assertIn('HostName=192.168.6.103', exit_call)

    def test_bypassed_master_is_torn_down_even_when_the_direct_dial_succeeds(self):
        stderr = ('muxclient: master hello exchange failed\nControlSocket %s already exists, '
                  'disabling multiplexing\n' % self.path)
        ssh, transport = self.transport(request_stderr=stderr)
        self.assertEqual(transport.request('sync', {}), {})
        exit_call = ssh.of('-O')[0]
        self.assertIn('ControlPath=' + self.path, exit_call)

    def test_success_conflict_other_failure_and_missing_binary_keep_the_master(self):
        for code, error in ((0, None), (75, RevisionConflict), (1, RuntimeError)):
            ssh, transport = self.transport(request_code=code)
            if error:
                with self.assertRaises(error):
                    transport.request('sync', {})
            else:
                transport.request('sync', {})
            self.assertEqual(ssh.of('-O'), [])
        ssh, transport = self.transport(request_error=OSError('no ssh'))
        with self.assertRaises(RuntimeError):
            transport.request('sync', {})
        self.assertEqual(ssh.of('-O'), [])


class TeardownTest(SocketCase):
    def route(self):
        return Route('192.168.6.103', self.path)

    def tear(self, **kwargs):
        ssh = FakeSsh([('x', self.path)], **kwargs)
        with patch.object(ssh_master.os, 'kill') as kill:
            tear_down_master(ssh, self.route())
        return ssh, kill

    def test_clean_exit_changes_nothing(self):
        ssh, kill = self.tear()
        self.assertTrue(os.path.exists(self.path))
        self.assertEqual((ssh.of(ssh_master.PS), kill.call_count), ([], 0))

    def test_failed_exit_kills_only_the_exact_title_and_unlinks_the_socket(self):
        lines = ['  11 ' + self.title(self.path + 'x'), ' 22 ssh: other [mux]',
                 '  33 ' + self.title(), ' 44 vim ' + self.title()]
        for kwargs in ({'exit_code': 255}, {'exit_error': subprocess.TimeoutExpired('ssh', 2)},
                       {'exit_error': OSError('boom')}):
            if not os.path.exists(self.path):
                self.bind()
            _, kill = self.tear(ps_lines=lines, **kwargs)
            kill.assert_called_once_with(33, signal.SIGKILL)
            self.assertFalse(os.path.exists(self.path))

    def test_no_kill_when_no_title_matches(self):
        _, kill = self.tear(exit_code=255, ps_lines=[' 5 ssh: elsewhere [mux]'])
        self.assertEqual(kill.call_count, 0)
        self.assertFalse(os.path.exists(self.path))

    def test_changed_inode_is_not_unlinked(self):
        ssh = FakeSsh([('x', self.path)], exit_code=255)
        real_kill = ssh_master.kill_master_by_title

        def replace_socket(run, route):
            real_kill(run, route)
            os.unlink(self.path)
            self.bind()  # a replacement master took the path
        with patch.object(ssh_master, 'kill_master_by_title', replace_socket):
            tear_down_master(ssh, self.route())
        self.assertTrue(os.path.exists(self.path))

    def test_ps_failure_and_kill_failure_are_swallowed(self):
        for kwargs in ({'ps_error': OSError('no ps')},
                       {'ps_error': subprocess.TimeoutExpired('ps', 2)}):
            if not os.path.exists(self.path):
                self.bind()
            self.tear(exit_code=255, **kwargs)
        self.bind() if not os.path.exists(self.path) else None
        ssh = FakeSsh([('x', self.path)], exit_code=255, ps_lines=[' 9 ' + self.title()])
        with patch.object(ssh_master.os, 'kill', side_effect=ProcessLookupError):
            tear_down_master(ssh, self.route())

    def test_regular_file_is_never_unlinked(self):
        regular = os.path.join(self.directory.name, 'regular')
        open(regular, 'w').write('keep')
        ssh = FakeSsh([('x', regular)], exit_code=255)
        tear_down_master(ssh, Route('h', regular))
        self.assertTrue(os.path.exists(regular))


if __name__ == '__main__':
    unittest.main()
