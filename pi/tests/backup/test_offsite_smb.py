"""Shared-folder (SMB) transport: record validation, rclone runtime, repository and CLI behavior.

Command doubles stand in for restic and rclone; the real-binary checks are in
test_offsite_rclone_integration.py (Linux lane), and SMB itself is proved only
by the live acceptance run against the NAS.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from offsite_config import RCLONE_VERSION, TRANSPORT_KINDS, validate_transport
from offsite_remote import RemoteRepository
from offsite_repository import Commands, Interrupted
import offsite_smb
from pi.tests.backup import offsite_fake_command
from pi.tests.backup.offsite_fixtures import FAKE_SHEBANG, REPO_SCRIPTS
from pi.tests.backup.offsite_remote_fixtures import (SECRET, SMB_PASSWORD, SMB_TRANSPORT, RemoteFixture,
                                                     SmbFixture, fake_obscured)

REST = {'schema': 1, 'address': '100.64.0.10', 'port': 8088, 'transport_password': SECRET}
LANE_PINS = REPO_SCRIPTS.parents[1] / 'macbook' / 'linux_test_lane' / 'pins.env'
SMB_REPOSITORY = 'rclone::smb:Jake_Share/vanpi-restic'
SERVE_OPTION = 'rclone.args=serve restic --stdio --append-only'
NAS_ALONE = '''
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('offsite_config', sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules['offsite_config'] = module
spec.loader.exec_module(module)
rest = dict(json.loads(sys.argv[2]), kind='rest', tailscale='/usr/bin/tailscale')
module.validate_transport(rest, extra_fields=('tailscale',))
try:
    module.validate_transport(json.loads(sys.argv[3]))
except ValueError:
    print('smb refused on %d.%d' % sys.version_info[:2])
'''


def secret_forms(secret):
    escaped = json.dumps(secret)[1:-1]
    return {secret, escaped, escaped.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')}


class TransportValidationTests(unittest.TestCase):
    def smb(self, **changes):
        return {**SMB_TRANSPORT, **changes}

    def test_rest_shapes_keep_validating_with_or_without_an_explicit_kind(self):
        self.assertEqual(validate_transport(dict(REST)), REST)
        self.assertEqual(validate_transport(dict(REST, kind='rest'), kinds=TRANSPORT_KINDS)['kind'], 'rest')
        receiver = dict(REST, kind='rest', tailscale='/usr/bin/tailscale')
        self.assertEqual(validate_transport(receiver, extra_fields=('tailscale',)), receiver)
        for broken in (dict(REST, kind='smb'), dict(REST, kind='nfs'), dict(REST, kind=None), dict(REST, kind='REST')):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                validate_transport(broken, kinds=TRANSPORT_KINDS)

    def test_the_receiver_default_refuses_a_shared_folder_record(self):
        self.assertEqual(validate_transport(self.smb(), kinds=TRANSPORT_KINDS), SMB_TRANSPORT)
        for kinds in ((), ('rest',)):
            with self.subTest(kinds=kinds), self.assertRaises(ValueError):
                validate_transport(self.smb(), kinds=kinds)
        with self.assertRaises(ValueError):
            validate_transport(self.smb())

    def test_every_invalid_shared_folder_field_is_refused_without_quoting_the_password(self):
        cases = [('schema', 2), ('schema', True), ('address', '192.168.6.20'), ('address', 'nas.example.com'),
                 ('address', 1684275210), ('port', 0), ('port', 65536), ('port', True), ('port', '445'),
                 ('share', ''), ('share', '.hidden'), ('share', '_x'), ('share', 'a/b'), ('share', 'a' * 65),
                 ('share', 'Jake Share'), ('share', 'Jake_Share\n'), ('path', ''), ('path', '/vanpi-restic'),
                 ('path', 'a//b'), ('path', 'a/b/c/d'), ('path', '.'), ('path', '..'), ('path', 'a/../b'),
                 ('path', 'a/./b'), ('path', 'repo/'), ('path', 7), ('user', ''), ('user', 'u' * 65),
                 ('user', 'ja ke'), ('user', 'jake@nas'), ('password', 'p' * 15), ('password', 'p' * 128),
                 ('password', 'p' * 15 + '\x07'), ('password', 'p' * 15 + '\n'), ('password', 'p' * 15 + '\t'),
                 ('password', 16), ('kind', 'SMB')]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError) as caught:
                    validate_transport(self.smb(**{key: value}), kinds=TRANSPORT_KINDS)
                self.assertNotIn(SMB_PASSWORD, str(caught.exception))
        for record in ({key: value for key, value in SMB_TRANSPORT.items() if key != 'user'},
                       self.smb(transport_password=SECRET), self.smb(extra='x'), [SMB_TRANSPORT], None):
            with self.subTest(record=record), self.assertRaises(ValueError):
                validate_transport(record, kinds=TRANSPORT_KINDS)

    def test_the_boundary_values_are_accepted(self):
        for key, value in [('port', 1), ('port', 65535), ('path', 'a/b.c/d-e_f'), ('share', 'a' * 64),
                           ('user', 'u' * 64), ('password', 'p' * 16), ('password', ' ~é' + 'p' * 124)]:
            with self.subTest(key=key):
                self.assertEqual(validate_transport(self.smb(**{key: value}), kinds=TRANSPORT_KINDS)[key], value)

    def test_the_config_module_still_loads_alone_under_the_system_interpreter(self):
        # The NAS installs offsite_config.py beside protected_json.py only (plan review R4).
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary) / 'offsite_config.py'
            shutil.copy2(REPO_SCRIPTS / 'backup' / 'offsite_config.py', copy)
            result = subprocess.run(['/usr/bin/python3', '-I', '-B', '-c', NAS_ALONE, str(copy), json.dumps(REST),
                                     json.dumps(SMB_TRANSPORT)], cwd=temporary, capture_output=True, text=True,
                                    timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('smb refused', result.stdout)

    def test_the_pinned_rclone_version_matches_the_linux_lane_and_the_double(self):
        pins = dict(line.split('=', 1) for line in LANE_PINS.read_text().splitlines() if '=' in line
                    and not line.startswith('#'))
        self.assertEqual(pins['RCLONE_VERSION'], RCLONE_VERSION)
        self.assertEqual(offsite_fake_command.RCLONE_VERSION_LINE, 'rclone v' + RCLONE_VERSION)


class RcloneRuntimeTests(unittest.TestCase):
    def completed(self, stdout='', returncode=0):
        return Mock(return_value=subprocess.CompletedProcess([], returncode, stdout, 'stderr ' + SMB_PASSWORD))

    def test_repository_options_and_environment(self):
        self.assertEqual(offsite_smb.restic_repository(SMB_TRANSPORT), SMB_REPOSITORY)
        self.assertEqual(offsite_smb.restic_options('/snap/bin/rclone'),
                         ['-o', 'rclone.program=/snap/bin/rclone', '-o', SERVE_OPTION])
        with self.assertRaises(ValueError):
            offsite_smb.restic_options('rclone')
        ambient = {'PATH': '/usr/bin', 'RCLONE_CONFIG': '/home/pi/.config/rclone/rclone.conf',
                   'RCLONE_SMB_HOST': '192.168.6.9', 'rclone_smb_user': 'x', 'RESTIC_PASSWORD': 'p',
                   'HTTP_PROXY': 'h', 'https_proxy': 'h', 'ALL_PROXY': 'h', 'VANPI_BACKUP_JOB_LOCK_FD': '10'}
        environment = offsite_smb.rclone_environment(SMB_TRANSPORT, 'obscured', ambient)
        self.assertEqual(environment, {'PATH': '/usr/bin', 'VANPI_BACKUP_JOB_LOCK_FD': '10', 'RCLONE_CONFIG': '',
                                       'RCLONE_SMB_HOST': '100.64.0.10', 'RCLONE_SMB_PORT': '445',
                                       'RCLONE_SMB_USER': 'jake', 'RCLONE_SMB_PASS': 'obscured'})

    def test_obscure_passes_the_password_on_stdin_with_a_minimal_environment(self):
        obscured = fake_obscured(SMB_PASSWORD)
        run = self.completed(obscured + '\n')
        self.assertEqual(offsite_smb.obscure('/snap/bin/rclone', SMB_PASSWORD, run=run), obscured)
        (argv,), options = run.call_args
        self.assertEqual(argv, ['/snap/bin/rclone', 'obscure', '-'])
        self.assertEqual(options['input'], SMB_PASSWORD + '\n')
        self.assertEqual(options['env'], {'LC_ALL': 'C', 'RCLONE_CONFIG': ''})
        self.assertEqual(options['timeout'], 10)

    def test_rclone_children_run_with_core_dumps_off(self):
        # obscure reads the DSM password; restic gets the same limit from Commands.execute.
        run = self.completed(fake_obscured(SMB_PASSWORD) + '\n')
        offsite_smb.obscure('/snap/bin/rclone', SMB_PASSWORD, run=run)
        preexec = run.call_args.kwargs['preexec_fn']
        with patch('offsite_repository.resource.setrlimit') as setrlimit:
            preexec()
        setrlimit.assert_called_once_with(resource.RLIMIT_CORE, (0, 0))

    def test_obscure_failures_never_carry_output_or_the_password(self):
        timeout = Mock(side_effect=subprocess.TimeoutExpired(['rclone'], 10, output=SMB_PASSWORD))
        for run in (self.completed(SMB_PASSWORD, returncode=1), self.completed(''), self.completed('not base64!'),
                    self.completed(SMB_PASSWORD), timeout, Mock(side_effect=FileNotFoundError(SMB_PASSWORD))):
            with self.subTest(run=run), self.assertRaises(RuntimeError) as caught:
                offsite_smb.obscure('/snap/bin/rclone', SMB_PASSWORD, run=run)
            # Nothing chained either: a traceback would print the original exception's output.
            self.assertTrue(caught.exception.__suppress_context__ or caught.exception.__context__ is None)
            self.assertNotIn(SMB_PASSWORD, str(caught.exception))
            self.assertNotIn('stderr', str(caught.exception))
        with self.assertRaises(ValueError):
            offsite_smb.obscure('rclone', SMB_PASSWORD, run=self.completed('x' * 30))

    def test_version_check_requires_the_pinned_release(self):
        offsite_smb.check_rclone_version('/snap/bin/rclone', self.completed(f'rclone v{RCLONE_VERSION}\n- os/arch: arm64'))
        for run in (self.completed('rclone v1.70.0\n'), self.completed(f'rclone v{RCLONE_VERSION}', returncode=1),
                    self.completed(f'rclone v{RCLONE_VERSION}-beta\n'), Mock(side_effect=PermissionError())):
            with self.subTest(run=run), self.assertRaisesRegex(RuntimeError, 'rclone v' + RCLONE_VERSION):
                offsite_smb.check_rclone_version('/snap/bin/rclone', run)

    def test_lingering_selects_only_servers_started_from_this_program(self):
        frozen = '/var/lib/vanpi-offsite/code/20261107T000000Z-0000abcd/bin/rclone'
        listing = [(10, [frozen, 'serve', 'restic', '--stdio', '--append-only', ':smb:Jake_Share/vanpi-restic']),
                   (11, ['/usr/local/bin/rclone', 'serve', 'restic', '--stdio']),
                   (12, [frozen, 'serve', 'restic']), (13, [frozen, 'serve', 'http', '--stdio']),
                   (14, [frozen + '2', 'serve', 'restic', '--stdio']), (15, []), (16, [frozen]),
                   (17, ['/usr/bin/restic', frozen, 'serve', 'restic', '--stdio']),
                   (18, [frozen, 'serve', 'restic', '--stdio'])]
        self.assertEqual(offsite_smb.lingering(listing, frozen), [10, 18])

    def test_reap_terms_waits_then_kills_only_what_remains(self):
        frozen = '/snap/bin/rclone'
        alive = {10: [frozen, 'serve', 'restic', '--stdio'], 11: [frozen, 'serve', 'restic', '--stdio'],
                 12: ['/bin/sleep', '60']}
        sent, now = [], [0.0]
        def kill(pid, number):
            sent.append((pid, number))
            if pid == 10 and number == signal.SIGTERM:
                del alive[10]
            if pid == 11 and number == signal.SIGKILL:
                del alive[11]
        def sleep(seconds):
            now[0] += seconds
        with patch('sys.stderr'):
            found = offsite_smb.reap(frozen, listing=lambda: list(alive.items()), kill=kill,
                                     clock=lambda: now[0], sleep=sleep)
        self.assertEqual(found, [10, 11])
        self.assertEqual(sent, [(10, signal.SIGTERM), (11, signal.SIGTERM), (11, signal.SIGKILL)])
        self.assertGreaterEqual(now[0], offsite_smb.REAP_SECONDS)

    def test_reap_tolerates_exited_processes_and_waits_for_nothing(self):
        def gone(pid, number):
            raise ProcessLookupError()
        sleep = Mock()
        self.assertEqual(offsite_smb.reap('/snap/bin/rclone', listing=lambda: [], kill=gone, sleep=sleep), [])
        sleep.assert_not_called()
        with patch('sys.stderr'):
            self.assertEqual(offsite_smb.reap('/snap/bin/rclone', kill=gone, sleep=sleep, listing=lambda: [
                (5, ['/snap/bin/rclone', 'serve', 'restic', '--stdio'])] if not sleep.called else []), [5])

    @unittest.skipUnless(sys.platform.startswith('linux'), 'process command lines come from Linux /proc')
    def test_reap_stops_a_real_server_that_ignores_term(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            program = str(root / 'bin' / 'rclone')
            # /bin/sh runs the script named by argv[1], so the process's argv reads
            # exactly like restic's rclone child: <program> serve restic --stdio ...
            (root / 'serve').write_text("trap '' TERM\nwhile :; do sleep 0.1; done\n")
            server = subprocess.Popen([program, 'serve', 'restic', '--stdio', '--append-only', ':smb:s/p'],
                                      executable='/bin/sh', cwd=root, start_new_session=True,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            other = subprocess.Popen(['/usr/local/bin/rclone', 'serve', 'restic', '--stdio'], executable='/bin/sh',
                                     cwd=root, start_new_session=True)
            try:
                deadline = time.monotonic() + 10
                while server.pid not in offsite_smb.lingering(offsite_smb.processes(), program):
                    self.assertLess(time.monotonic(), deadline, 'server never appeared')
                    time.sleep(0.05)
                with patch('sys.stderr'):
                    self.assertEqual(offsite_smb.reap(program, grace=0.5), [server.pid])
                self.assertEqual(server.wait(timeout=10), -signal.SIGKILL)
                self.assertIsNone(other.poll())
            finally:
                for process in (server, other):
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)


EXECUTE_DOUBLE = '''
import json, os, resource, sys, time
mode = os.environ['SMB_TEST_MODE']
if mode == 'env':
    print(json.dumps(dict(os.environ)))
elif mode == 'core':
    print(json.dumps(resource.getrlimit(resource.RLIMIT_CORE)))
elif mode == 'sleep':
    print('started', flush=True)
    time.sleep(60)
else:
    value = os.environ['SMB_TEST_VALUE']
    print(json.dumps({'message_type': 'status', 'current_files': [value], 'id': value}))
    print('plain ' + value + ' ' + os.environ['RCLONE_SMB_PASS'])
    raise SystemExit(int(os.environ.get('SMB_TEST_EXIT', '0')))
'''


class ExecuteTests(unittest.TestCase):
    def setUp(self):
        self.f = RemoteFixture()
        self.addCleanup(self.f.close)
        double = self.f.root / 'restic-double'
        double.write_text(FAKE_SHEBANG + EXECUTE_DOUBLE)
        double.chmod(0o755)
        self.cfg = replace(self.f.cfg, commands={**self.f.cfg.commands, 'restic': str(double),
                                                 'rclone': '/nonexistent/bin/rclone'})
        self.commands = Commands(self.cfg, self.f.locks())
        self.obscured = fake_obscured(SMB_PASSWORD)
        self.rclone_env = offsite_smb.rclone_environment(SMB_TRANSPORT, self.obscured, {})

    def execute(self, mode, accept=(0,), **kwargs):
        environment = {**self.f.env, 'SMB_TEST_MODE': mode, 'RCLONE_CONFIG': '/home/pi/rclone.conf',
                       'RCLONE_FOO': 'ambient', 'RESTIC_REPOSITORY': '/elsewhere', 'http_proxy': 'http://proxy'}
        environment.update(kwargs.pop('env', {}))
        with patch.dict(os.environ, environment, clear=True):
            return self.commands.execute('restic', accept=accept, **kwargs)

    def test_rclone_environment_is_restic_only_and_never_with_rest_auth(self):
        for name, extra in (('restic', {'rest_auth': ('a' * 64, SECRET)}), ('lsblk', {}),
                            ('restic', {'rclone_env': {}}), ('restic', {'rclone_env': {'LD_PRELOAD': 'x'}})):
            options = {'rclone_env': self.rclone_env, **extra}
            with self.subTest(name=name, extra=extra), self.assertRaises(ValueError):
                self.commands.execute(name, accept=(0,), **options)

    def test_restic_gets_only_the_transport_environment_and_no_core_dumps(self):
        with patch('offsite_smb.reap') as reap:
            _, output = self.execute('env', rclone_env=self.rclone_env)
        environment = json.loads(output)
        for key, value in self.rclone_env.items():
            self.assertEqual(environment[key], value)
        for key in ('RCLONE_FOO', 'RESTIC_REPOSITORY', 'http_proxy'):
            self.assertNotIn(key, environment)
        self.assertEqual(environment['RESTIC_PROGRESS_FPS'], '0.2')
        reap.assert_called_once_with('/nonexistent/bin/rclone')
        with patch('offsite_smb.reap') as reap:
            self.assertEqual(json.loads(self.execute('core')[1]), [0, 0])
        reap.assert_not_called()

    def test_parsed_output_stays_raw_while_diagnostics_are_redacted(self):
        # A 16-character password that is also part of a JSON value, plus one JSON escapes.
        for secret in ('a' * 16, SMB_PASSWORD):
            hidden = (secret, self.obscured)
            lines = []
            with self.subTest(secret=secret):
                _, output = self.execute('secrets', env={'SMB_TEST_VALUE': 'x' + secret + 'y'},
                                         rclone_env=self.rclone_env, redact=hidden, line=lines.append)
                event = json.loads(output.splitlines()[0])
                self.assertEqual(event['id'], 'x' + secret + 'y')
                self.assertEqual(json.loads(lines[0]), event)
                with self.assertRaises(RuntimeError) as caught:
                    self.execute('secrets', env={'SMB_TEST_VALUE': secret, 'SMB_TEST_EXIT': '3'},
                                 rclone_env=self.rclone_env, redact=hidden)
                message = str(caught.exception)
                self.assertIn('[redacted]', message)
                for form in (*secret_forms(secret), self.obscured):
                    self.assertNotIn(form, message)

    def test_rclone_servers_are_reaped_after_a_stopped_restic(self):
        def stop():
            raise Interrupted('stopped')
        with patch('offsite_smb.reap') as reap, self.assertRaises(Interrupted):
            self.execute('sleep', rclone_env=self.rclone_env, guard=stop, timeout=None)
        reap.assert_called_once_with('/nonexistent/bin/rclone')


class SmbRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.f = SmbFixture()
        self.addCleanup(self.f.close)
        result = self.f.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.f.sources()
        self.environment = patch.dict(os.environ, self.f.env)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def repository(self, label=None, **kwargs):
        return RemoteRepository(self.f.cfg, Commands(self.f.cfg, self.f.locks()), label or self.f.label, **kwargs)

    def restic_calls(self, before):
        return [row for row in self.f.calls()[before:] if row[0] == 'restic']

    def test_configure_builds_the_rclone_transport_once(self):
        repo = self.repository()
        with patch('offsite_smb.obscure', wraps=offsite_smb.obscure) as obscure, \
                patch('offsite_smb.check_rclone_version', wraps=offsite_smb.check_rclone_version) as version:
            repo.network_guard()
            repo.network_guard()
        self.assertEqual((obscure.call_count, version.call_count), (1, 1))
        obscured = fake_obscured(SMB_PASSWORD)
        self.assertEqual(repo.path, SMB_REPOSITORY)
        self.assertEqual(repo.transport_kind, 'smb')
        self.assertIsNone(repo.auth)
        self.assertEqual(repo.options, ('-o', 'rclone.program=' + str(self.f.rclone), '-o', SERVE_OPTION))
        self.assertEqual(repo.rclone_env, {'RCLONE_CONFIG': '', 'RCLONE_SMB_HOST': '100.64.0.10',
                                           'RCLONE_SMB_PORT': '445', 'RCLONE_SMB_USER': 'jake',
                                           'RCLONE_SMB_PASS': obscured})
        self.assertEqual(repo.redact('x' + SMB_PASSWORD + obscured), 'x[redacted][redacted]')

    def test_configure_refuses_a_reused_password_or_a_wrong_rclone(self):
        restic_password = self.f.cfg.password.read_text().strip()
        self.f.write_transport({**self.f.transport, 'password': restic_password})
        with self.assertRaisesRegex(ValueError, 'must not be the restic encryption password'):
            self.repository().configure()
        self.f.write_transport(self.f.transport)
        wrong = self.f.root / 'old-rclone'
        wrong.write_text(FAKE_SHEBANG + "print('rclone v1.70.0')\n")
        wrong.chmod(0o755)
        self.f.cfg.commands['rclone'] = str(wrong)
        before = len(self.f.calls())
        with self.assertRaisesRegex(RuntimeError, 'is not rclone v' + RCLONE_VERSION):
            self.repository().verify()
        self.assertEqual(self.restic_calls(before), [])

    def test_nothing_but_cat_config_runs_before_the_identity_is_verified(self):
        repo = self.repository()
        before = len(self.f.calls())
        with self.assertRaisesRegex(RuntimeError, 'verified before'):
            repo.restic('check')
        self.assertEqual(self.restic_calls(before), [])
        self.assertEqual(repo.verify()['repo_id'], 'a' * 64)
        self.assertIn('no errors', repo.restic('check'))
        calls = self.restic_calls(before)
        self.assertEqual([row[-1] for row in calls], ['config', 'check'])
        self.f.reload()
        self.f.state['repo_id'] = 'f' * 64
        self.f.save()
        with self.assertRaisesRegex(RuntimeError, 'Unknown repository ID'):
            self.repository().verify()

    def test_registering_runs_only_init_and_cat_config_without_a_marker(self):
        repo = self.repository('NEW-LABEL', registering=True)
        before = len(self.f.calls())
        for command in (('backup', '--stdin'), ('snapshots', '--json'), ('check',), ('forget', 'x'),
                        ('init', '--json'), ('cat', 'config', 'extra'), ('key', 'list'), ()):
            with self.subTest(command=command), self.assertRaisesRegex(RuntimeError, 'only restic init'):
                repo.restic(*command)
        self.assertEqual(self.f.calls()[before:], [])
        repo.restic('init')
        self.assertEqual(json.loads(repo.restic('cat', 'config'))['id'], 'a' * 64)
        self.assertIsNone(repo.marker)
        self.assertTrue(all(SMB_REPOSITORY in row for row in self.restic_calls(before)))
        with self.assertRaisesRegex(RuntimeError, 'no recorded identity'):
            repo.verify()

    def test_registering_guard_skips_the_marker_but_keeps_admission_and_config_checks(self):
        repo = self.repository('NEW-LABEL', registering=True)
        repo.network_guard()
        registry = self.f.cfg.state / 'repos.json'
        value = json.loads(registry.read_text())
        value['repos']['NEW-LABEL'] = {**value['repos'][self.f.label], 'label': 'NEW-LABEL'}
        registry.write_text(json.dumps(value))
        repo.network_guard()
        (self.f.root / 'denied').touch()
        with self.assertRaises(Interrupted):
            repo.network_guard()
        (self.f.root / 'denied').unlink()
        self.f.write_transport({**self.f.transport, 'path': 'other-repo'})
        with self.assertRaises(Interrupted):
            repo.network_guard()

    def test_a_rest_remote_cannot_be_initialized(self):
        self.f.remote_config.write_text(json.dumps(REST))
        with self.assertRaisesRegex(ValueError, 'Only a kind smb remote'):
            self.repository('NEW-LABEL', registering=True).network_guard()


class SmbCliTests(unittest.TestCase):
    def fixture(self, password=SMB_PASSWORD):
        f = SmbFixture(password)
        self.addCleanup(f.close)
        result = f.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        f.sources()
        return f

    def backup(self, f, *extra):
        result = f.run('backup', *extra, '--label', f.label, '--only', 'borg')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_backup_uses_the_frozen_rclone_and_verifies_first_without_leaking_secrets(self):
        f = self.fixture()
        before = len(f.executions())
        status = self.backup(f, '--remote')
        self.assertEqual((status['result'], status['target_kind'], status['transport_kind']), ('ok', 'remote', 'smb'))
        restic = [row for row in f.executions()[before:] if row['name'] == 'restic']
        self.assertEqual(restic[0]['args'][-2:], ['cat', 'config'])
        obscured = fake_obscured(SMB_PASSWORD)
        for row in restic:
            args = row['args']
            self.assertEqual(args[args.index('--repo') + 1], SMB_REPOSITORY)
            program = args[args.index('-o') + 1].removeprefix('rclone.program=')
            self.assertEqual(Path(program).relative_to(f.cfg.state / 'code').parts[1:], ('bin', 'rclone'))
            self.assertIn(SERVE_OPTION, args)
            self.assertEqual({key: row['env'][key] for key in ('RCLONE_CONFIG', 'RCLONE_SMB_PASS')},
                             {'RCLONE_CONFIG': '', 'RCLONE_SMB_PASS': obscured})
        self.assertEqual(f.snapshots(), [])  # removed, frozen rclone included, once restic and rclone are done
        recorded = ''.join(path.read_text() for path in (f.root / 'commands.jsonl', f.cfg.state / 'status.json',
                                                          f.cfg.state / 'history.json'))
        for form in (*secret_forms(SMB_PASSWORD), obscured):
            self.assertNotIn(form, recorded + json.dumps(status))
        check = f.run('check', '--remote', '--label', f.label)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(json.loads((f.cfg.state / 'remote-check.json').read_text())['result'], 'ok')

    def test_a_password_inside_parsed_json_values_does_not_break_the_run(self):
        # 'a' * 16 is part of the fixture repository ID and receipt JSON, which are parsed raw.
        f = self.fixture('a' * 16)
        status = self.backup(f, '--remote')
        self.assertEqual(status['sources']['borg']['result'], 'ok')
        self.assertRegex(status['sources']['borg']['receipt'], '^d{64}$')

    def test_local_and_rest_runs_record_their_transport_kind(self):
        f = self.fixture()
        self.assertIsNone(self.backup(f)['transport_kind'])
        f.remote_config.write_text(json.dumps(REST))
        before = len(f.executions())
        self.assertEqual(self.backup(f, '--remote')['transport_kind'], 'rest')
        for row in (row for row in f.executions()[before:] if row['name'] == 'restic'):
            self.assertNotIn('-o', row['args'])
            self.assertIsNone(row['env']['RCLONE_SMB_PASS'])

    def test_dashboard_availability_reads_a_shared_folder_record(self):
        from offsite_availability import remote_availability
        f = SmbFixture()
        self.addCleanup(f.close)
        values = [json.dumps({'BackendState': 'Running', 'Self': {'Online': True},
                              'Peer': {'nas': {'Online': True, 'TailscaleIPs': ['100.64.0.10']}}}),
                  json.dumps([{'dev': 'tailscale0'}])]
        command = Mock(side_effect=[subprocess.CompletedProcess([], 0, value, '') for value in values])
        self.assertEqual(remote_availability(f.cfg, command=command), {'state': 'unknown', 'reason': 'target-unverified'})
        self.assertNotIn(SMB_PASSWORD, str(command.call_args_list))


if __name__ == '__main__':
    unittest.main()
