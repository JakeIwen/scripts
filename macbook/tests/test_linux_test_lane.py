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
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from macbook.linux_test_lane import cli, lima, sync
from macbook.linux_test_lane.pins import PINS_FILE, parse_pins, read_pins
from tests.run_tests_fixture import RunnerCase

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

    def test_destination_is_created_only_below_the_owned_home(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp).resolve()
            destination = home / 'linux-test-lane/checkout'
            with patch.object(sync.pwd, 'getpwuid', return_value=SimpleNamespace(pw_dir=str(home))):
                sync.validate_destination(destination)
                self.assertTrue((destination / '.git').is_dir())
                with self.assertRaises(ValueError):
                    sync.validate_destination(home / 'foreign')

    def test_redirected_ancestors_and_git_are_refused_without_touching_sibling(self):
        for relative in ('linux-test-lane', 'linux-test-lane/checkout',
                         'linux-test-lane/checkout/.git'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temp:
                home = Path(temp).resolve()
                external = home / 'external'
                external.mkdir()
                sentinel = external / 'sentinel'
                sentinel.write_text('retained')
                redirect = home / relative
                redirect.parent.mkdir(parents=True, exist_ok=True)
                redirect.symlink_to(external, target_is_directory=True)
                with patch.object(sync.pwd, 'getpwuid', return_value=SimpleNamespace(pw_dir=str(home))):
                    with self.assertRaises(ValueError):
                        sync.validate_destination(home / 'linux-test-lane/checkout')
                self.assertEqual(sentinel.read_text(), 'retained')
                self.assertEqual(list(external.iterdir()), [sentinel])

    def test_sync_cli_refuses_foreign_destination_before_rsync(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source, external = root / 'source', root / 'external'
            source.mkdir()
            external.mkdir()
            (source / 'sample').write_text('new content')
            sentinel = external / 'sentinel'
            sentinel.write_text('retained')
            commands = (sync.tree_command(source, external),
                        sync.git_mirror_command(source, external),
                        sync.git_state_command(source, external))
            for command in commands:
                with self.subTest(command=command):
                    result = subprocess.run(command, input=sync.filter_rules(['sample']),
                                            text=True, capture_output=True)
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn('Unexpected VM checkout:', result.stderr)
                    self.assertEqual(sentinel.read_text(), 'retained')
                    self.assertEqual(list(external.iterdir()), [sentinel])

    @unittest.skipUnless(sys.platform == 'linux', 'GNU rsync content comparison runs in the Linux lane')
    def test_gnu_rsync_refreshes_same_size_same_mtime_source_and_git_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source, destination, common = (root / name for name in ('source', 'destination', 'common'))
            source.mkdir()
            common.mkdir()
            (destination / '.git').mkdir(parents=True)
            working = source / 'sample'
            working.write_text('good')
            for name in ('HEAD', 'index', 'config'):
                (common / name).write_text('good')
            commands = (sync.tree_command(source, destination),
                        sync.git_mirror_command(common, destination),
                        sync.git_state_command(common, destination))
            def copy():
                for command in commands:
                    # The guard is covered above; execute the exact GNU rsync argv
                    # against disposable paths, not the real lane checkout.
                    subprocess.run(command[4:], input=sync.filter_rules(['sample']),
                                   text=True, check=True, capture_output=True)
            copy()
            for path in (working, *(common / name for name in ('HEAD', 'index', 'config'))):
                metadata = path.stat()
                path.write_text('fail')
                os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
            copy()
            for path in (destination / 'sample', *(destination / '.git' / name
                                                   for name in ('HEAD', 'index', 'config'))):
                self.assertEqual(path.read_text(), 'fail')

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


@unittest.skipUnless(sys.platform == 'linux', 'GNU rsync cache regression runs in the Linux lane')
class SyncedCacheTests(RunnerCase):
    def test_same_size_same_mtime_edit_cannot_reuse_guest_pass(self):
        self.add_tests(sample=(0, False))
        source = self.repo
        self.repo = source.parent / 'guest'
        (self.repo / '.git').mkdir(parents=True)

        def copy():
            commands = (sync.tree_command(source, self.repo),
                        sync.git_mirror_command(source / '.git', self.repo),
                        sync.git_state_command(source / '.git', self.repo))
            for command in commands:
                subprocess.run(command[4:], input=sync.filter_rules(
                    ['run_tests.py', 'data.txt', 'tests/test_sample.py']),
                    text=True, check=True, capture_output=True)

        copy()
        self.assertEqual(self.run_suite()[0], 0)
        self.assertIn('(cached:', self.run_suite()[1])
        path = source / 'tests/test_sample.py'
        before = path.stat()
        path.write_text(path.read_text().replace('if False', 'if  True'))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        copy()
        code, output = self.run_suite()
        self.assertEqual(code, 1, output)
        self.assertNotIn('(cached:', output)
        self.assertEqual(self.executions(), ['sample', 'sample'])


class CliTests(unittest.TestCase):
    def arguments(self, *argv):
        return cli.runner_arguments(cli.parser().parse_args(['test', *argv]))

    def test_default_selection_covers_linux_code_and_skips_claude_mods(self):
        self.assertEqual(self.arguments(), ['--path', 'pi', '--path', 'shared', '--path', 'synology',
                                            '--path', 'tests', '--path', 'macbook/tests/test_linux_test_lane.py',
                                            '--exclude-path', 'shared/claude-mods'])
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

    def test_provision_stamp_tracks_upstream_binary_installers(self):
        before = cli.provision_stamp('/home/pi/v')
        read_bytes = Path.read_bytes
        for relative in ('pi/scripts/backup/install_restic.sh', 'synology/offsite/binaries.py'):
            dependency = ROOT / relative
            def changed(path):
                content = read_bytes(path)
                return content + b'\n# changed pin\n' if path == dependency else content
            with self.subTest(installer=relative), patch.object(Path, 'read_bytes', changed):
                self.assertNotEqual(before, cli.provision_stamp('/home/pi/v'))

    def test_current_stamp_does_not_hide_failed_tool_verification(self):
        pins = read_pins()
        _, venv = cli.vm_paths(pins)
        stamp = cli.provision_stamp(venv)
        missing = subprocess.CalledProcessError(1, ['verify'])
        with patch.object(lima, 'run_in_vm', side_effect=[None, SimpleNamespace(stdout=stamp), missing]) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                cli.ensure_provisioned(pins, [])
        self.assertEqual(run.call_args.args, ('sudo', 'bash', cli.PROVISION, '--verify', venv))

    def test_explicit_repair_reprovisions_current_stamp_after_destination_guard(self):
        pins = read_pins()
        checkout, venv = cli.vm_paths(pins)
        stamp = cli.provision_stamp(venv)
        with patch.object(lima, 'run_in_vm', side_effect=[None, SimpleNamespace(stdout=stamp), None]) as run:
            cli.ensure_provisioned(pins, [], repair=True)
        self.assertEqual([call.args for call in run.call_args_list], [
            tuple(sync.destination_command(checkout)), ('cat', cli.STAMP_FILE),
            ('sudo', 'bash', cli.PROVISION, stamp, venv, '--repair')])
        self.assertTrue(cli.parser().parse_args(['up', '--repair']).repair)

    def test_redirected_lane_is_rejected_before_stale_or_missing_stamp_can_provision(self):
        for stamp, repair in (('stale', False), ('', False), ('current', True)):
            with self.subTest(stamp=stamp, repair=repair), tempfile.TemporaryDirectory() as temp:
                home = Path(temp).resolve()
                external = home / 'external'
                (external / 'venv').mkdir(parents=True)
                sentinel = external / 'venv/sentinel'
                sentinel.write_text('retained')
                (home / 'linux-test-lane').symlink_to(external, target_is_directory=True)
                checkout = str(home / 'linux-test-lane/checkout')

                def guest(*command, **kwargs):
                    if command == tuple(sync.destination_command(checkout)):
                        sync.validate_destination(checkout)
                        return SimpleNamespace(returncode=0)
                    if command == ('cat', cli.STAMP_FILE):
                        return SimpleNamespace(stdout=stamp)
                    self.fail('Provisioning reached a redirected ancestor')

                with patch.object(sync.pwd, 'getpwuid', return_value=SimpleNamespace(pw_dir=str(home))), \
                        patch.object(lima, 'run_in_vm', side_effect=guest) as run:
                    with self.assertRaises(ValueError):
                        cli.ensure_provisioned(SimpleNamespace(home=str(home)), [], repair=repair)
                    run.assert_called_once_with(*sync.destination_command(checkout))
                self.assertEqual(sentinel.read_text(), 'retained')

    def test_suite_uses_fixed_fingerprinted_offsite_binaries(self):
        pins = read_pins()
        checkout, venv = cli.vm_paths(pins)
        args = cli.parser().parse_args(['test', '--path', 'pi/tests/backup'])
        with patch.dict(os.environ, OFFSITE_RESTIC='/foreign/restic',
                        OFFSITE_REST_SERVER='/foreign/rest-server'):
            with patch.object(lima, 'run_in_vm') as run:
                run.return_value.returncode = 7
                self.assertEqual(cli.run_tests(pins, args, []), 7)
        run.assert_called_once_with(
            'env', f'OFFSITE_RESTIC={venv}/bin/offsite-restic',
            f'OFFSITE_REST_SERVER={venv}/bin/offsite-rest-server',
            'OFFSITE_RCLONE=/usr/local/bin/rclone',
            f'{venv}/bin/python', 'run_tests.py', '--path', 'pi/tests/backup',
            workdir=checkout, check=False)

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
