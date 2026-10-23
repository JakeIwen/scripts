"""Documentation/deployment contracts and terminal pager behavior for shell tricks."""
from pathlib import Path
import os
import platform
import pty
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SHARED = ROOT / 'shared/sh'
HOSTS = {
    'Mac': ('zsh', ROOT / 'macbook/scripts/shell_tricks.zsh'),
    'Pi': ('bash', ROOT / 'pi/scripts/shell_tricks.bash'),
}


class ShellTricksContracts(unittest.TestCase):
    def test_every_documented_host_has_the_tool_and_every_tool_has_help(self):
        common = (SHARED / 'shell_tricks_common.sh').read_text()
        sections = re.split(r'^## ', (SHARED / 'shell_tricks.md').read_text(), flags=re.M)[1:]
        documented = {host: set() for host in HOSTS}
        names = []
        for section in sections:
            name = section.splitlines()[0]
            names.append(name)
            host_line = re.search(r'^Hosts: (.+)$', section, re.M)
            self.assertIsNotNone(host_line, name)
            hosts = host_line[1].split(', ')
            self.assertTrue(set(hosts) <= set(HOSTS), name)
            self.assertRegex(section, r'(?m)^Summary: .+')
            self.assertRegex(section, r'(?m)^Usage: .+')
            for host in hosts:
                documented[host].add(name)
        self.assertEqual(len(names), len(set(names)), 'duplicate help headings')
        for host, (_, source) in HOSTS.items():
            definitions = re.findall(r'^([a-z][a-z0-9_]*)\(\)\s*[({]',
                                     common + source.read_text(), re.M)
            self.assertEqual(len(definitions), len(set(definitions)), host)
            self.assertEqual(set(definitions), documented[host], host)
            self.assertIn('tricks', definitions)
            self.assertLessEqual(len(definitions) - 1, 16, host)

    def test_shell_syntax(self):
        for shell, source in HOSTS.values():
            subprocess.run([shell, '-n', str(source)], check=True)
            subprocess.run([shell, '-n', str(SHARED / 'shell_tricks_common.sh')], check=True)

    def test_terminal_help_uses_pager(self):
        for host, (shell, source) in HOSTS.items():
            if host == 'Mac' and platform.system() != 'Darwin':
                continue  # The Mac behavior entry point states its OS skip separately.
            with self.subTest(host=host), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                installed = home / ('dev/scripts/shared/sh' if host == 'Mac' else 'scripts')
                installed.mkdir(parents=True)
                for name in ('shell_tricks_common.sh', 'shell_tricks.md'):
                    (installed / name).symlink_to(SHARED / name)
                binary = home / 'bin'
                binary.mkdir()
                less = binary / 'less'
                less.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$PAGER_ARGS"\n')
                less.chmod(0o755)
                environment = dict(os.environ, HOME=str(home),
                                   PATH=f'{binary}{os.pathsep}{os.environ["PATH"]}',
                                   PAGER_ARGS=str(home / 'args'))
                master, terminal = pty.openpty()
                try:
                    subprocess.run([shell, '-c', 'set -u; source "$1"; tricks',
                                    'tricks-test', str(source)], env=environment,
                                   stdin=subprocess.DEVNULL, stdout=terminal,
                                   stderr=subprocess.PIPE, check=True, timeout=10)
                finally:
                    os.close(terminal)
                    os.close(master)
                self.assertEqual((home / 'args').read_text().splitlines(),
                                 ['-FRX', '--', str(installed / 'shell_tricks.md')])


if __name__ == '__main__':
    unittest.main()
