"""ps parsing, the executable boundary and display names that never carry arguments."""
import unittest

from cli_runner import CommandResult
from panel_contracts import ApiError
from process_table import (PS_ENV, TABLE_COMMAND, ProcessReader, display_name, parse_row,
                           parse_table, resolve_executable)
from resources_fixtures import STARTED, ArgvRunner, make_table, ps_line


def row_for(command, is_file=lambda _path: False):
    return parse_row(ps_line(10, 1, command), is_file)


class PsParsingTests(unittest.TestCase):
    def test_fields_lstart_and_command_remainder(self):
        line = ps_line(4242, 1, 'claude --permission-mode bypassPermissions', nice=5, state='T+',
                       cpu=12.5, rss_kb=2048, pgid=4000)
        row = parse_row(line)
        self.assertEqual((row.pid, row.ppid, row.pgid, row.uid, row.nice), (4242, 1, 4000, 501, 5))
        self.assertTrue(row.stopped)
        self.assertEqual(row.cpu, 12.5)
        self.assertEqual(row.rss_bytes, 2048 * 1024)
        self.assertEqual(row.started, STARTED)
        self.assertEqual(row.argv_text, 'claude --permission-mode bypassPermissions')

    def test_awkward_commands_keep_their_text_and_never_shift_fields(self):
        commands = ['/bin/echo  two  spaces', 'node ünïcødé → 名前.js', '/usr/bin/tool 1 2 3 4 5 6 7 8',
                    'sh -c "Tue Nov 17 22:19:53 2026 501 0 S 99.0"', '(zombie)']
        table = make_table(*[(100 + i, 1, command) for i, command in enumerate(commands)])
        self.assertEqual(table.malformed, 0)
        self.assertEqual([table.rows[100 + i].argv_text for i in range(len(commands))], commands)
        self.assertTrue(all(row.cpu == 0.0 and row.uid == 501 for row in table.rows.values()))

    def test_malformed_rows_are_dropped_and_counted(self):
        good = ps_line(5, 1, 'ok')
        text = '\n'.join([good, 'garbage', '  7 1 7 501 0 S 0.0 12 Tue Nov 17 22:19:53',
                          ps_line(8, 1, 'x').replace('Tue', 'Xyz'), ps_line(9, 1, 'y').replace(' 0.0 ', ' -1 '),
                          good, '', ps_line(11, 1, 'later')])
        table = parse_table(text)
        self.assertEqual(sorted(table.rows), [5, 11])
        # A duplicate pid is malformed too: the first row wins.
        self.assertEqual(table.malformed, 5)

    def test_paths_with_spaces_resolve_to_the_shortest_existing_file(self):
        app = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
        argv = f'{app} --type=renderer --secret value'
        self.assertEqual(resolve_executable(argv, lambda path: path == app), app)
        self.assertEqual(resolve_executable(argv, lambda _path: False), '/Applications/Google')
        self.assertEqual(resolve_executable('/bin/zsh -c x', lambda path: path == '/bin/zsh'), '/bin/zsh')
        row = row_for(argv, lambda path: path == app)
        self.assertEqual(display_name(row), 'Google Chrome')

    def test_tree_helpers(self):
        table = make_table((1, 0, '/sbin/launchd'), (10, 1, 'app'), (11, 10, 'child'),
                           (12, 11, 'grandchild'), (13, 10, 'sibling'), (20, 1, 'other'))
        self.assertEqual([(row.pid, depth) for row, depth in table.descendants(10)],
                         [(11, 1), (13, 1), (12, 2)])
        self.assertEqual(table.ancestors(12), [11, 10, 1])
        self.assertEqual(table.top_ancestor(12), 10)
        self.assertEqual(table.top_ancestor(20), 20)

    def test_reader_uses_fixed_argv_and_environment(self):
        runner = ArgvRunner({TABLE_COMMAND: ps_line(5, 1, 'ok') + '\n'})
        table = ProcessReader(runner, is_file=lambda _path: False).table()
        self.assertEqual(list(table.rows), [5])
        runner = ArgvRunner({TABLE_COMMAND: CommandResult(1, '', 'ps: failed')})
        with self.assertRaises(ApiError) as caught:
            ProcessReader(runner).table()
        self.assertEqual(caught.exception.status, 502)
        self.assertEqual(PS_ENV, {'LC_ALL': 'C', 'TZ': 'UTC'})

    def test_single_row_read_returns_none_for_a_gone_pid(self):
        argv = ('/bin/ps', '-ww', '-o', TABLE_COMMAND[-1], '-p', '77')
        reader = ProcessReader(ArgvRunner({argv: CommandResult(1, '')}))
        self.assertIsNone(reader.row(77))
        reader = ProcessReader(ArgvRunner({argv: ps_line(77, 1, 'sleep 600')}))
        self.assertEqual(reader.row(77).exe, 'sleep')


class DisplayNameTests(unittest.TestCase):
    def assertName(self, command, expected):
        row = row_for(command)
        self.assertEqual(display_name(row), expected, command)
        self.assertEqual(row.name, expected)

    def test_programs_show_only_their_base_name(self):
        self.assertName('claude -p "secret prompt about payroll"', 'claude')
        self.assertName('/usr/local/bin/mysql --password hunter2 --user root', 'mysql')
        self.assertName('grep needle /private/haystack positional text', 'grep')
        self.assertName('-zsh', 'zsh')
        self.assertName('npm exec kapture-mcp@latest bridge', 'npm')

    def test_interpreters_show_their_script_or_module_only(self):
        self.assertName('/usr/bin/python3 -P -m van_compute.worker --serve --token abc', 'python3 -m van_compute.worker')
        self.assertName('/opt/x/Python -B /Users/me/dev/server.py --password X', 'Python server.py')
        self.assertName('node /Users/me/tools/script.js secret-argument', 'node script.js')
        self.assertName('node --require /tmp/hook.js app.mjs --key value', 'node app.mjs')
        self.assertName('python3 -c "import os; print(os.environ[\'TOKEN\'])"', 'python3 -c')
        self.assertName('/bin/bash -lc "curl https://x?token=abc"', 'bash -lc')
        self.assertName('/bin/zsh -c source ~/.claude/shell-snapshots/snap.sh', 'zsh -c')
        self.assertName('python3 -Bm package.cli run', 'python3 -m package.cli')

    def test_interpreter_operands_that_are_not_scripts_are_hidden(self):
        # An unknown option's value lands in the script position; it is not a path, so it stays hidden.
        self.assertName('node --password hunter2 app.js', 'node')
        self.assertName('python3 my secret positional text', 'python3')
        self.assertName('python3 -m "os; import x"', 'python3 -m')
        self.assertName('ruby -e "puts ENV[\'SECRET\']"', 'ruby -e')
        self.assertName('perl', 'perl')

    def test_argument_text_is_kept_out_of_repr(self):
        row = row_for('claude -p "secret prompt"')
        self.assertNotIn('secret', repr(row))


if __name__ == '__main__':
    unittest.main()
