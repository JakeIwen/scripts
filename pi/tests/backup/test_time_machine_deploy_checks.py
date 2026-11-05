"""Exercise the deploy/gate CLIs with isolated files and a fixture SSH transport."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from pi import time_machine_deploy_checks as checks


REPO = Path(__file__).resolve().parents[3]
DEPLOYER = 'pi/deploy_time_machine_icloud.sh'
GATE = 'pi/time_machine_deploy_checks.py'


class GateFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        self.env.pop('BASH_ENV', None)
        self.env.pop('ENV', None)

    def copy_sources(self):
        result = subprocess.run([sys.executable, str(REPO / GATE), '--files'],
                                check=True, capture_output=True, text=True)
        sources = result.stdout.splitlines()
        for source in sources:
            destination = self.repo / source
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / source, destination)
        self.env['PYTHONPATH'] = str(self.repo) + ':' + str(self.repo / 'pi/scripts/backup')
        return sources

    def script(self, path, body, python=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(('#!' + sys.executable if python else '#!/bin/bash') + '\n' + body)
        path.chmod(0o700)

    def gate(self):
        return subprocess.run([sys.executable, str(self.repo / GATE), '--run'],
                              cwd=self.root, env=self.env, capture_output=True,
                              text=True, timeout=30)

    def fixture_tests(self):
        for index, (source, _prefix) in enumerate(checks.TARGET_TESTS):
            path = self.repo / source
            if source.endswith('.py'):
                path.write_text('import os, unittest\nfrom pathlib import Path\n'
                                'class TargetTest(unittest.TestCase):\n'
                                ' def test_target(self):\n'
                                '  root = Path(os.environ["TEST_ROOT"])\n'
                                f'  (root / "ready-{index}").touch()\n'
                                f'  self.assertNotEqual(os.environ.get("FAIL_TEST"), "{index}")\n')
            else:
                path.write_text('set -eu\n'
                                f'touch "$TEST_ROOT/ready-{index}"\n'
                                'for attempt in {1..200}; do\n'
                                '  [[ -f "$TEST_ROOT/ready-0" && -f "$TEST_ROOT/ready-1" && '
                                '-f "$TEST_ROOT/ready-2" ]] && break\n'
                                '  /bin/sleep 0.01\ndone\n'
                                '[[ -f "$TEST_ROOT/ready-0" && -f "$TEST_ROOT/ready-1" && '
                                '-f "$TEST_ROOT/ready-2" ]] || exit 91\n'
                                f'echo fixture-test-{index}\n'
                                f'[[ "${{FAIL_TEST:-}}" != "{index}" ]] || exit 42\n')
        bin_dir = self.root / 'bin'
        self.script(bin_dir / 'sudo', '[[ "$1" != -n ]] || shift\n'
                    'if [[ "$1" == /usr/bin/python3 ]]; then shift; exec "$TEST_PYTHON" "$@"; fi\n'
                    'exec "$@"\n')
        self.env.update(TEST_ROOT=str(self.root), TEST_PYTHON=sys.executable,
                        PATH=str(bin_dir) + ':' + os.environ['PATH'])


class GateTests(GateFixture):
    def test_manifest_stages_real_runtime_and_registry_dependencies_without_checkout_imports(self):
        sources = self.copy_sources()
        self.assertEqual(len(sources), len(set(sources)))
        self.assertEqual({name for name in sources if '/test_' in name},
                         {source for source, _prefix in checks.TARGET_TESTS})
        self.assertNotIn('pi/configs/icloud-backup.json', sources)
        self.assertNotIn('pi/services/vanpi-icloud-backup.service', sources)
        for arguments in [('-m', 'pi.generate_backup_lock_registry', '--check'),
                          ('-m', 'unittest', 'pi.tests.backup.test_time_machine_runtime'),
                          ('-m', 'unittest', 'pi.tests.backup.test_backup_admission')]:
            result = subprocess.run([sys.executable, '-B', *arguments], cwd=self.repo,
                                    env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # On Linux these run the real process/lock fixtures; Mac reports its
        # explicit Linux-tool skips, not target-runtime coverage.
        for source, _prefix in checks.TARGET_TESTS:
            if not source.endswith('.sh'):
                continue
            result = subprocess.run(['/bin/bash', str(self.repo / source)], cwd=self.root,
                                    env=self.env, capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_public_gate_runs_exact_commands_concurrently_and_reports_each_failure(self):
        self.copy_sources()
        self.fixture_tests()
        for failure in (None, *map(str, range(len(checks.TARGET_TESTS)))):
            with self.subTest(failure=failure):
                for marker in self.root.glob('ready-*'):
                    marker.unlink()
                if failure is None:
                    self.env.pop('FAIL_TEST', None)
                else:
                    self.env['FAIL_TEST'] = failure
                result = self.gate()
                self.assertEqual(result.returncode, int(failure is not None), result.stdout + result.stderr)
                for source, prefix in checks.TARGET_TESTS:
                    argument = checks.test_command(source, prefix)[-1]
                    self.assertIn(argument, result.stdout)
                self.assertEqual(result.stdout.count(' (exit '), len(checks.TARGET_TESTS))
                self.assertNotIn('exit 91', result.stdout)

    def test_missing_transfer_dependency_fails_instead_of_emitting_partial_payload(self):
        self.copy_sources()
        (self.repo / 'pi/tests/lib.sh').unlink()
        result = subprocess.run([sys.executable, str(self.repo / GATE), '--files'],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('Missing deploy-check source: pi/tests/lib.sh', result.stderr)

    def test_missing_command_is_a_gate_failure_with_diagnostics(self):
        self.copy_sources()
        self.fixture_tests()
        (self.root / 'bin/sudo').unlink()
        self.env['PATH'] = str(self.root / 'bin')
        result = self.gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('FAIL sudo -n /usr/bin/python3', result.stdout)


TRANSPORT = r'''
import os
from pathlib import Path
import subprocess
import sys
args = sys.argv[1:]
while args and args[0] == '-o':
    args = args[2:]
target, *command = args
root = Path(os.environ['TEST_ROOT'])
with (root / 'events').open('a') as stream:
    stream.write('ssh:' + target + ':' + command[0] + '\n')
stage = root / 'stage'
if command[0] == '/usr/bin/mktemp':
    stage.mkdir()
    print('/tmp/vanpi-tm-icloud.ABC123')
elif command[0] == '/bin/tar':
    raise SystemExit(subprocess.call(['/usr/bin/tar', '-xf', '-', '-C', str(stage)]))
else:
    code = sys.stdin.read().replace('cd "$stage"', 'stage="$TEST_STAGE"\ncd "$stage"')
    code = code.replace('/home/pi/scripts/vanpi-metered', str(root / 'bin/metered'))
    code = code.replace('/run/lock/vanpi-backup', str(root / 'locks'))
    for tool in ('python3', 'testparm', 'systemd-analyze', 'systemd-tmpfiles', 'systemctl', 'stat'):
        code = code.replace('/usr/bin/' + tool, str(root / 'bin' / tool))
    (root / 'received.sh').write_text(code)
    raise SystemExit(subprocess.run(['/bin/bash', '-s', *command[2:]],
                                    input=code, text=True).returncode)
'''

PYTHON_SHIM = r'''
import os
from pathlib import Path
import subprocess
import sys
args = sys.argv[1:]
root = Path(os.environ['TEST_ROOT'])
if args == ['run_tests.py']:
    event = 'local-suite'
    (root / 'suite-pythonpath').write_text(os.environ.get('PYTHONPATH', ''))
elif any(argument.endswith('backup_schedule.py') for argument in args):
    event = 'schedule:' + args[-1]
elif args[-1:] == ['--capture-request']:
    event = 'capture-request'
else:
    raise SystemExit(subprocess.call([sys.executable, *args]))
with (root / 'events').open('a') as stream:
    stream.write(event + '\n')
raise SystemExit(43 if os.environ.get('FAIL_EVENT') == event else 0)
'''


class DeployInterfaceTests(GateFixture):
    def deploy_fixture(self):
        self.copy_sources()
        shutil.copyfile(REPO / DEPLOYER, self.repo / DEPLOYER)
        self.fixture_tests()
        self.script(self.repo / '.local/test-venv/bin/python', PYTHON_SHIM, python=True)
        for name, body in [('ssh', TRANSPORT), ('python3', PYTHON_SHIM)]:
            self.script(self.root / 'bin' / name, body, python=True)
        self.env['TEST_PYTHON'] = str(self.root / 'bin/python3')
        self.env['TEST_STAGE'] = str(self.root / 'stage')
        (self.root / 'locks').mkdir(exist_ok=True)
        shutil.copyfile(REPO / 'pi/scripts/samba_require_mount.sh', self.repo / 'samba-gate')
        samba_source = self.repo / 'pi/scripts/samba_require_mount.sh'
        shutil.copyfile(self.repo / 'samba-gate', samba_source)
        self.script(self.root / 'bin/metered', '''
import json, os, sys
from pathlib import Path
if sys.argv[1] == 'admit':
    # The real CLI reads evidence to EOF; replying first can SIGPIPE the producer.
    assert json.load(sys.stdin) == {}
with (Path(os.environ['TEST_ROOT']) / 'events').open('a') as stream:
    stream.write('metered:' + ' '.join(sys.argv[1:]) + '\\n')
''', python=True)
        self.script(self.root / 'bin/testparm',
                    'echo "testparm:$*" >> "$TEST_ROOT/events"\n'
                    'if [[ "${!#}" == --parameter-name=preexec ]]; then\n'
                    ' echo "/home/pi/scripts/samba_require_mount.sh mbp2tbkup"\n'
                    'else echo Yes; fi\n')
        for name in ('systemd-analyze', 'systemd-tmpfiles', 'systemctl'):
            self.script(self.root / 'bin' / name,
                        f'echo "{name}:$*" >> "$TEST_ROOT/events"\n'
                        f'[[ "${{FAIL_EVENT:-}}" != {name} ]] || exit 44\n')
        self.script(self.root / 'bin/stat', 'echo "root pi 770"\n')
        self.script(self.root / 'bin/sha256sum', r'''
import hashlib
import os
from pathlib import Path
import sys
root = Path(os.environ['TEST_ROOT'])
name = sys.argv[1]
if name == '/home/pi/scripts/samba_require_mount.sh':
    path = root / 'repo/samba-gate'
elif name == '/home/pi/scripts/connectivity_status.py':
    path = root / 'repo/pi/scripts/connectivity_status.py'
else:
    path = Path(name)
print(('0' * 64 if os.environ.get('FAIL_EVENT') == 'hash' else
       hashlib.sha256(path.read_bytes()).hexdigest()) + '  ' + name)
''', python=True)
        (self.repo / 'pi/backup_local_deploy.sh').write_text('''
local_backup_previous_hashes() { echo reviewed; }
local_backup_check_bootstrap() {
  echo bootstrap-check >> "$TEST_ROOT/events"
  [[ ${FAIL_EVENT:-} != bootstrap ]] || return 45
}
local_backup_begin() { echo begin >> "$TEST_ROOT/events"; }
local_backup_publish() { echo publish >> "$TEST_ROOT/events"; }
local_backup_commit() { echo commit >> "$TEST_ROOT/events"; }
''')

    def deploy(self, *arguments):
        return subprocess.run(['/bin/bash', str(self.repo / DEPLOYER), *arguments],
                              cwd=self.root, env=self.env, capture_output=True, text=True, timeout=30)

    def events(self):
        return (self.root / 'events').read_text().splitlines()

    def test_check_only_runs_live_checks_but_never_publishes_or_activates(self):
        self.deploy_fixture()
        self.env['PYTHONPATH'] = 'caller-pythonpath'
        result = self.deploy('--check')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / 'suite-pythonpath').read_text(), 'caller-pythonpath')
        events = self.events()
        self.assertIn('local-suite', events)
        self.assertIn('schedule:--check', events)
        self.assertIn('bootstrap-check', events)
        self.assertTrue(any(event.startswith('metered:admit ') for event in events))
        self.assertTrue(any(event.startswith('systemd-analyze:verify ') for event in events))
        self.assertEqual(sum(event.startswith('testparm:') for event in events), 2)
        self.assertFalse(set(events) & {'begin', 'publish', 'commit', 'schedule:--apply', 'capture-request'})
        self.assertFalse(any(event.startswith(('systemctl:', 'systemd-tmpfiles:')) for event in events))
        staged_tests = {str(path.relative_to(self.root / 'stage'))
                        for path in (self.root / 'stage/pi/tests').rglob('test_*')}
        self.assertEqual(staged_tests, {source for source, _prefix in checks.TARGET_TESTS})

    def test_check_requires_bootstrap_readiness_without_attempting_recovery(self):
        self.deploy_fixture()
        self.env['FAIL_EVENT'] = 'bootstrap'
        result = self.deploy('--check')
        self.assertEqual(result.returncode, 45, result.stdout + result.stderr)
        self.assertIn('bootstrap-check', self.events())
        self.assertNotIn('Target checks passed', result.stdout)
        self.assertFalse(set(self.events()) & {'begin', 'publish', 'commit', 'capture-request'})

    def test_check_flag_accepts_target_before_or_after_it(self):
        self.deploy_fixture()
        for arguments in [('--check', 'pi@fixture.lan'), ('pi@fixture.lan', '--check')]:
            result = self.deploy(*arguments)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn('begin', self.events())
            self.assertTrue(all(event.startswith('ssh:pi@fixture.lan:')
                                for event in self.events() if event.startswith('ssh:')))
            shutil.rmtree(self.root / 'stage')
            (self.root / 'events').unlink()

    def test_normal_deploy_retains_transaction_and_activation_order(self):
        self.deploy_fixture()
        result = self.deploy('pi@fixture.lan')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        events = self.events()
        expected = ['local-suite', 'schedule:--check', 'begin', 'publish',
                    'systemd-tmpfiles:', 'systemctl:daemon-reload', 'commit',
                    'schedule:--apply', 'capture-request', 'systemctl:enable --now']
        indices = [next(index for index, event in enumerate(events) if event.startswith(prefix))
                   for prefix in expected]
        self.assertEqual(indices, sorted(indices))
        self.assertTrue(all(event.startswith('ssh:pi@fixture.lan:')
                            for event in events if event.startswith('ssh:')))

    def test_target_and_local_failures_prevent_publication(self):
        for failure in ('test', 'systemd-analyze', 'schedule:--check', 'hash', 'local-suite'):
            with self.subTest(failure=failure):
                self.deploy_fixture()
                if failure == 'test':
                    self.env['FAIL_TEST'] = '1'
                else:
                    self.env['FAIL_EVENT'] = failure
                result = self.deploy('--check' if failure == 'local-suite' else 'pi@fixture.lan')
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn('begin', self.events())
                self.assertNotIn('publish', self.events())
                shutil.rmtree(self.root / 'stage', ignore_errors=True)
                (self.root / 'events').unlink()
                self.env.pop('FAIL_TEST', None)
                self.env.pop('FAIL_EVENT', None)

    def test_invalid_arguments_and_missing_venv_never_contact_target(self):
        self.deploy_fixture()
        for arguments in [('--invalid',), ('--check', '--check'), ('first', 'second'),
                          ('bad target',), ('',), ('--check', '-target')]:
            result = self.deploy(*arguments)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        (self.repo / '.local/test-venv/bin/python').unlink()
        result = self.deploy('--check')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing .local/test-venv/bin/python', result.stderr)
        self.assertFalse((self.root / 'events').exists())


if __name__ == '__main__':
    unittest.main()
