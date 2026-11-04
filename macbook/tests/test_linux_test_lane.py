"""The Linux test lane's Mac-side rules: pins, template, sync selection, CLI args.

rsync's filter semantics and the VM itself are exercised by running the lane
(`macbook/linux_test_lane/linux-test-lane test`); macOS ships openrsync, which
does not implement the GNU rsync rules the VM applies.
"""
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from macbook.linux_test_lane import cli, lima, sync
from macbook.linux_test_lane.pins import PINS_FILE, parse_pins, read_pins

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / 'macbook/linux_test_lane/linux-test-lane'


def git(*args, cwd):
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=cwd,
                   env=env, check=True, capture_output=True)


class PinsTests(unittest.TestCase):
    def test_checked_in_pins_parse_and_provision_can_source_them(self):
        pins = read_pins()
        self.assertTrue(pins.image_url.startswith('https://cloud.debian.org/images/cloud/bookworm/'))
        self.assertRegex(pins.image_digest, r'^sha512:[0-9a-f]{128}$')
        self.assertEqual((pins.user, pins.uid, pins.home), ('pi', 1000, '/home/pi'))
        sourced = subprocess.run(['bash', '-c', 'set -eu; source "$1"; echo "$NODE_VERSION $RCLONE_VERSION"',
                                  '_', str(PINS_FILE)], check=True, capture_output=True, text=True)
        self.assertRegex(sourced.stdout.strip(), r'^\d+\.\d+\.\d+ \d+\.\d+\.\d+$')

    def test_rejects_shell_active_duplicate_missing_and_insecure_pins(self):
        valid = PINS_FILE.read_text()
        cases = {
            'quoted': valid + 'EXTRA="x"\n',
            'expansion': valid + 'EXTRA=$HOME\n',
            'command separator': valid + 'EXTRA=a;reboot\n',
            'duplicate': valid + 'VM_CPUS=2\n',
            'missing': valid.replace('VM_DISK=', '# VM_DISK='),
            'http': valid.replace('DEBIAN_IMAGE_URL=https://', 'DEBIAN_IMAGE_URL=http://'),
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                parse_pins(text)


class TemplateTests(unittest.TestCase):
    def test_template_pins_image_mounts_read_only_and_disables_extras(self):
        pins = read_pins()
        mount = '/Users/x/dev/odd "name"'
        template = lima.render_template(pins, mount)
        self.assertIn(f'- location: {json.dumps(pins.image_url)}\n  arch: aarch64\n'
                      f'  digest: {json.dumps(pins.image_digest)}\n', template)
        self.assertIn(f'- location: {json.dumps(mount)}\n  writable: false\n', template)
        self.assertIn('vmType: vz\n', template)
        self.assertIn('containerd:\n  system: false\n  user: false\n', template)
        self.assertEqual(template.count('  ignore: true\n'), 2)
        self.assertNotIn('latest', template)


class SyncTests(unittest.TestCase):
    def test_synced_paths_drop_mac_state_other_worktrees_and_deleted_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('pi/a.py', 'web/node_modules/x.js', '.local/test-venv/bin/python',
                         'link-target.txt'):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text('x')
            (root / 'dangling').symlink_to(root / 'missing')
            listed = [Path(name) for name in ('pi/a.py', 'pi/deleted.py', 'web/node_modules/x.js',
                                              '.local/test-venv/bin/python', 'dangling',
                                              '.claude/worktrees/other/test_x.py')]
            self.assertEqual(sync.synced_paths(listed, root), ['pi/a.py', 'dangling'])

    def test_filter_rules_protect_vm_state_include_parents_and_escape_wildcards(self):
        rules = sync.filter_rules(['pi/tests/test_a.py', 'odd/[x]*.txt', 'top.txt']).splitlines()
        self.assertEqual(rules[:3], ['P /.git/', 'P /.local/', 'P node_modules/'])
        self.assertEqual(rules[-1], '- *')
        for rule in ('+ /pi/', '+ /pi/tests/', '+ /pi/tests/test_a.py', '+ /odd/',
                     r'+ /odd/\[x]\*.txt', '+ /top.txt'):
            self.assertIn(rule, rules)
        self.assertLess(rules.index('+ /pi/'), rules.index('- *'))
        for bad in ('line\nbreak', 'back\\slash', ' leading'):
            with self.subTest(bad), self.assertRaises(ValueError):
                sync.filter_rules([bad])

    def test_git_sync_keeps_vm_runner_state_and_takes_worktree_head_and_index(self):
        mirror = sync.git_mirror_command('/src/.git', '/vm/checkout')
        self.assertIn('--exclude=/run_tests/', mirror)
        self.assertIn('--exclude=/worktrees/', mirror)
        self.assertEqual(mirror[-2:], ['/src/.git/', '/vm/checkout/.git/'])
        state = sync.git_state_command('/src/.git/worktrees/w', '/vm/checkout')
        self.assertEqual(state[-3:], ['/src/.git/worktrees/w/HEAD', '/src/.git/worktrees/w/index',
                                      '/vm/checkout/.git/'])

    def test_source_mount_is_the_main_checkout_for_linked_worktrees(self):
        with tempfile.TemporaryDirectory() as temp:
            main = Path(temp).resolve() / 'main'
            main.mkdir()
            git('init', '-q', cwd=main)
            git('commit', '-q', '--allow-empty', '-m', 'root', cwd=main)
            inside = main / '.claude/worktrees/inner'
            outside = Path(temp).resolve() / 'outer'
            git('worktree', 'add', '-q', '-b', 'inner', str(inside), cwd=main)
            git('worktree', 'add', '-q', '-b', 'outer', str(outside), cwd=main)
            self.assertEqual(sync.source_mount(main), main)
            self.assertEqual(sync.source_mount(inside), main)
            self.assertEqual(sync.git_dirs(inside), (main / '.git', main / '.git/worktrees/inner'))
            with self.assertRaisesRegex(ValueError, 'outside'):
                sync.source_mount(outside)


class CliTests(unittest.TestCase):
    def arguments(self, *argv):
        return cli.runner_arguments(cli.parser().parse_args(['test', *argv]))

    def test_default_selection_covers_linux_code_and_skips_claude_mods(self):
        self.assertEqual(self.arguments(), ['--path', 'pi', '--path', 'shared', '--path', 'synology',
                                            '--path', 'tests', '--exclude-path', 'shared/claude-mods'])
        self.assertEqual(self.arguments('--path', 'pi/tests/backup', '--force', '-j', '3'),
                         ['--path', 'pi/tests/backup', '--force', '--jobs', '3'])
        self.assertEqual(self.arguments('--exclude-path', 'pi/tests/media')[-2:],
                         ['--exclude-path', 'pi/tests/media'])

    def test_default_paths_exist_in_the_repository(self):
        for path in cli.DEFAULT_PATHS + cli.DEFAULT_EXCLUDES:
            self.assertTrue((ROOT / path).exists(), path)

    def test_provision_stamp_tracks_inputs_and_venv(self):
        self.assertRegex(cli.provision_stamp('/home/pi/v'), r'^[0-9a-f]{64}$')
        self.assertNotEqual(cli.provision_stamp('/home/pi/v'), cli.provision_stamp('/home/pi/w'))

    def test_launcher_reports_missing_lima_and_bad_verbs(self):
        environment = dict(os.environ, PATH='/usr/bin:/bin')
        result = subprocess.run([str(LAUNCHER), 'status'], cwd='/', env=environment,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('brew install lima', result.stderr)
        result = subprocess.run([str(LAUNCHER), 'bogus'], cwd='/', env=environment,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.parser().parse_args(['test', '--jobs', 'many'])


if __name__ == '__main__':
    unittest.main()
