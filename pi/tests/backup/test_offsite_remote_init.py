"""configure-smb and init-remote (offsite_remote_init.py) with restic and rclone doubles.

The launcher's routes (configure-smb holding no lock; configure-smb-run and
init-remote through job-offsite, the snapshot and the frozen worker) are checked
in test_offsite_launcher.py and, with the real worker, test_offsite_end_to_end.py.
"""
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import resource
import stat
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from offsite_config import MANIFEST, RCLONE_VERSION, REMOTE_CONFIG, SMB_TRANSPORT_FIELDS, SNAPSHOT_RCLONE, Config
from offsite_remote import RemoteRepository
from offsite_remote_init import ALREADY_INITIALIZED, SHARE_NOTE, configure_from_pipe, configure_smb, init_remote
from offsite_remote_init import start_configure
from offsite_repository import Commands
from pi.tests.backup.offsite_fixtures import FAKE_SHEBANG
from pi.tests.backup.offsite_remote_fixtures import SMB_PASSWORD, SMB_TRANSPORT, SmbFixture, fake_obscured

FIELDS = {name: SMB_TRANSPORT[name] for name in ('address', 'share', 'path', 'user', 'port')}
SUMMARY_KEYS = {'kind', 'address', 'port', 'share', 'path', 'user', 'labels', 'note'}
SMB_REPOSITORY = 'rclone::smb:Jake_Share/vanpi-restic'
NEW_LABEL = 'NAS-SHARE'
NEW_ID = 'b' * 64
RECORD_LAUNCHER = '''
import json, os, sys
from pathlib import Path
Path(os.environ['LAUNCHER_RECORD']).write_text(json.dumps({'argv': sys.argv[1:], 'stdin': sys.stdin.read()}))
raise SystemExit(7)
'''


def rclone_double(version='rclone v' + RCLONE_VERSION, obscure_status=0):
    """A subprocess.run stand-in answering offsite_smb's `version` and `obscure -`; records each call."""
    calls = []

    def run(argv, **options):
        calls.append((list(argv), options))
        if list(argv[1:]) == ['version']:
            return subprocess.CompletedProcess(argv, 0, version + '\n- os/version: fixture\n', '')
        if list(argv[1:]) == ['obscure', '-']:
            return subprocess.CompletedProcess(argv, obscure_status,
                                               fake_obscured(options['input'].removesuffix('\n')) + '\n', '')
        raise AssertionError(f'unexpected rclone call {argv}')
    return run, calls


def arguments(**changes):
    return SimpleNamespace(**{**FIELDS, **changes})


class ConfigureSmbTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = Config(borg=root / 'borg', ignition=root / 'ignition', state=root / 'state',
                          password=root / 'password' / 'secret', owner=os.getuid())
        self.remote = self.cfg.state / REMOTE_CONFIG

    def configure(self, run=None, **changes):
        run = run or rclone_double()[0]
        values = {**FIELDS, 'password': SMB_PASSWORD, **changes}
        return configure_smb(self.cfg, **values, run=run)

    def test_writes_a_root_only_record_with_exactly_the_shared_folder_keys(self):
        run, calls = rclone_double()
        summary = self.configure(run)
        info = self.remote.stat()
        self.assertEqual((stat.S_IMODE(info.st_mode), info.st_uid), (0o600, os.getuid()))
        self.assertEqual(stat.S_IMODE(self.cfg.state.stat().st_mode), 0o700)
        record = json.loads(self.remote.read_text())
        self.assertEqual(set(record), set(SMB_TRANSPORT_FIELDS))
        self.assertEqual(record, SMB_TRANSPORT)
        self.assertEqual(summary, {**{name: SMB_TRANSPORT[name] for name in SUMMARY_KEYS - {'labels', 'note'}},
                                   'labels': [], 'note': SHARE_NOTE})
        self.assertNotIn('password', json.dumps(summary))
        self.assertNotIn(SMB_PASSWORD, json.dumps(summary))
        # The installed rclone answers both checks; the password only ever goes to obscure's stdin.
        self.assertEqual([argv for argv, _ in calls], [['/usr/local/bin/rclone', 'version'],
                                                      ['/usr/local/bin/rclone', 'obscure', '-']])
        self.assertEqual(calls[1][1]['input'], SMB_PASSWORD + '\n')
        self.assertEqual(calls[1][1]['env'], {'LC_ALL': 'C', 'RCLONE_CONFIG': ''})

    def test_a_rerun_replaces_the_record_atomically_and_lists_registered_labels(self):
        self.configure()
        first = self.remote.stat().st_ino
        marker = {'schema': 1, 'label': 'FORT-MPLS-5TB', 'repo_id': 'a' * 64, 'repo_path': 'vanpi-restic',
                  'priority': 10, 'created': '2026-11-07T00:00:00+00:00'}
        (self.cfg.state / 'repos.json').write_text(json.dumps({'schema': 1, 'repos': {'FORT-MPLS-5TB': marker}}))
        (self.cfg.state / 'repos.json').chmod(0o600)
        summary = self.configure(share='Other_Share', port=4445)
        self.assertNotEqual(self.remote.stat().st_ino, first)
        self.assertEqual(stat.S_IMODE(self.remote.stat().st_mode), 0o600)
        self.assertEqual(json.loads(self.remote.read_text()), {**SMB_TRANSPORT, 'share': 'Other_Share', 'port': 4445})
        self.assertEqual((summary['share'], summary['labels']), ('Other_Share', ['FORT-MPLS-5TB']))
        self.assertEqual(sorted(path.name for path in self.cfg.state.iterdir()), ['remote.json', 'repos.json'])

    def test_every_invalid_field_is_refused_before_rclone_runs_or_anything_is_written(self):
        run = Mock()
        cases = [('address', '192.168.6.20'), ('address', 'nas.local'), ('port', 0), ('port', '445'),
                 ('share', '.hidden'), ('share', 'a/b'), ('path', '..'), ('path', '/abs'), ('path', 'a/b/c/d'),
                 ('user', 'ja ke'), ('user', ''), ('password', 'p' * 15), ('password', 'p' * 128),
                 ('password', SMB_PASSWORD + '\x07')]
        for name, value in cases:
            with self.subTest(name=name, value=value):
                with self.assertRaises(ValueError) as caught:
                    self.configure(run, **{name: value})
                self.assertNotIn(SMB_PASSWORD, str(caught.exception))
        run.assert_not_called()
        self.assertFalse(self.cfg.state.exists())

    def test_a_wrong_rclone_or_a_failed_obscure_leaves_the_old_record(self):
        self.configure()
        before = self.remote.read_bytes()
        for run, message in ((rclone_double(version='rclone v1.70.0')[0], 'is not rclone v' + RCLONE_VERSION),
                             (rclone_double(obscure_status=1)[0], 'rclone obscure failed')):
            with self.subTest(message=message):
                with self.assertRaisesRegex(RuntimeError, message) as caught:
                    self.configure(run, share='Other_Share')
                self.assertNotIn(SMB_PASSWORD, str(caught.exception))
                self.assertEqual(self.remote.read_bytes(), before)

    def test_the_restic_encryption_password_is_refused(self):
        self.cfg.password.parent.mkdir(mode=0o700)
        secret = 'r' * 64
        self.cfg.password.write_text(secret + '\n')
        self.cfg.password.chmod(0o600)
        run = Mock()
        with self.assertRaisesRegex(ValueError, 'must not be the restic encryption password'):
            self.configure(run, password=secret)
        run.assert_not_called()
        self.assertFalse(self.remote.exists())

    def test_the_worker_takes_one_piped_line_and_never_a_terminal(self):
        run = rclone_double()[0]
        summary = configure_from_pipe(self.cfg, arguments(), stdin=io.StringIO(SMB_PASSWORD + '\nignored\n'), run=run)
        self.assertEqual((summary['kind'], json.loads(self.remote.read_text())['password']), ('smb', SMB_PASSWORD))
        # A line without a newline is the whole password; a line over 127 characters stays too long.
        configure_from_pipe(self.cfg, arguments(), stdin=io.StringIO('n' * 20), run=run)
        self.assertEqual(json.loads(self.remote.read_text())['password'], 'n' * 20)
        with self.assertRaisesRegex(ValueError, '16-127 printable'):
            configure_from_pipe(self.cfg, arguments(), stdin=io.StringIO('p' * 300 + '\n'), run=run)
        terminal = Mock(isatty=Mock(return_value=True))
        with self.assertRaisesRegex(RuntimeError, 'run configure-smb'):
            configure_from_pipe(self.cfg, arguments(), stdin=terminal, run=run)
        terminal.readline.assert_not_called()


class StartConfigureTests(unittest.TestCase):
    """configure-smb itself: fields, then the password, holding no lock, then the admitted worker."""

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = Config(borg=self.root / 'borg', ignition=self.root / 'ignition', state=self.root / 'state',
                          password=self.root / 'secret', owner=os.getuid())
        self.record = self.root / 'launcher.json'
        self.launcher = self.root / 'offsite_backup.sh'
        self.launcher.write_text(FAKE_SHEBANG + RECORD_LAUNCHER)
        self.launcher.chmod(0o755)
        self.environment = patch.dict(os.environ, {'LAUNCHER_RECORD': str(self.record)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def start(self, stdin, **kwargs):
        return start_configure(self.cfg, kwargs.pop('args', arguments()), launcher=self.launcher, stdin=stdin,
                               **kwargs)

    def test_a_piped_password_reaches_the_worker_on_its_stdin_only(self):
        self.assertEqual(self.start(io.StringIO(SMB_PASSWORD + '\n')), 7)
        record = json.loads(self.record.read_text())
        self.assertEqual(record['argv'], ['configure-smb-run', '--address=100.64.0.10', '--share=Jake_Share',
                                          '--path=vanpi-restic', '--user=jake', '--port=445'])
        self.assertEqual(record['stdin'], SMB_PASSWORD + '\n')

    def test_a_terminal_password_is_read_with_getpass_before_the_worker_starts(self):
        order = Mock()
        order.prompt.return_value = SMB_PASSWORD
        child = Mock(returncode=0)
        order.popen.return_value = child
        terminal = Mock(isatty=Mock(return_value=True))
        self.assertEqual(self.start(terminal, prompt=order.prompt, popen=order.popen), 0)
        self.assertEqual(order.mock_calls[:2], [call.prompt('DSM password for jake: '),
                                                call.popen([str(self.launcher), 'configure-smb-run',
                                                            *(f'--{key}={value}' for key, value in FIELDS.items())],
                                                           stdin=subprocess.PIPE, text=True)])
        child.communicate.assert_called_once_with(SMB_PASSWORD + '\n')
        terminal.readline.assert_not_called()

    def test_a_bad_field_or_password_refuses_before_the_worker_starts(self):
        prompt, popen = Mock(return_value=SMB_PASSWORD), Mock()
        terminal = Mock(isatty=Mock(return_value=True))
        with self.assertRaisesRegex(ValueError, 'Shared folder name'):
            self.start(terminal, args=arguments(share='a/b'), prompt=prompt, popen=popen)
        prompt.assert_not_called()  # a typo fails before the password is asked for
        for stdin, error in ((io.StringIO('short\n'), ValueError), (io.StringIO(''), ValueError)):
            with self.subTest(stdin=stdin.getvalue()), self.assertRaises(error) as caught:
                self.start(stdin, popen=popen)
            self.assertNotIn('short', str(caught.exception))
        for failure in (EOFError, KeyboardInterrupt):
            with self.subTest(failure=failure), self.assertRaisesRegex(RuntimeError, 'No shared-folder password'):
                self.start(terminal, prompt=Mock(side_effect=failure), popen=popen)
        popen.assert_not_called()
        self.assertFalse(self.record.exists())

    def test_only_root_and_only_the_live_tree_start_it(self):
        popen = Mock()
        for cfg, message in ((Config(borg=self.root, ignition=self.root, owner=os.getuid() + 1), 'as root'),
                             (Config(borg=self.root, ignition=self.root, owner=os.getuid(), code_root=self.root),
                              'never from a code snapshot')):
            with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, message):
                start_configure(cfg, arguments(), stdin=io.StringIO(SMB_PASSWORD + '\n'), popen=popen)
        popen.assert_not_called()

    def test_a_worker_killed_by_a_signal_reports_the_shell_status(self):
        child = Mock(returncode=-15)
        self.assertEqual(self.start(io.StringIO(SMB_PASSWORD + '\n'), popen=Mock(return_value=child)), 143)


class CoreDumpTests(unittest.TestCase):
    """Every Python path that reads the DSM password has core dumps off before it reads it."""

    def setUp(self):
        self.order = Mock()
        patcher = patch('offsite_repository.resource.setrlimit', self.order.setrlimit)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = Config(borg=self.root / 'borg', ignition=self.root / 'ignition', state=self.root / 'state',
                          password=self.root / 'secret', owner=os.getuid())
        self.limit = call.setrlimit(resource.RLIMIT_CORE, (0, 0))

    def test_configure_smb_front_end_disables_them_before_asking_for_the_password(self):
        terminal = Mock(isatty=Mock(return_value=True))
        self.order.prompt.return_value = SMB_PASSWORD
        child = Mock(returncode=0)
        start_configure(self.cfg, arguments(), stdin=terminal, prompt=self.order.prompt,
                        popen=Mock(return_value=child))
        self.assertEqual(self.order.mock_calls[:2], [self.limit, call.prompt('DSM password for jake: ')])

    def test_configure_smb_run_disables_them_before_reading_the_pipe(self):
        pipe = Mock(isatty=Mock(return_value=False), readline=self.order.readline)
        self.order.readline.return_value = SMB_PASSWORD + '\n'
        configure_from_pipe(self.cfg, arguments(), stdin=pipe, run=rclone_double()[0])
        self.assertEqual(self.order.mock_calls[0], self.limit)
        self.assertIn('readline', [row[0] for row in self.order.mock_calls[1:]])

    def test_init_remote_disables_them_before_any_credential_is_read(self):
        with patch('offsite_remote_init.checked_request', side_effect=StopIteration), \
                self.assertRaises(StopIteration):
            init_remote(self.cfg, Mock(), label=NEW_LABEL, priority=30)
        self.assertEqual(self.order.mock_calls, [self.limit])

    def test_every_remote_run_disables_them_before_reading_remote_json(self):
        commands = Mock()
        commands.cfg.commands = {}
        repo = RemoteRepository(self.cfg, commands, 'NAS', registering=True)
        with patch('offsite_remote.transport_config', side_effect=StopIteration), self.assertRaises(StopIteration):
            repo.configure()
        self.assertEqual(self.order.mock_calls, [self.limit])


class InitRemoteTests(unittest.TestCase):
    def setUp(self):
        self.f = SmbFixture()
        self.addCleanup(self.f.close)
        result = self.f.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.environment = patch.dict(os.environ, self.f.env)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.registry = self.f.cfg.state / 'repos.json'
        self.drive = json.loads(self.registry.read_text())['repos'][self.f.label]
        self.update(repo_id=NEW_ID)  # what the restic double's new repository decrypts to

    def update(self, **values):
        self.f.reload()
        self.f.state.update(values)
        self.f.save()

    def init(self, label=NEW_LABEL, priority=30, adopt=None):
        return init_remote(self.f.cfg, Commands(self.f.cfg, self.f.locks()), label=label, priority=priority,
                           adopt=adopt)

    def restic(self, before):
        return [row[1:] for row in self.f.calls()[before:] if row[0] == 'restic']

    def repos(self):
        return json.loads(self.registry.read_text())['repos']

    def test_creates_registers_and_then_verifies_with_a_fresh_repository(self):
        before, secret = len(self.f.calls()), self.f.cfg.password.read_bytes()
        original = RemoteRepository.verify
        verified = []

        def spy(repo):
            verified.append(repo.registering)
            return original(repo)
        with patch.object(RemoteRepository, 'verify', spy):
            summary = self.init()
        self.assertEqual(summary, {'label': NEW_LABEL, 'repo_id': NEW_ID, 'adopted': False})
        self.assertEqual(verified, [False])
        calls = self.restic(before)
        self.assertEqual([row[-1] for row in calls], ['init', 'config', 'config'])
        self.assertTrue(all(row[row.index('--repo') + 1] == SMB_REPOSITORY for row in calls))
        repos = self.repos()
        self.assertEqual(repos[self.f.label], self.drive)
        marker = repos[NEW_LABEL]
        self.assertEqual({key: marker[key] for key in ('schema', 'label', 'repo_id', 'repo_path', 'priority')},
                         {'schema': 1, 'label': NEW_LABEL, 'repo_id': NEW_ID, 'repo_path': 'vanpi-restic',
                          'priority': 30})
        self.assertRegex(marker['created'], r'^\d{4}-\d\d-\d\dT')
        self.assertEqual(stat.S_IMODE(self.registry.stat().st_mode), 0o600)
        self.assertEqual(self.f.cfg.password.read_bytes(), secret)

    def test_refusals_that_need_no_network_run_no_restic_and_register_nothing(self):
        before, registry = len(self.f.calls()), self.registry.read_bytes()
        cases = [({'label': self.f.label}, 'already registered'), ({'label': 'bigboi'}, 'disk_policy'),
                 ({'label': '-x'}, 'Label must'), ({'label': 'L' * 17}, 'Label must'),
                 ({'priority': True}, 'Priority'), ({'priority': -1}, 'Priority'), ({'priority': 2 ** 31}, 'Priority'),
                 ({'priority': '5'}, 'Priority'), ({'adopt': 'B' * 64}, '--adopt'), ({'adopt': 'b' * 63}, '--adopt')]
        for changes, message in cases:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                self.init(**changes)
        self.assertEqual(self.restic(before), [])
        self.assertEqual(self.registry.read_bytes(), registry)

    def test_a_rest_or_missing_remote_json_is_refused_clearly(self):
        before = len(self.f.calls())
        self.f.write_transport({'schema': 1, 'address': '100.64.0.10', 'port': 8088, 'transport_password': 'x' * 48})
        with self.assertRaisesRegex(ValueError, 'kind smb remote.json; a REST receiver'):
            self.init()
        self.f.remote_config.unlink()
        with self.assertRaisesRegex(ValueError, 'run configure-smb first'):
            self.init()
        self.assertEqual(self.restic(before), [])
        self.assertNotIn(NEW_LABEL, self.repos())

    def test_an_existing_repository_is_named_and_never_registered_unasked(self):
        self.update(init_exists=True, repo_id='c' * 64)
        with self.assertRaisesRegex(RuntimeError, f"Existing repository id {'c' * 64} .*--adopt {'c' * 64} only if"):
            self.init()
        self.update(cat_config_fails=True)
        with self.assertRaisesRegex(RuntimeError, 'this Pi cannot open already exists'):
            self.init()
        self.assertNotIn(NEW_LABEL, self.repos())

    def test_the_adopt_hint_survives_a_password_equal_to_the_words_it_keys_on(self):
        # Redaction runs on the displayed error, so classifying from it would lose the hint here.
        password = 'config file already exists'  # what the restic double prints; 26 characters
        self.assertIn(password, ALREADY_INITIALIZED)
        self.f.write_transport({**SMB_TRANSPORT, 'password': password})
        self.update(init_exists=True, repo_id='c' * 64)
        with self.assertRaises(RuntimeError) as caught:
            self.init()
        self.assertIn(f"--adopt {'c' * 64} only if", str(caught.exception))
        self.assertNotIn(password, str(caught.exception))
        self.assertNotIn(NEW_LABEL, self.repos())

    def test_adopt_registers_only_the_given_decrypted_id_without_init(self):
        self.update(init_exists=True, repo_id='c' * 64)
        before = len(self.f.calls())
        with self.assertRaisesRegex(RuntimeError, f"has id {'c' * 64}, not {'d' * 64}; nothing registered"):
            self.init(adopt='d' * 64)
        self.assertNotIn(NEW_LABEL, self.repos())
        self.assertEqual(self.init(adopt='c' * 64), {'label': NEW_LABEL, 'repo_id': 'c' * 64, 'adopted': True})
        self.assertNotIn('init', [row[-1] for row in self.restic(before)])
        self.assertEqual(self.repos()[NEW_LABEL]['repo_id'], 'c' * 64)

    def test_an_id_already_registered_for_another_label_is_refused(self):
        # A mirrored seed keeps its drive's label; a second label for one repository is refused.
        self.update(repo_id=self.drive['repo_id'])
        with self.assertRaisesRegex(RuntimeError, f'already registered as {self.f.label}'):
            self.init(adopt=self.drive['repo_id'])
        self.assertNotIn(NEW_LABEL, self.repos())

    def test_a_failed_confirmation_leaves_the_registration_and_says_so(self):
        with patch.object(RemoteRepository, 'verify', side_effect=RuntimeError('Unknown repository ID')), \
                redirect_stderr(io.StringIO()) as stderr, self.assertRaisesRegex(RuntimeError, 'Unknown repository ID'):
            self.init()
        self.assertIn(f'{NEW_LABEL} is registered as {NEW_ID}', stderr.getvalue())
        self.assertEqual(self.repos()[NEW_LABEL]['repo_id'], NEW_ID)

    def test_a_pi_with_no_repository_yet_creates_the_encryption_password(self):
        self.registry.unlink()
        self.f.cfg.password.unlink()
        self.assertEqual(self.init()['repo_id'], NEW_ID)
        self.assertEqual(stat.S_IMODE(self.f.cfg.password.stat().st_mode), 0o600)
        self.assertEqual(list(self.repos()), [NEW_LABEL])


class SharedFolderCliTests(unittest.TestCase):
    """Through the CLI worker and its real snapshot: init-remote freezes rclone, a local run never does."""

    def setUp(self):
        self.f = SmbFixture()
        self.addCleanup(self.f.close)
        result = self.f.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.f.sources()

    def test_init_remote_runs_restic_with_the_snapshot_rclone(self):
        self.f.reload()
        self.f.state['repo_id'] = NEW_ID
        self.f.save()
        before = len(self.f.executions())
        result = self.f.run('init-remote', '--label', NEW_LABEL, '--priority', '30')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), {'adopted': False, 'label': NEW_LABEL, 'repo_id': NEW_ID})
        restic = [row for row in self.f.executions()[before:] if row['name'] == 'restic']
        self.assertEqual([row['args'][-1] for row in restic], ['init', 'config', 'config'])
        for row in restic:
            program = row['args'][row['args'].index('-o') + 1].removeprefix('rclone.program=')
            self.assertEqual(Path(program).relative_to(self.f.cfg.state / 'code').parts[1:], ('bin', 'rclone'))
        self.assertEqual(self.f.snapshots(), [])
        self.assertNotIn(SMB_PASSWORD, result.stdout + result.stderr)

    def test_configure_smb_run_writes_the_record_from_its_pipe(self):
        result = self.f.run('configure-smb-run', '--address', '100.64.0.11', '--share', 'Other_Share',
                            '--path', 'vanpi-restic', '--user', 'jake', input_text=SMB_PASSWORD + '\n')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual((summary['address'], summary['share'], summary['labels']),
                         ('100.64.0.11', 'Other_Share', [self.f.label]))
        self.assertEqual(json.loads(self.f.remote_config.read_text()),
                         {**SMB_TRANSPORT, 'address': '100.64.0.11', 'share': 'Other_Share'})
        self.assertNotIn(SMB_PASSWORD, result.stdout + result.stderr)
        self.assertEqual(self.f.snapshots(), [])

    def held_snapshot(self, *arguments):
        """(manifest rclone entry, frozen rclone present) of a backup's snapshot while restic is held mid-run."""
        release, started = self.f.root / 'release', self.f.root / 'backup-started'
        release.unlink(missing_ok=True)
        started.unlink(missing_ok=True)
        self.f.reload()
        self.f.state['hold_until'] = str(release)
        self.f.save()
        child = self.f.start('backup', *arguments, '--label', self.f.label, '--only', 'borg')
        found = None
        try:
            deadline = time.monotonic() + self.f.command_timeout
            while not started.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if started.exists():
                found = [(json.loads((root / MANIFEST).read_text())['rclone'], (root / SNAPSHOT_RCLONE).exists())
                         for root in self.f.snapshots()]
        finally:
            release.touch()
            out, err = child.communicate(timeout=self.f.command_timeout)
        self.assertEqual(child.returncode, 0, out + err)
        self.assertIsNotNone(found, 'restic never started: ' + out + err)
        [value] = found
        return value

    def test_only_a_remote_backup_freezes_rclone_for_a_kind_smb_record(self):
        self.assertEqual(self.held_snapshot(), (None, False))
        frozen, present = self.held_snapshot('--remote')
        self.assertTrue(present)
        self.assertEqual(frozen['version'], RCLONE_VERSION)

    def test_an_abbreviated_remote_flag_is_refused(self):
        # The launcher matches the exact --remote; argparse must not accept a prefix of it.
        for command in ('backup', 'check'):
            with self.subTest(command=command):
                result = self.f.run(command, '--rem', '--label', self.f.label)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn('unrecognized arguments: --rem', result.stderr)


if __name__ == '__main__':
    unittest.main()
