"""Exercise reboot ordering without contacting or rebooting any live device."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'rball.sh'


class RballTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.calls = self.root / 'calls'
        self.calls.touch()
        self.script = self.root / 'rball.sh'
        shutil.copy2(SCRIPT, self.script)
        self.env = dict(os.environ, HOME=str(self.home),
                        PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        RBALL_TEST_CALLS=str(self.calls))
        self.helper = self.root / 'safe_reboot.sh'
        self.write_executable(self.helper, 'echo pi-reboot >> "$RBALL_TEST_CALLS"\n')
        self.write_executable(self.bin / 'ssh', '''
identity=no
batch=no
host_check=no
while (( $# > 2 )); do
  case "$1" in
    -n) shift ;;
    -o) case "$2" in
          BatchMode=yes) batch=yes ;;
          StrictHostKeyChecking=yes) host_check=yes ;;
        esac
        shift 2 ;;
    -i) [[ "$2" == "$HOME/.ssh/id_rsa" ]] || exit 98
        identity=yes; shift 2 ;;
    *) exit 97 ;;
  esac
done
[[ "$batch" == yes && "$host_check" == yes ]] || exit 99
if [[ "$1" == ubnt@* && "$identity" != yes ]]; then exit 96; fi
printf '%s:%s\n' "$1" "$2" >> "$RBALL_TEST_CALLS"
[[ "$1" != "${RBALL_TEST_FAIL_TARGET:-}" ]] || exit 255
''')
        self.write_executable(self.bin / 'uptime', 'echo pi-uptime >> "$RBALL_TEST_CALLS"\n')

    @staticmethod
    def write_executable(path, body):
        path.write_text('#!/bin/bash\nset -eu\n' + body)
        path.chmod(0o755)

    def run_script(self, *args):
        return subprocess.run(['/bin/bash', str(self.script), *args],
                              env=self.env, cwd=self.home,
                              text=True, capture_output=True, timeout=5)

    def call_lines(self):
        return self.calls.read_text().splitlines()

    def test_dry_run_only_checks_uptime_in_order(self):
        result = self.run_script('--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.call_lines(), [
            'ubnt@192.168.8.20:uptime', 'root@192.168.6.1:uptime', 'pi-uptime'])
        self.assertIn('Would reboot antenna', result.stdout)
        self.assertIn('Would reboot router', result.stdout)
        self.assertIn('Would detach', result.stdout)
        self.assertFalse((self.home / '.rball-reboot.log').exists())

    def test_live_run_detaches_after_ordered_remote_reboots(self):
        # The real nohup must release the captured SSH-like streams before the
        # delayed Pi helper is allowed to proceed; a fake nohup would miss this.
        gate = self.root / 'gate'
        os.mkfifo(gate)
        self.env['RBALL_TEST_GATE'] = str(gate)
        pid_pipe = self.root / 'pid'
        os.mkfifo(pid_pipe)
        self.env['RBALL_TEST_PID'] = str(pid_pipe)
        self.write_executable(self.bin / 'sleep', '''
[[ "$1" == 5 ]] || exit 95
printf 'delay:%s\n' "$1" >> "$RBALL_TEST_CALLS"
printf '%s\n' "$PPID" > "$RBALL_TEST_PID"
read -r signal < "$RBALL_TEST_GATE"
''')
        complete = self.root / 'complete'
        self.env['RBALL_TEST_COMPLETE'] = str(complete)
        os.mkfifo(complete)
        self.write_executable(self.helper, '''
[[ $(cat) == '' ]] || exit 94
echo pi-reboot >> "$RBALL_TEST_CALLS"
echo completed > "$RBALL_TEST_COMPLETE"
''')
        try:
            result = self.run_script()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.call_lines()[:2], [
                'ubnt@192.168.8.20:reboot', 'root@192.168.6.1:reboot'])
            self.assertNotIn('pi-reboot', self.call_lines())
            self.assertIn('Pi safe reboot queued after 5s', result.stdout)
        finally:
            # Release the child even if the parent-stream assertion fails.
            subprocess.run(['/bin/bash', '-eu', '-c',
                            'read -r pid < "$1"; kill -HUP "$pid"; '
                            'echo go > "$2"; cat "$3"', 'test',
                            str(pid_pipe), str(gate), str(complete)],
                           check=True, timeout=5, capture_output=True)
        self.assertEqual(self.call_lines(), [
            'ubnt@192.168.8.20:reboot', 'root@192.168.6.1:reboot',
            'delay:5', 'pi-reboot'])

    def test_remote_failure_stops_remaining_reboots(self):
        for mode in ((), ('--dry-run',)):
            for target in ('ubnt@192.168.8.20', 'root@192.168.6.1'):
                with self.subTest(mode=mode, target=target):
                    self.calls.write_text('')
                    self.env['RBALL_TEST_FAIL_TARGET'] = target
                    result = self.run_script(*mode)
                    self.assertEqual(result.returncode, 255)
                    action = 'uptime' if mode else 'reboot'
                    expected = [f'ubnt@192.168.8.20:{action}']
                    if target.startswith('root@'):
                        expected.append(f'{target}:{action}')
                    self.assertEqual(self.call_lines(), expected)
                    self.assertIn('stopping before the remaining reboots', result.stderr)
                    self.assertNotIn('queued', result.stdout)

    def test_missing_helper_or_unwritable_log_prevents_remote_changes(self):
        self.helper.chmod(0o644)
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.call_lines(), [])
        self.helper.chmod(0o755)
        (self.home / '.rball-reboot.log').mkdir()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.call_lines(), [])

    def test_help_and_bad_arguments_do_not_contact_devices(self):
        result = self.run_script('--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('--dry-run', result.stdout)
        for args in (('--invalid',), ('',), ('--dry-run', 'extra'), ('--help', 'extra')):
            result = self.run_script(*args)
            self.assertEqual(result.returncode, 2)
        self.assertEqual(self.call_lines(), [])


if __name__ == '__main__':
    unittest.main()
