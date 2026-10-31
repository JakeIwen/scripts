"""Public updater commands: independent cadence, fail-closed gates and settings ownership."""
from contextlib import redirect_stdout
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import tool_automation

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('codex_update', REPO / 'shared/sh/codex_update.py')
codex_update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(codex_update)
NOW = 1_800_000_000.0


class FakeMachine:
    """Run a real local permission stub, but never a real installer or SSH connection."""

    def __init__(self, pi=None, update_codes=None, versions=None, permission_error=None):
        self.pi = pi
        self.update_codes = update_codes or {}
        self.versions = versions or {}
        self.permission_error = permission_error
        self.calls = []
        self.options = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        self.options.append(kwargs)
        if len(argv) == 3 and argv[1] == 'allows':
            if self.permission_error:
                raise self.permission_error
            return subprocess.run(argv, **kwargs)
        if argv[0] == '/usr/bin/ssh':
            answer = self.pi
            return self.reply(json.dumps(answer), 0 if answer is not None else 255)
        name = Path(argv[0]).name
        if argv[1:] == ['--version']:
            versions = self.versions.setdefault(name, ['1.0.0', '1.1.0'])
            version = versions.pop(0) if len(versions) > 1 else versions[0]
            return self.reply(f'codex-cli {version}\n' if name == 'codex' else f'{version} (Claude Code)\n')
        if argv[1:] == ['update']:
            assert kwargs['env']['PATH'].startswith(str(Path(argv[0]).parent) + ':')
            expected = codex_update.TOOLS[name].environment
            assert all(kwargs['env'][key] == value for key, value in expected.items())
            return self.reply('installed\n', self.update_codes.get(name, 0))
        raise AssertionError(f'unexpected command {argv}')

    @staticmethod
    def reply(stdout, code=0):
        return subprocess.CompletedProcess([], code, stdout, '')

    def updates(self):
        return [Path(call[0]).name for call in self.calls if call[1:] == ['update']]


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.settings = self.root / '.claude/settings.json'
        self.settings.parent.mkdir()
        self.settings.write_text('{"env":{"KEEP":"yes"},"hooks":{"Stop":[]},"model":"opus"}\n')
        self.cli = self.root / 'metered'
        self.allow()

    def allow(self, code=0):
        self.cli.write_text(f'#!/bin/sh\n[ "$1" = allows ] || exit 2\nexit {code}\n')
        self.cli.chmod(0o755)

    def host(self, machine, is_pi=True, now=NOW):
        return codex_update.Host(is_pi=is_pi, home=self.root, metered_cli=self.cli,
                                 state_dir=self.root / 'state', run=machine, now=lambda: now)

    def main(self, argv, host):
        output = io.StringIO()
        with redirect_stdout(output):
            code = codex_update.main(argv, host)
        return code, output.getvalue()

    def state(self):
        machine = FakeMachine()
        _, output = self.main(['status', '--json'], self.host(machine))
        self.assertEqual(machine.calls, [])
        return json.loads(output)

    def test_updates_both_then_each_waits_a_week(self):
        machine = FakeMachine()
        code, output = self.main(['run'], self.host(machine))
        self.assertEqual(code, 0)
        self.assertEqual(machine.updates(), ['codex', 'claude'])
        self.assertIn('codex updated: 1.0.0 -> 1.1.0', output)
        self.assertIn('claude updated: 1.0.0 -> 1.1.0', output)
        self.assertEqual({s['last_success'] for s in self.state().values()}, {NOW})
        later = FakeMachine()
        self.main(['run'], self.host(later, now=NOW + 6 * 86400))
        self.assertEqual(later.calls, [])
        self.main(['run'], self.host(later, now=NOW + 7 * 86400))
        self.assertEqual(later.updates(), ['codex', 'claude'])

    def test_per_tool_success_does_not_delay_the_other(self):
        first = FakeMachine(update_codes={'claude': 1})
        self.assertEqual(self.main(['run'], self.host(first))[0], 1)
        self.assertEqual(self.state()['claude']['outcome'], 'failed')
        self.assertNotIn('last_success', self.state()['claude'])
        retry = FakeMachine()
        self.main(['run'], self.host(retry, now=NOW + 60))
        self.assertEqual(retry.updates(), ['claude'])
        self.assertEqual(self.state()['codex']['last_success'], NOW)
        next_week = FakeMachine()
        self.main(['run'], self.host(next_week, now=NOW + 7 * 86400))
        self.assertEqual(next_week.updates(), ['codex'])

    def test_tool_selection_and_current_result(self):
        machine = FakeMachine(versions={'claude': ['2.1.314']})
        self.main(['run', '--tool', 'claude'], self.host(machine))
        self.assertEqual(machine.updates(), ['claude'])
        self.assertEqual(self.state()['claude']['outcome'], 'current')
        self.assertEqual(self.state()['codex'], {})

    def test_denied_missing_failing_and_timed_out_permission_defer(self):
        for failure in ('denied', 'missing', 'failed', 'timeout'):
            with self.subTest(failure=failure):
                self.allow(1 if failure == 'denied' else 2)
                if failure == 'missing':
                    self.cli.unlink()
                error = subprocess.TimeoutExpired(['metered'], 60) if failure == 'timeout' else None
                machine = FakeMachine(permission_error=error)
                code, output = self.main(['run', '--force'], self.host(machine))
                self.assertEqual((code, machine.updates()), (0, []))
                self.assertIn('deferred: metered or unknown', output)
                for state in self.state().values():
                    self.assertEqual((state['outcome'], state['reason']), ('deferred', 'metered or unknown'))
                self.assertTrue(all(options['timeout'] == 60 for options in machine.options))

    def test_host_local_permission_ids_and_no_ssh_gate(self):
        for is_pi, prefix in ((True, 'pi'), (False, 'mac')):
            with self.subTest(prefix=prefix):
                machine = FakeMachine()
                self.main(['run', '--force'], self.host(machine, is_pi=is_pi))
                gates = [call for call in machine.calls if call[1:2] == ['allows']]
                self.assertEqual(gates, [[str(self.cli), 'allows', f'{prefix}.{tool}_update']
                                         for tool in ('codex', 'claude')])
                self.assertFalse(any(call[0] == '/usr/bin/ssh' for call in machine.calls))

    def test_force_skips_wait_but_not_permission(self):
        self.main(['run'], self.host(FakeMachine()))
        self.allow(1)
        machine = FakeMachine()
        self.main(['run', '--force'], self.host(machine, now=NOW + 60))
        self.assertEqual(machine.updates(), [])
        self.assertEqual({s['last_success'] for s in self.state().values()}, {NOW})

    def test_legacy_codex_state_preserves_cadence_and_version(self):
        (self.root / 'state').mkdir()
        (self.root / 'state/state.json').write_text(json.dumps({
            'outcome': 'current', 'version': '0.164.0', 'last_success': NOW,
        }))
        machine = FakeMachine()
        self.main(['run'], self.host(machine, now=NOW + 60))
        self.assertEqual(machine.updates(), ['claude'])
        self.assertEqual(self.state()['codex']['version'], '0.164.0')
        self.assertEqual(self.state()['codex']['last_success'], NOW)

    def test_settings_merge_preserves_every_other_key_and_is_idempotent(self):
        original = json.loads(self.settings.read_text())
        machine = FakeMachine()
        self.main(['run', '--tool', 'claude'], self.host(machine))
        merged = json.loads(self.settings.read_text())
        self.assertEqual(merged['env'].pop('DISABLE_AUTOUPDATER'), '1')
        self.assertEqual(merged, original)
        before = (self.settings.read_bytes(), self.settings.stat().st_ino, self.settings.stat().st_mtime_ns)
        self.main(['run', '--force', '--tool', 'claude'], self.host(machine))
        self.assertEqual((self.settings.read_bytes(), self.settings.stat().st_ino,
                          self.settings.stat().st_mtime_ns), before)
        self.assertEqual(self.settings.stat().st_mode & 0o777, 0o600)

    def test_settings_are_disabled_even_when_denied_or_not_due(self):
        self.allow(1)
        self.main(['run'], self.host(FakeMachine()))
        self.assertEqual(json.loads(self.settings.read_text())['env']['DISABLE_AUTOUPDATER'], '1')
        self.allow()
        self.main(['run'], self.host(FakeMachine()))
        self.settings.write_text('{"env":{"DISABLE_AUTOUPDATER":"0","KEEP":"value"}}')
        machine = FakeMachine()
        self.main(['run'], self.host(machine, now=NOW + 60))
        self.assertEqual(machine.calls, [])
        self.assertEqual(json.loads(self.settings.read_text())['env'],
                         {'DISABLE_AUTOUPDATER': '1', 'KEEP': 'value'})

    def test_settings_merge_adds_missing_env(self):
        self.settings.write_text('{"permissions":{"allow":["Read"]}}')
        self.main(['run', '--tool', 'claude'], self.host(FakeMachine()))
        self.assertEqual(json.loads(self.settings.read_text()),
                         {'permissions': {'allow': ['Read']}, 'env': {'DISABLE_AUTOUPDATER': '1'}})

    def test_invalid_settings_fail_only_claude_without_rewriting(self):
        for raw in ('{bad json', '[]', '{"env":[]}', '{"env":null}',
                    '{"model":"a","model":"b"}', '{"value":NaN}'):
            with self.subTest(raw=raw):
                self.settings.write_text(raw)
                machine = FakeMachine()
                code, _ = self.main(['run', '--force'], self.host(machine))
                self.assertEqual(code, 1)
                self.assertEqual(machine.updates(), ['codex'])
                self.assertEqual(self.settings.read_text(), raw)
                self.assertEqual(self.state()['claude']['outcome'], 'failed')
                self.assertNotIn(raw, self.state()['claude']['reason'])

    def test_missing_and_symlinked_settings_are_not_created_or_replaced(self):
        self.settings.unlink()
        machine = FakeMachine()
        self.assertEqual(self.main(['run', '--tool', 'claude'], self.host(machine))[0], 1)
        self.assertFalse(self.settings.exists())
        target = self.root / 'other-settings'
        target.write_text('{}')
        self.settings.symlink_to(target)
        self.assertEqual(self.main(['run', '--tool', 'claude'], self.host(machine))[0], 1)
        self.assertTrue(self.settings.is_symlink())
        self.assertEqual(target.read_text(), '{}')
        self.assertEqual(machine.calls, [])

    def test_settings_publication_replaces_inode_without_partial_write(self):
        raw = self.settings.read_bytes()
        with self.settings.open('rb') as old_reader:
            self.main(['run', '--tool', 'claude'], self.host(FakeMachine()))
            self.assertEqual(old_reader.read(), raw)
            self.assertNotEqual(os.fstat(old_reader.fileno()).st_ino, self.settings.stat().st_ino)
        self.assertEqual(json.loads(self.settings.read_text())['env']['DISABLE_AUTOUPDATER'], '1')
        self.assertEqual(list(self.settings.parent.glob('.settings.json.*')), [])

    def test_failed_atomic_replace_keeps_original_and_cleans_temporary(self):
        original = self.settings.read_bytes()
        replace = os.replace

        def fail_settings(source, destination):
            if Path(destination) == self.settings:
                raise OSError('fixture publication failure')
            return replace(source, destination)

        machine = FakeMachine()
        with patch.object(codex_update.os, 'replace', side_effect=fail_settings):
            self.assertEqual(self.main(['run', '--tool', 'claude'], self.host(machine))[0], 1)
        self.assertEqual(self.settings.read_bytes(), original)
        self.assertEqual(list(self.settings.parent.glob('.settings.json.*')), [])
        self.assertEqual(machine.calls, [])

    def test_busy_lock_prevents_settings_and_updates(self):
        state_dir = self.root / 'state'
        state_dir.mkdir()
        before = self.settings.read_bytes()
        with (state_dir / 'lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            machine = FakeMachine()
            code, output = self.main(['run'], self.host(machine))
        self.assertEqual((code, machine.calls), (0, []))
        self.assertIn('another run holds the lock', output)
        self.assertEqual(self.settings.read_bytes(), before)

    def test_permission_status_is_read_only(self):
        before = self.settings.read_bytes()
        code, output = self.main(['uplink', '--tool', 'claude', '--json'], self.host(FakeMachine()))
        self.assertEqual((code, json.loads(output)), (0, {'allowed': True, 'reason': 'permitted'}))
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertFalse((self.root / 'state').exists())

    def test_panel_record_covers_both_tools_on_both_hosts(self):
        self.main(['run'], self.host(FakeMachine()))
        pi_state = {name: {'outcome': 'deferred', 'reason': 'metered or unknown', 'version': '1.1.0',
                           'last_check': NOW, 'last_success': NOW - 20 * 86400}
                    for name in ('codex', 'claude')}
        machine = FakeMachine(pi=pi_state)
        _, output = self.main(['status', '--automation'], self.host(machine, is_pi=False))
        record = tool_automation.validate_status(json.loads(output))
        self.assertEqual(record['level'], 'warn')
        labels = [field['label'] for field in record['fields']]
        for machine_name in ('Mac', 'Pi'):
            for tool in ('Codex', 'Claude'):
                self.assertIn(f'{machine_name} {tool} version', labels)
                self.assertIn(f'{machine_name} {tool} last check', labels)
        self.assertEqual(machine.options[0]['timeout'], 10)
        self.assertIn('BatchMode=yes', machine.calls[0])
        self.assertIn('ConnectTimeout=5', machine.calls[0])
        _, output = self.main(['status', '--automation'], self.host(FakeMachine(), is_pi=False))
        self.assertEqual(json.loads(output)['level'], 'unknown')


class ContractTest(unittest.TestCase):
    def test_default_permission_paths(self):
        for platform, expected in (('linux', '/home/pi/scripts/vanpi-metered'),
                                   ('darwin', '/Users/jacobr/dev/scripts/macbook/metered/mac-metered')):
            with patch.object(codex_update.sys, 'platform', platform):
                self.assertEqual(str(codex_update.default_host().metered_cli), expected)

    def test_existing_cron_entries_are_unchanged(self):
        self.assertIn('47 */6 * * * /usr/bin/python3 -I -B ' + codex_update.PI_SCRIPT + ' run',
                      (REPO / 'pi/crontab_pi').read_text())
        self.assertIn('41 */6 * * * /usr/bin/python3 -I -B $scripts/shared/sh/codex_update.py run',
                      (REPO / 'macbook/crontab').read_text())

    def test_ytdlp_cron_denial_blocks_update_and_allow_preserves_arguments(self):
        line, = [line for line in (REPO / 'pi/crontab_pi').read_text().splitlines()
                 if not line.startswith('#') and 'update-ytdlp' in line]
        command = line.split(maxsplit=5)[5]
        expected = ('/home/pi/scripts/vanpi-metered allows pi.ytdlp_update && '
                    '/bin/bash /home/pi/youtube-cast/current/controller.sh update-ytdlp '
                    '2>&1 | /usr/bin/logger -t youtube-cast')
        self.assertEqual(command, expected)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / 'calls'
            controller = root / 'controller.sh'
            controller.write_text(f'printf "%s\\n" "$@" > "{log}"\n')
            gate = root / 'metered'
            command = command.replace('/home/pi/scripts/vanpi-metered', str(gate))
            command = command.replace('/home/pi/youtube-cast/current/controller.sh', str(controller))
            command = command.replace('/usr/bin/logger -t youtube-cast', '/bin/cat')
            for code in (1, 0):
                gate.write_text(f'#!/bin/sh\nexit {code}\n')
                gate.chmod(0o755)
                result = subprocess.run(['/bin/bash', '-c', command], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, code)
                self.assertEqual(log.exists(), code == 0)
            self.assertEqual(log.read_text(), 'update-ytdlp\n')

    def test_panel_declaration_loads(self):
        declaration = tool_automation.load_declaration('codex-update')
        self.assertIn(str(REPO / 'shared/sh/codex_update.py'), declaration.status.argv)
        self.assertIn('Claude', declaration.title)
        self.assertIn('Codex', declaration.title)


if __name__ == '__main__':
    unittest.main()
