"""Exercise the manual refresh with isolated command doubles."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'offsite_tm_refresh.zsh'

STUB = r'''#!/bin/sh
name=${0##*/}
printf '%s %s\n' "$name" "$*" >> "$CALL_LOG"
case "$name" in
  sudo)
    case "$1" in -v|-n) exit 0;; esac
    exec "$@" ;;
  launchctl)
    case "$1" in
      bootout) : > "$STATE" ;;
      bootstrap) : > "$STATE.loaded" ;;
      print) [ -f "$STATE.loaded" ] ;;
    esac ;;
  tmutil) printf 'Running = 0\n' ;;
  mount)
    if [ "$1" = -t ]; then
      printf '%s\n' '//pi@VANPI._smb._tcp.local./mbp2tbkup on /tmp/x/mbp2tbkup (smbfs, nodev, nosuid, mounted by jacobr)'
    fi ;;
  diskutil) exit 0 ;;
  sleep) [ "$1" != 50 ] || /bin/sleep 1 ;;
  ssh)
    case "$*" in
      *smbstatus*)
        case "$SCENARIO" in
          failure) exit 9 ;;
          handles) printf '3\n' ;;
          *) printf '0\n' ;;
        esac ;;
      *systemd-run*) exit 0 ;;
      *'test -e'*) exit 0 ;;
      *is-active*) exit 0 ;;
      *'offsite_backup.sh status'*) printf '%s\n' '{"result": "ok", "last_result": null}' ;;
    esac ;;
esac
'''


class OffsiteTimeMachineRefreshTests(unittest.TestCase):
    def test_syntax(self):
        subprocess.run(['zsh', '-n', str(SCRIPT)], check=True)

    def run_scenario(self, scenario, volume=False):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            commands = root / 'bin'
            commands.mkdir()
            for name in ('sudo', 'launchctl', 'tmutil', 'mount', 'diskutil', 'ssh', 'sleep'):
                command = commands / name
                command.write_text(STUB)
                command.chmod(0o755)
            plist = root / 'Library/LaunchAgents/com.jacobr.vanpi-time-machine.plist'
            plist.parent.mkdir(parents=True)
            plist.touch()
            target = root / 'volume'
            if volume:
                target.mkdir()
            log = root / 'calls'
            env = dict(os.environ, HOME=str(root), PATH=str(commands) + ':' + os.environ['PATH'],
                       CALL_LOG=str(log), STATE=str(root / 'state'), SCENARIO=scenario,
                       OFFSITE_TM_REFRESH_VOLUME=str(target))
            result = subprocess.run(['zsh', str(SCRIPT)], env=env, capture_output=True,
                                    text=True, timeout=15)
            return result, log.read_text().splitlines(), str(target)

    def test_success_sequence(self):
        result, calls, _ = self.run_scenario('success')
        self.assertEqual(result.returncode, 0, result.stderr)
        bootout = next(i for i, line in enumerate(calls) if line.startswith('launchctl bootout'))
        unmount = next(i for i, line in enumerate(calls) if line.startswith('diskutil unmount /tmp/x/mbp2tbkup'))
        handles = [i for i, line in enumerate(calls) if line.startswith('ssh ') and 'smbstatus' in line]
        start = next(i for i, line in enumerate(calls) if 'systemd-run' in line)
        bootstrap = next(i for i, line in enumerate(calls) if line.startswith('launchctl bootstrap'))
        self.assertLess(bootout, unmount)
        self.assertLess(unmount, handles[-1])
        self.assertLess(handles[-1], start)
        self.assertLess(start, bootstrap)
        self.assertEqual(result.stdout.splitlines()[-1], 'ok ')

    def test_handle_failure_reloads_agent(self):
        for scenario in ('failure', 'handles'):
            with self.subTest(scenario=scenario):
                result, calls, _ = self.run_scenario(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any('systemd-run' in line for line in calls))
                self.assertTrue(any(line.startswith('launchctl bootstrap') for line in calls))

    def test_missing_store_preserves_volume(self):
        result, calls, volume = self.run_scenario('success', volume=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(line == 'diskutil unmount ' + volume for line in calls))
        self.assertIn('Cannot find the APFS physical store', result.stderr)
        self.assertTrue(any(line.startswith('launchctl bootstrap') for line in calls))


if __name__ == '__main__':
    unittest.main()
