"""Execute the shell playback/check boundaries with temporary command fakes."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from pi.tests.media import test_video_api_wiring as wiring


ROOT = Path(__file__).resolve().parents[3]


class MovieAudioShellTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.events = self.root / 'events'
        self.environment = {
            'HOME': str(self.root), 'PATH': f'{self.bin}:/usr/bin:/bin',
            'XDG_RUNTIME_DIR': str(self.root), 'EVENTS': str(self.events),
        }
        self.command('flock', 'exit "${FLOCK_STATUS:-0}"')
        self.command('sleep', 'echo "sleep:$*" >> "$EVENTS"')
        self.command('timeout', 'echo "timeout:$*" >> "$EVENTS"; shift 2; exec "$@"')
        self.command('logger', 'echo "log:$*" >> "$EVENTS"')
        (self.root / 'sns.sh').write_text(
            'echo "sns:$*" >> "$EVENTS"; exit "${SNS_STATUS:-0}"\n'
        )

    def command(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/bash\n' + body + '\n')
        path.chmod(0o700)

    def run_check(self):
        return subprocess.run(
            ['/bin/bash', str(ROOT / 'pi/scripts/rear_movie_check.sh')],
            env=self.environment, capture_output=True, text=True, timeout=5,
        )

    def test_shell_check_delays_bounds_and_uses_ensure_task(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events.read_text().splitlines(), [
            'sleep:1.5',
            f'timeout:--kill-after=5 45 bash {self.root}/sns.sh ensure_rear_movie',
            'sns:ensure_rear_movie',
        ])

    def test_busy_shell_check_is_coalesced_and_failure_is_logged(self):
        self.environment['FLOCK_STATUS'] = '1'
        self.assertEqual(self.run_check().returncode, 0)
        self.assertFalse(self.events.exists())
        self.environment['FLOCK_STATUS'] = '0'
        self.environment['SNS_STATUS'] = '9'
        self.assertEqual(self.run_check().returncode, 1)
        self.assertIn('Sonos check failed', self.events.read_text())

    def test_legacy_vlc_launch_schedules_check_only_after_start(self):
        source = (ROOT / 'pi/.bashrc').read_text()
        extract = wiring.DeploymentApiWiringTests._extract_bash_function
        scripts = self.root / 'scripts'
        scripts.mkdir()
        (scripts / 'rear_movie_check.sh').write_text(
            (ROOT / 'pi/scripts/rear_movie_check.sh').read_text()
        )
        script = '\n'.join((
            extract(source, 'prep_for_media'), extract(source, 'run_vlc'),
            'kill_media() { echo kill >> "$EVENTS"; }',
            'wake_display() { echo wake >> "$EVENTS"; }',
            'uridecode() { echo "$*"; }',
            'mkvmerge() { echo "{}"; }; node() { echo 0; }',
            'nohup() { echo vlc >> "$EVENTS"; }',
            'vlcnice() { :; }; vlc_jump_to_position() { :; }',
            'run_vlc /movie.mp4', 'wait',
        ))
        result = subprocess.run(
            ['/bin/bash', '-c', script], env=self.environment,
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        events = self.events.read_text().splitlines()
        self.assertEqual(events[:2], ['kill', 'wake'])
        self.assertLess(events.index('vlc'), events.index('sns:ensure_rear_movie'))
        self.assertEqual(events.count('sns:ensure_rear_movie'), 1)

    def test_ensure_wrapper_is_quiet_skips_mixer_and_execs_python(self):
        self.command('amixer', 'echo mixer >> "$EVENTS"; exit 99')
        self.command('python3', 'echo "$$:$*" >> "$EVENTS"; echo "rear movie: ok"')
        process = subprocess.Popen(
            ['/bin/bash', str(ROOT / 'pi/sns.sh'), 'ensure_rear_movie'],
            env=self.environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(stdout, 'rear movie: ok\n')
        self.assertEqual(self.events.read_text().strip(),
                         f'{process.pid}:-c from sonos_tasks import ensure_rear_movie; ensure_rear_movie()')


if __name__ == '__main__':
    unittest.main()
