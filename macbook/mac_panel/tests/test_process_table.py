"""ps parsing, the argv[0] boundary from its own pass, and names that never carry arguments."""
import unittest

from cli_runner import CommandResult
from panel_contracts import ApiError
from unittest import mock

import process_table
from process_table import (ARGV0_COMMAND, KERNEL_NAME_COMMAND, PS_ENV, TABLE_COMMAND,
                           ProcessReader, display_name, normal_started, parse_table, program_name)
from resources_fixtures import STARTED, ArgvRunner, make_table, make_texts, name_line, ps_line


def row_for(command, **overrides):
    return make_table((10, 1, command, overrides)).rows[10]


def reader_for(*specs, answers=None, files=None):
    """A reader over fake ps output; `files` answers the kernel executable-name lookup."""
    texts = make_texts(*specs)
    runner = ArgvRunner({argv: text + '\n' for argv, text in
                         zip((TABLE_COMMAND, ARGV0_COMMAND, KERNEL_NAME_COMMAND), texts)})
    runner.answers.update(answers or {})
    files = files or {}
    looked_up = []

    def lookup(pid):
        looked_up.append(pid)
        return files.get(pid)
    reader = ProcessReader(runner, executable_name=lookup)
    reader.looked_up = looked_up
    return reader, runner


class PsParsingTests(unittest.TestCase):
    def test_fields_lstart_tty_and_command_remainder(self):
        row = make_table((4242, 1, 'claude --permission-mode bypassPermissions',
                          {'nice': 5, 'state': 'T+', 'cpu': 12.5, 'rss_kb': 2048, 'pgid': 4000,
                           'tty': 'ttys004'})).rows[4242]
        self.assertEqual((row.pid, row.ppid, row.pgid, row.uid, row.nice), (4242, 1, 4000, 501, 5))
        self.assertTrue(row.stopped)
        self.assertEqual((row.cpu, row.rss_bytes, row.started, row.tty), (12.5, 2048 * 1024, STARTED, 'ttys004'))
        self.assertEqual(row.argv_text, 'claude --permission-mode bypassPermissions')
        self.assertEqual((row.executable, row.arguments), ('claude', ('--permission-mode', 'bypassPermissions')))
        self.assertIsNone(row_for('sleep 5').tty)

    def test_awkward_commands_keep_their_text_and_never_shift_fields(self):
        commands = ['/bin/echo  two  spaces', 'node ünïcødé → 名前.js', '/usr/bin/tool 1 2 3 4 5 6 7 8',
                    'sh -c "Tue Nov 17 22:19:53 2026 501 0 S 99.0"', '(zombie)']
        table = make_table(*[(100 + i, 1, command) for i, command in enumerate(commands)])
        self.assertEqual(table.malformed, 0)
        self.assertEqual([table.rows[100 + i].argv_text for i in range(len(commands))], commands)
        self.assertTrue(all(row.cpu == 0.0 and row.uid == 501 for row in table.rows.values()))

    def test_argv0_from_its_own_pass_bounds_a_path_with_spaces(self):
        app = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
        row = row_for(f'{app} --type=renderer --secret value', argv0=app)
        self.assertEqual((row.exe, row.arguments_text), ('Google Chrome', ' --type=renderer --secret value'))
        self.assertEqual(display_name(row), 'Google Chrome')

    def test_rows_that_do_not_join_on_pid_and_start_are_malformed(self):
        main, argv0s, kernel = make_texts((5, 1, 'ok'), (6, 1, 'sleep 9'), (7, 1, 'other'))
        # pid 6's argv[0] row is from a different process (another start); pid 7 has none.
        argv0s = '\n'.join([name_line(5, 'ok'), name_line(6, 'sleep', 'Wed Nov 18 04:11:36 2026')])
        table = parse_table(main, argv0s, kernel)
        self.assertEqual((sorted(table.rows), table.malformed), ([5], 2))
        # A command that does not begin with its argv[0] is a reused pid between passes.
        main, argv0s, kernel = make_texts((8, 1, 'vim notes', {'argv0': 'emacs'}))
        self.assertEqual(parse_table(main, argv0s, kernel).malformed, 1)

    def test_malformed_rows_are_dropped_and_counted(self):
        main, argv0s, kernel = make_texts((5, 1, 'ok'), (11, 1, 'later'))
        lines = main.splitlines()
        text = '\n'.join([lines[0], 'garbage', '  7 1 7 501 0 S 0.0 12 ?? Tue Nov 17 22:19:53',
                          ps_line(8, 1, 'x').replace('Tue', 'Xyz'), ps_line(9, 1, 'y').replace(' 0.0 ', ' -1 '),
                          lines[0], '', lines[1]])
        table = parse_table(text, argv0s, kernel)
        self.assertEqual(sorted(table.rows), [5, 11])
        # A duplicate pid is malformed too: the first row wins.
        self.assertEqual(table.malformed, 5)

    def test_tree_helpers(self):
        table = make_table((1, 0, '/sbin/launchd'), (10, 1, 'app'), (11, 10, 'child'),
                           (12, 11, 'grandchild'), (13, 10, 'sibling'), (20, 1, 'other'))
        self.assertEqual([(row.pid, depth) for row, depth in table.descendants(10)],
                         [(11, 1), (13, 1), (12, 2)])
        self.assertEqual(table.ancestors(12), [11, 10, 1])
        self.assertEqual(table.top_ancestor(12), 10)
        self.assertEqual(table.top_ancestor(20), 20)
        self.assertEqual(table.complete_chain(12), [11, 10, 1])
        self.assertTrue(table.chain_reaches(12, 10))
        self.assertFalse(table.chain_reaches(20, 10))

    def test_a_missing_parent_breaks_the_chain_but_not_the_display_ancestor(self):
        table = make_table((1, 0, '/sbin/launchd'), (10, 1, 'app'), (12, 11, 'orphaned view'))
        # 11 exited (or its row was unreadable): the highest ancestor actually present is 12.
        self.assertEqual(table.top_ancestor(12), 12)
        self.assertIsNone(table.complete_chain(12))
        self.assertFalse(table.chain_reaches(12, 10))
        self.assertIsNone(make_table((10, 1, 'no launchd row')).complete_chain(10))

    def test_reader_uses_fixed_argv_and_environment(self):
        reader, runner = reader_for((5, 1, 'ok'))
        self.assertEqual(list(reader.table().rows), [5])
        self.assertEqual(runner.calls, [TABLE_COMMAND, ARGV0_COMMAND, KERNEL_NAME_COMMAND])
        self.assertEqual(PS_ENV, {'LC_ALL': 'C', 'TZ': 'UTC'})

    def assertFails(self, reader):
        with self.assertRaises(ApiError) as caught:
            reader.table()
        self.assertEqual(caught.exception.status, 502)

    def test_a_failed_or_timed_out_pass_is_502(self):
        self.assertFails(reader_for((5, 1, 'ok'), answers={TABLE_COMMAND: CommandResult(1, '', 'ps: failed')})[0])
        # The runner reports a timeout as 504; the section's declared failure is 502.
        self.assertFails(reader_for((5, 1, 'ok'), answers={ARGV0_COMMAND: ApiError(504, 'timed out')})[0])

    def test_a_table_with_no_usable_rows_or_mostly_malformed_is_502(self):
        self.assertFails(reader_for((5, 1, 'ok'), answers={TABLE_COMMAND: 'garbage\nmore garbage\n'})[0])
        good = [(100 + n, 1, f'tool{n}') for n in range(100)]
        main, argv0s, kernel = make_texts(*good)
        broken = '\n'.join(argv0s.splitlines()[:80])
        reader = reader_for(*good, answers={ARGV0_COMMAND: broken + '\n'})[0]
        self.assertFails(reader)
        tolerable = '\n'.join(argv0s.splitlines()[:95])
        self.assertEqual(len(reader_for(*good, answers={ARGV0_COMMAND: tolerable})[0].table().rows), 95)

    def test_start_times_normalise_day_padding(self):
        self.assertEqual(normal_started('Mon Nov  2 01:00:00 2026'), 'Mon Nov 2 01:00:00 2026')
        for bad in (None, True, 1, '', 'yesterday', 'Mon Nov 2 01:00:00'):
            self.assertIsNone(normal_started(bad), bad)


class DisplayNameTests(unittest.TestCase):
    def assertName(self, command, expected, **overrides):
        row = row_for(command, **overrides)
        self.assertEqual(display_name(row), expected, command)
        self.assertEqual(row.name, expected)

    def test_programs_show_only_their_base_name(self):
        self.assertName('claude -p "secret prompt about payroll"', 'claude')
        self.assertName('/usr/local/bin/mysql --password hunter2 --user root', 'mysql')
        self.assertName('grep needle /private/haystack positional text', 'grep')
        self.assertName('-zsh', 'zsh')

    def test_a_title_in_argv0_gives_way_to_the_kernel_name(self):
        # Programs that rewrite argv[0] can put arguments in it.
        self.assertName('npm exec kapture-mcp@latest bridge', 'node',
                        argv0='npm exec kapture-mcp@latest bridge', ucomm='node')
        self.assertName('tmux: server', 'tmux', argv0='tmux: server', ucomm='tmux')
        self.assertName('ssh: /Users/me/.ssh/sock [mux]', 'ssh', argv0='ssh: /Users/me/.ssh/sock [mux]', ucomm='ssh')

    def test_interpreters_show_a_confident_script_or_module_only(self):
        self.assertName('/usr/bin/python3 -P -m van_compute.worker --serve --token abc', 'python3 -m van_compute.worker')
        self.assertName('/opt/x/Python -B /Users/me/dev/server.py --password X', 'Python server.py')
        self.assertName('node /Users/me/tools/script.js secret-argument', 'node script.js')
        self.assertName('python3 -Bm package.cli run', 'python3 -m package.cli')
        self.assertName('python3 -OO -I tool.py', 'python3 tool.py')
        self.assertName('/bin/sh /Users/me/Library/Caches/van-compute/jobs/j1/run.sh', 'sh run.sh')

    def test_anything_not_parsed_with_confidence_shows_the_bare_executable(self):
        cases = {
            'node --password hunter2 app.js': 'node',
            'node --require /tmp/hook.js app.mjs --key value': 'node',
            'node --redirect-warnings=/Users/me/secret-notes.js app.js': 'node',
            'node --redirect-warnings /Users/me/secret-notes.js app.js': 'node',
            'bash --rcfile /Users/me/private-rc.sh': 'bash',
            'perl -esleep': 'perl',
            'perl -e "print $ENV{SECRET}"': 'perl',
            'ruby -e "puts ENV[\'SECRET\']"': 'ruby',
            'python3 -c "import os; print(os.environ[\'TOKEN\'])"': 'python3',
            '/bin/bash -lc "curl https://x?token=abc"': 'bash',
            '/bin/zsh -c source ~/.claude/shell-snapshots/snap.sh': 'zsh',
            'python3 -W ignore secret.py': 'python3',
            'python3 -mvenv /tmp/x': 'python3',
            'python3 my secret positional text': 'python3',
            'python3 -m "os; import x"': 'python3',
            # One argument with a space: "/Users/me/tax notes/run.py" arrives cut at the space.
            'python3 /Users/me/tax notes/run.py': 'python3',
            'node /tmp/a$b.js': 'node',
            'perl': 'perl',
        }
        for command, expected in cases.items():
            self.assertName(command, expected)

    def test_a_title_with_spaces_is_never_shown(self):
        # A rewritten title that starts with a path still carries arguments after it.
        self.assertName('/usr/bin/worker --note plain-example', 'node',
                        argv0='/usr/bin/worker --note plain-example', ucomm='node')
        self.assertName('node --secret value', 'node', argv0='node --secret value', ucomm='node')
        self.assertName('Google Chrome Helper --token X', 'Google Chrome He',
                        argv0='Google Chrome Helper --token X', ucomm='Google Chrome He')

    def test_finding2_a_full_length_kernel_name_does_not_authenticate_the_title(self):
        # The reviewer's reproduction: positional text after a matching kernel prefix.
        self.assertEqual(program_name('Google Chrome Helper private draft', 'Google Chrome He'),
                         'Google Chrome He')
        self.assertName('Google Chrome Helper private draft', 'Google Chrome He',
                        argv0='Google Chrome Helper private draft', ucomm='Google Chrome He')

    def test_finding2_the_kernel_supplies_the_real_file_name_for_a_titled_process(self):
        helper = '/Applications/Chrome.app/Contents/MacOS/Google Chrome Helper (Renderer)'
        reader, _ = reader_for(
            (10, 1, f'{helper} --type=renderer', {'argv0': helper}),
            (11, 1, 'Google Chrome Helper private draft',
             {'argv0': 'Google Chrome Helper private draft', 'ucomm': 'Google Chrome He'}),
            (12, 1, 'tmux: server', {'argv0': 'tmux: server', 'ucomm': 'tmux'}),
            # A lookup that reached a reused pid names another program: not believed.
            (13, 1, 'ssh: /Users/me/.ssh/sock [mux]', {'argv0': 'ssh: /Users/me/.ssh/sock [mux]', 'ucomm': 'ssh'}),
            (14, 1, '/usr/bin/plain --flag'),
            # The 16-character cut ends in a space, which the kernel-name pass strips.
            (15, 1, 'Keychain Circle Notification', {'argv0': 'Keychain Circle Notification', 'ucomm': 'Keychain Circle '}),
            files={10: 'Google Chrome Helper (Renderer)', 11: 'Google Chrome Helper (GPU)', 13: 'other',
                   15: 'Keychain Circle Notification'})
        table = reader.table()
        names = {pid: row.name for pid, row in table.rows.items()}
        self.assertEqual(names, {10: 'Google Chrome Helper (Renderer)', 11: 'Google Chrome Helper (GPU)',
                                 12: 'tmux', 13: 'ssh', 14: 'plain', 15: 'Keychain Circle Notification'})
        # Only rows whose argv[0] has whitespace are looked up.
        self.assertEqual(sorted(reader.looked_up), [10, 11, 12, 13, 15])
        for row in table.rows.values():
            self.assertNotIn('private', row.name)
            self.assertNotIn('/', row.name)

    def test_executable_lookups_are_capped_per_table(self):
        specs = [(20 + i, 1, f'title {i} words', {'argv0': f'title {i} words', 'ucomm': 'worker'})
                 for i in range(5)]
        reader, _ = reader_for(*specs, files={20 + i: 'worker' for i in range(5)})
        with mock.patch.object(process_table, 'EXECUTABLE_LOOKUP_LIMIT', 3):
            table = reader.table()
        self.assertEqual(len(reader.looked_up), 3)
        self.assertEqual({row.name for row in table.rows.values()}, {'worker'})

    def test_each_shell_parses_only_its_own_flags(self):
        # fish `-p` writes a profile to the path after it; fish has no table, so no detail.
        self.assertName('fish -p /Users/me/profile-notes.sh', 'fish')
        self.assertName('ksh /Users/me/run.sh', 'ksh')
        self.assertName('zsh -l /Users/me/run.zsh', 'zsh run.zsh')
        self.assertName('bash -p /Users/me/privileged.sh', 'bash')
        self.assertName('/bin/bash -c sh /x/Library/Caches/van-compute/jobs/secret/run.sh', 'bash')

    def test_argument_text_is_kept_out_of_repr(self):
        row = row_for('claude -p "secret prompt"')
        self.assertNotIn('secret', repr(row))


if __name__ == '__main__':
    unittest.main()
