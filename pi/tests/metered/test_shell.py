"""Stable shell entry points fail closed and never launch without permission."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]


class ShellTests(unittest.TestCase):
    def test_startup_checks_permission_before_launching_the_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = {
                'pgrep': '#!/bin/sh\nexit 1\n',
                'metered': '#!/bin/sh\nprintf "%s\\n" "$*" > "$CALLS"\nexit "$PERMISSION"\n',
                'torrent': '#!/bin/sh\nprintf launched > "$LAUNCH"\n',
            }
            for name, text in scripts.items():
                path = root / name; path.write_text(text); path.chmod(0o755)
            environment = dict(os.environ, ISW_PGREP=str(root / 'pgrep'),
                ISW_METERED=str(root / 'metered'), ISW_QBIT_BINARY=str(root / 'torrent'),
                CALLS=str(root / 'calls'), LAUNCH=str(root / 'launch'))
            program = 'source "$1"; grep() { printf movingparts; }; start_torrent_client; wait'
            for permission in ('1', '2', '0'):
                result = subprocess.run(['/bin/bash', '-c', program, 'test',
                    str(ROOT / 'pi/scripts/internet_switches.sh')],
                    env=dict(environment, PERMISSION=permission), capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((root / 'calls').read_text().strip(), 'allows pi.torrents')
                self.assertEqual((root / 'launch').exists(), permission == '0')

    def test_aliases_use_host_qualified_approvals_and_missing_cli_is_unknown(self):
        helper = ROOT / 'pi/scripts/metered_shell.bash'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'scripts').mkdir()
            executable = root / 'scripts/vanpi-metered'
            executable.write_text('#!/bin/sh\nprintf "%s\\n" "$*"\nexit "${RESULT:-0}"\n')
            executable.chmod(0o755)
            for command, expected in [('startor', 'approve pi.torrents 1h'),
                                      ('startor 4h', 'approve pi.torrents 4h'),
                                      ('startorx', 'revoke pi.torrents'),
                                      ('meteredAllows pi.torrents', 'allows pi.torrents')]:
                result = subprocess.run(['/bin/bash', '-c', 'source "$1"; ' + command, 'test', str(helper)],
                    env=dict(os.environ, HOME=str(root)), capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)
            executable.unlink()
            for command, expected in [('isMetered', 0), ('meteredAllows pi.torrents', 127)]:
                result = subprocess.run(['/bin/bash', '-c', 'source "$1"; ' + command, 'test', str(helper)],
                    env=dict(os.environ, HOME=str(root)), capture_output=True, text=True)
                self.assertEqual(result.returncode, expected)


if __name__ == '__main__':
    unittest.main()
