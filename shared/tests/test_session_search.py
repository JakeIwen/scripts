"""Exercise session search output through both shell entry points."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'sh'
# Any of these points the search away from the sessions the index covers.
GREP_ONLY_ENVIRONMENT = ('CLAUDESEARCH_ROOT', 'CODEXSEARCH_ROOT', 'CLAUDE_CONFIG_DIR',
                         'SESSION_SEARCH_RECORDS')
NOT_BUILT_NOTE = 'llmsearch: the transcript index is not built yet, so searching with grep (slow)\n'


class SessionFixtureTestCase(unittest.TestCase):
    """Claude and Codex session stores under a temporary root."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        for tool in ('claude', 'codex'):
            self.write_session(tool, 'first', ['old needle', 'new needle needle', 'unrelated'])
            self.write_session(tool, 'second', ['another needle'])

    def write_session(self, tool, session_id, messages, cwd=None, timestamps=()):
        store = self.root / tool
        cwd = str(self.home / 'dev/my project') if cwd is None else cwd
        if tool == 'claude':
            folder = store / 'projects/project'
            events = [{'type': 'custom-title', 'customTitle': session_id}]
            events += [{'type': 'user', 'cwd': cwd, 'message': {'content': text}}
                       for text in messages]
        else:
            folder = store / 'sessions/2026/10/18'
            events = [{'type': 'session_meta', 'payload': {'id': session_id, 'cwd': cwd}}]
            events += [{'type': 'event_msg', 'payload': {'message': text}}
                       for text in messages]
        folder.mkdir(parents=True, exist_ok=True)
        for event, timestamp in zip(events[1:], timestamps):
            event['timestamp'] = timestamp
        path = folder / (session_id + '.jsonl')
        path.write_text(''.join(json.dumps(event) + '\n' for event in events))
        if tool == 'codex':
            with (store / 'session_index.jsonl').open('a') as index:
                index.write(json.dumps({'id': session_id, 'thread_name': session_id}) + '\n')
        modified = time.time() - 3 * 3600 - 30
        os.utime(path, (modified, modified))
        return path


class SessionSearchTests(SessionFixtureTestCase):
    def search(self, tool, *args, **overrides):
        environment = dict(os.environ, HOME=str(self.home))
        for provider in ('claude', 'codex'):
            environment[provider.upper() + 'SEARCH_ROOT'] = str(self.root / provider)
        environment.update(overrides)
        # The Pi can spend over ten seconds starting jq processes under load.
        return subprocess.run([str(SCRIPTS / (tool + 'search.sh')), *args],
                              env=environment, capture_output=True, text=True, timeout=60)

    def test_title_search_matches_saved_titles_without_body_hits(self):
        for tool in ('claude', 'codex'):
            self.write_session(tool, 'title Needle', ['unrelated body'])
        for tool in ('claude', 'codex', 'llm'):
            for flag in ('-t', '--title'):
                with self.subTest(tool=tool, flag=flag):
                    result = self.search(tool, flag, 'NEEDLE')
                    self.assertEqual(result.returncode, 0, result.stderr)
                    blocks = result.stdout.strip().split('\n\n')
                    self.assertEqual(len(blocks), 2 if tool == 'llm' else 1)
                    self.assertTrue(all(block.endswith('title Needle') for block in blocks))
                    self.assertNotIn('unrelated body', result.stdout)
                    self.assertNotIn('pr first', result.stdout)
                    self.assertEqual(self.search(tool, flag, 'absent').stdout, '')
                    self.assertEqual(self.search(tool, flag).returncode, 2)

    def test_title_search_uses_current_title_and_excludes_directory_fallback(self):
        claude = self.write_session('claude', 'stale', ['body needle'])
        with claude.open('a') as stream:
            stream.write(json.dumps({'type': 'custom-title', 'customTitle': 'Current'}) + '\n')
            stream.write(json.dumps({'type': 'ai-title', 'aiTitle': 'Ignored'}) + '\n')
        self.write_session('codex', 'stale', ['body needle'])
        with (self.root / 'codex/session_index.jsonl').open('a') as stream:
            stream.write(json.dumps({'id': 'stale', 'thread_name': 'Current'}) + '\n')
        for tool in ('claude', 'codex'):
            self.write_session(tool, 'untitled', ['body needle'], cwd='/tmp/fallback')
        untitled = self.root / 'claude/projects/project/untitled.jsonl'
        untitled.write_text(untitled.read_text().split('\n', 1)[1])
        index = self.root / 'codex/session_index.jsonl'
        index.write_text(''.join(line + '\n' for line in index.read_text().splitlines()
                                 if json.loads(line)['id'] != 'untitled'))
        for tool in ('claude', 'codex', 'llm'):
            with self.subTest(tool=tool):
                for query in ('stale', 'Ignored', 'fallback'):
                    self.assertEqual(self.search(tool, '-t', query).stdout, '')
                result = self.search(tool, '-t', 'current')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Current', result.stdout)

    def test_title_search_grouping_records_and_literal_flags(self):
        for tool in ('claude', 'codex'):
            for session in ('title --title older', 'title --title newer'):
                path = self.write_session(tool, session, ['body'])
                hours = 1 if session.endswith('newer') else 2
                modified = time.time() - hours * 3600 - 30
                os.utime(path, (modified, modified))
        for tool in ('claude', 'codex', 'llm'):
            result = self.search(tool, '-g', '-t', '--', '--title')
            self.assertEqual(result.returncode, 0, result.stderr)
            blocks = result.stdout.strip().split('\n\n')
            self.assertEqual(len(blocks), 2)
            self.assertTrue(all(block.startswith('2 matches | 1 hour ago') if tool == 'llm'
                                else block.startswith('1 matches | ') for block in blocks))
            records = self.search(tool, '--title', '--', '--title', SESSION_SEARCH_RECORDS='1')
            self.assertEqual(records.returncode, 0, records.stderr)
            decoded = [json.loads(line) for line in records.stdout.splitlines()]
            self.assertEqual(len(decoded), 4 if tool == 'llm' else 2)
            self.assertTrue(all(record['count'] == 1 for record in decoded))
            self.assertTrue(all(record['context'].startswith('title --title') for record in decoded))
            self.write_session('claude' if tool == 'llm' else tool,
                               'literal-title-flag', ['body --title'])
            literal = self.search(tool, '--', '--title')
            self.assertEqual(literal.returncode, 0, literal.stderr)
            self.assertIn('body --title', literal.stdout)

    def test_group_counts_events_and_keeps_latest_matching_excerpt(self):
        for tool, command in [('claude', 'claudepr'), ('codex', 'codexpr')]:
            for args in [('-g', 'needle'), ('--group', 'needle'), ('needle', '-g')]:
                with self.subTest(tool=tool, args=args):
                    result = self.search(tool, *args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertCountEqual(result.stdout.strip().split('\n\n'), [
                        '2 matches | 3 hours ago | first\n'
                        f"cd ~/'dev/my project' && {command} first\nnew needle needle",
                        '1 matches | 3 hours ago | second\n'
                        f"cd ~/'dev/my project' && {command} second\nanother needle",
                    ])

    def test_default_keeps_every_matching_event(self):
        for tool in ('claude', 'codex'):
            with self.subTest(tool=tool):
                result = self.search(tool, 'needle')
                self.assertEqual(result.returncode, 0, result.stderr)
                blocks = result.stdout.strip().split('\n\n')
                self.assertEqual(len(blocks), 3)
                self.assertTrue(all(block.startswith('3 hours ago | ') for block in blocks))
                self.assertCountEqual([block.splitlines()[-1] for block in blocks],
                                      ['old needle', 'new needle needle', 'another needle'])

    def test_newest_activity_order_interleaves_tools_and_reverses_excerpts(self):
        expected = []
        for hours, (tool, session) in enumerate([
                ('codex', 'first'), ('claude', 'first'),
                ('codex', 'second'), ('claude', 'second')], 1):
            path = self.write_session(tool, session, ['older needle', 'newer needle'])
            modified = time.time() - hours * 3600 - 30
            os.utime(path, (modified, modified))
            expected.extend([(tool, session, text) for text in ('newer needle', 'older needle')])
        for tool in ('claude', 'codex', 'llm'):
            with self.subTest(tool=tool):
                result = self.search(tool, 'needle')
                self.assertEqual(result.returncode, 0, result.stderr)
                blocks = result.stdout.strip().split('\n\n')
                wanted = [item for item in expected if tool == 'llm' or item[0] == tool]
                self.assertEqual(len(blocks), len(wanted))
                for block, (provider, session, context) in zip(blocks, wanted):
                    self.assertIn(f'{provider}pr {session}\n', block)
                    self.assertEqual(block.splitlines()[-1], context)
        for tool in ('claude', 'codex'):
            result = self.search(tool, '-g', 'needle')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([block.splitlines()[0].split(' | ')[-1]
                              for block in result.stdout.strip().split('\n\n')],
                             ['first', 'second'])

    def test_records_keep_latest_match_and_counts_without_display_controls(self):
        for tool in ('claude', 'codex'):
            self.write_session(tool, 'first', ['older needle', 'newer needle'],
                               timestamps=['2026-10-18T10:00:00Z', '2026-10-18T12:00:00Z'])
        result = self.search('llm', 'needle', SESSION_SEARCH_RECORDS='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(records), 4)
        for record in records:
            if record['resume'].endswith(' first'):
                self.assertEqual(record['count'], 2)
                self.assertEqual(record['context'], 'newer needle')
                self.assertEqual(record['matched_at'], 1792324800)

    def test_grouped_llm_directories_are_newest_first(self):
        recent = self.write_session('codex', 'recent', ['recent needle'], cwd='/tmp/recent')
        modified = time.time() - 3600 - 30
        os.utime(recent, (modified, modified))
        result = self.search('llm', '-g', 'needle')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith('1 matches | 1 hour ago | recent\n'))
        self.assertNotIn('\x1b', result.stdout)

    def test_inherited_file_override_does_not_replace_session_enumeration(self):
        for tool, count in [('claude', 3), ('codex', 3), ('llm', 6)]:
            with self.subTest(tool=tool):
                result = self.search(tool, 'needle', SESSION_SEARCH_FILE='/missing/transcript')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(result.stdout.strip().split('\n\n')), count)

    def test_empty_results_missing_query_and_literal_flag(self):
        for tool in ('claude', 'codex'):
            with self.subTest(tool=tool):
                self.assertEqual(self.search(tool, '-g', 'absent').stdout, '')
                for args in [(), ('-g',), ('--group', ''), ('--',)]:
                    self.assertEqual(self.search(tool, *args).returncode, 2)
                self.write_session(tool, 'literal', ['literal --group option'])
                result = self.search(tool, '-g', '--', '--group')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout.startswith('1 matches | '))
                self.assertTrue(result.stdout.endswith('literal --group option\n\n'))

    def test_help_lists_flags_without_requiring_session_storage(self):
        for tool in ('claude', 'codex'):
            (self.root / tool).rename(self.root / (tool + '-unused'))
            for args in [('-h',), ('--help',), ('-g', 'needle', '--help')]:
                with self.subTest(tool=tool, args=args):
                    result = self.search(tool, *args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, '')
                    self.assertIn(f'Usage: {tool}search [flags] [--] <term>', result.stdout)
                    self.assertIn('-g, --group', result.stdout)
                    self.assertIn('-h, --help', result.stdout)
                    self.assertIn('-t, --title', result.stdout)
                    self.assertIn('Treat all remaining arguments as search text.', result.stdout)

    def test_help_flags_can_be_searched_literally(self):
        for tool in ('claude', 'codex'):
            self.write_session(tool, 'help-text', ['literal --help and -h option'])
            for flag in ('-h', '--help'):
                with self.subTest(tool=tool, flag=flag):
                    result = self.search(tool, '--', flag)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(result.stdout.startswith('3 hours ago | help-text\n'))
                    self.assertTrue(result.stdout.endswith('literal --help and -h option\n\n'))

    def test_llm_combines_all_hits_without_grouping(self):
        result = self.search('llm', 'needle')
        self.assertEqual(result.returncode, 0, result.stderr)
        blocks = result.stdout.strip().split('\n\n')
        self.assertEqual(len(blocks), 6)
        self.assertEqual(sum('&& claudepr ' in block for block in blocks), 3)
        self.assertEqual(sum('&& codexpr ' in block for block in blocks), 3)

    def test_llm_groups_by_directory_and_provider_using_latest_hit(self):
        for tool in ('claude', 'codex'):
            self.write_session(tool, 'first', ['old needle', 'latest needle', 'unrelated'],
                               timestamps=['2026-10-18T10:00:00Z',
                                           '2026-10-18T12:00:00.123Z',
                                           '2026-10-18T13:00:00Z'])
            # A newer file mtime must not beat a more recent matching event.
            self.write_session(tool, 'second', ['other needle'],
                               timestamps=['2026-10-18T11:00:00Z'])
        self.write_session('claude', 'elsewhere', ['separate needle'], cwd='/tmp/elsewhere')
        for flag in ('-g', '--group'):
            with self.subTest(flag=flag):
                result = self.search('llm', flag, 'needle')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertCountEqual(result.stdout.strip().split('\n\n'), [
                    '3 matches | 3 hours ago | first\n'
                    "cd ~/'dev/my project' && claudepr first\nlatest needle",
                    '3 matches | 3 hours ago | first\n'
                    "cd ~/'dev/my project' && codexpr first\nlatest needle",
                    '1 matches | 3 hours ago | elsewhere\n'
                    'cd /tmp/elsewhere && claudepr elsewhere\nseparate needle',
                ])

    def test_llm_group_falls_back_to_mtime_and_preserves_unknown_directories(self):
        for tool in ('claude', 'codex'):
            newer = self.write_session(tool, 'second', ['newer needle'])
            timestamp = time.time() - 2 * 3600 - 30
            os.utime(newer, (timestamp, timestamp))
        self.write_session('claude', 'unknown-one', ['unknown needle'], cwd='')
        self.write_session('claude', 'unknown-two', ['unknown needle'], cwd='')
        result = self.search('llm', '-g', 'needle')
        self.assertEqual(result.returncode, 0, result.stderr)
        blocks = result.stdout.strip().split('\n\n')
        self.assertEqual(len(blocks), 4)
        self.assertEqual(sum(block.startswith('3 matches | 2 hours ago | second')
                             for block in blocks), 2)
        self.assertEqual(sum(block.endswith('newer needle') for block in blocks), 2)

    def test_llm_flags_and_one_missing_provider(self):
        (self.root / 'codex').rename(self.root / 'codex-unused')
        for flag in ('-h', '--help'):
            result = self.search('llm', flag)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Usage: llmsearch [flags] [--] <query>', result.stdout)
            self.assertIn('Group by directory and tool', result.stdout)
        result = self.search('llm', '-g', 'needle')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.strip().split('\n\n')), 1)
        self.assertTrue(result.stdout.startswith('3 matches | '))
        self.assertEqual(self.search('llm', '-g').returncode, 2)
        self.assertEqual(self.search('llm', '-g', 'absent').stdout, '')
        self.write_session('claude', 'literal', ['literal --help option'])
        self.assertTrue(self.search('llm', '-g', '--', '--help').stdout.endswith(
            'literal --help option\n\n'))
        (self.root / 'claude').rename(self.root / 'claude-unused')
        self.assertEqual(self.search('llm', 'needle').returncode, 1)
        self.assertEqual(self.search('llm', '--help').returncode, 0)


class IndexSearchTests(SessionFixtureTestCase):
    """llmsearch asks LLM Switchboard's index first, through a stand-in on PATH."""

    def fake_switchboard(self, results=(), code=0, message=''):
        folder = self.root / 'bin'
        folder.mkdir(exist_ok=True)
        answer = folder / 'answer.jsonl'
        answer.write_text(''.join(json.dumps(result) + '\n' for result in results))
        calls = shlex.quote(str(folder / 'calls'))
        script = folder / 'llm-switchboard'
        script.write_text('#!/bin/sh\n'
                          f'printf "[%s]" "$@" >> {calls}\n'
                          f'echo >> {calls}\n'
                          f'cat {shlex.quote(str(answer))}\n'
                          + (f'echo {shlex.quote(message)} >&2\n' if message else '')
                          + f'exit {code}\n')
        script.chmod(0o755)

    def switchboard_calls(self):
        calls = self.root / 'bin/calls'
        return calls.read_text().splitlines() if calls.exists() else []

    def llmsearch(self, *args, **environment):
        """Run llmsearch as Jacob does, on the default session folders."""
        for tool in ('claude', 'codex'):
            link = self.home / ('.' + tool)
            if not link.is_symlink():
                link.symlink_to(self.root / tool)
        # Leave out any real llm-switchboard, so only the stand-in can answer.
        path = [str(self.root / 'bin')] + [
            folder for folder in os.environ['PATH'].split(os.pathsep)
            if folder and not (Path(folder) / 'llm-switchboard').exists()]
        variables = {name: value for name, value in os.environ.items()
                     if name not in GREP_ONLY_ENVIRONMENT}
        variables.update(HOME=str(self.home), PATH=os.pathsep.join(path), **environment)
        return subprocess.run([str(SCRIPTS / 'llmsearch.sh'), *args], env=variables,
                              capture_output=True, text=True, timeout=60)

    def index_result(self, tool, session_id, title, count, excerpt, hours_ago, cwd=None,
               matched_hours_ago=None):
        cwd = str(self.home / 'dev/my project') if cwd is None else cwd
        now = time.time()
        matched = hours_ago if matched_hours_ago is None else matched_hours_ago
        return {'tool': tool, 'id': session_id, 'short_id': session_id[:8], 'title': title,
                'cwd': cwd, 'project': cwd, 'mtime': now - hours_ago * 3600 - 30.5,
                'count': count, 'matched_at': now - matched * 3600 - 30.5,
                'excerpt': excerpt, 'resume_cmd': '', 'host': '', 'title_only': False}

    def assert_grep_results(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        blocks = result.stdout.strip().split('\n\n')
        self.assertEqual(len(blocks), 6)
        self.assertEqual(sum('&& claudepr ' in block for block in blocks), 3)
        self.assertEqual(sum('&& codexpr ' in block for block in blocks), 3)

    def test_index_prints_one_block_per_session_newest_first(self):
        ageless = self.index_result('claude', 'ageless', '(untitled)', 1, 'ageless needle', 0, cwd='')
        ageless.update(mtime=None, matched_at=None)
        self.fake_switchboard([
            self.index_result('codex', 'second', 'Second', 1, '…another needle', 1),
            self.index_result('claude', 'first', 'First\tline', 2, 'new needle needle…', 3),
            self.index_result('claude', 'untitled', '(untitled)', 1, 'loose needle', 26,
                        cwd='/tmp/elsewhere/'),
            ageless,
        ])
        result = self.llmsearch('needle')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertEqual(result.stdout,
                         '1 matches | 1 hour ago | Second\n'
                         "cd ~/'dev/my project' && codexpr second\n…another needle\n\n"
                         '2 matches | 3 hours ago | First line\n'
                         "cd ~/'dev/my project' && claudepr first\nnew needle needle…\n\n"
                         '1 matches | 1 day ago | elsewhere\n'
                         'cd /tmp/elsewhere/ && claudepr untitled\nloose needle\n\n'
                         '1 matches | unknown age | unknown\nclaudepr ageless\nageless needle\n\n')
        self.assertEqual(self.switchboard_calls(), ['[search][--json][--limit][200][--][needle]'])

    def test_index_query_keeps_its_syntax_and_quotes_text_after_double_dash(self):
        self.fake_switchboard()
        result = self.llmsearch('-g', 'pcan', '-usb', 'dir:obd', '--', 'say "hi"', '-g')
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        self.assertEqual(self.switchboard_calls(),
                         ['[search][--json][--limit][200][--][pcan -usb dir:obd "say  hi  -g"]'])

    def test_index_groups_by_directory_and_tool_using_latest_match(self):
        self.fake_switchboard([
            self.index_result('claude', 'first', 'First', 2, 'latest needle', 1),
            self.index_result('codex', 'other', 'Other', 1, 'other needle', 2),
            # A newer transcript must not beat a more recent match.
            self.index_result('claude', 'second', 'Second', 3, 'older needle', 0.5,
                        matched_hours_ago=4),
            self.index_result('claude', 'elsewhere', 'Elsewhere', 1, 'separate needle', 5,
                        cwd='/tmp/elsewhere'),
        ])
        for flag in ('-g', '--group'):
            with self.subTest(flag=flag):
                result = self.llmsearch(flag, 'needle')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout,
                                 '5 matches | 1 hour ago | First\n'
                                 "cd ~/'dev/my project' && claudepr first\nlatest needle\n\n"
                                 '1 matches | 2 hours ago | Other\n'
                                 "cd ~/'dev/my project' && codexpr other\nother needle\n\n"
                                 '1 matches | 5 hours ago | Elsewhere\n'
                                 'cd /tmp/elsewhere && claudepr elsewhere\nseparate needle\n\n')

    def test_unbuilt_index_falls_back_to_grep_after_asking_twice(self):
        self.fake_switchboard(code=3, message="search: the transcript index isn't built yet")
        result = self.llmsearch('needle')
        self.assert_grep_results(result)
        self.assertEqual(result.stderr, NOT_BUILT_NOTE)
        self.assertEqual(self.switchboard_calls(),
                         ['[search][--json][--limit][200][--][needle]'] * 2)

    def test_missing_switchboard_falls_back_to_grep_quietly(self):
        result = self.llmsearch('needle')
        self.assert_grep_results(result)
        self.assertEqual(result.stderr, '')

    def test_title_search_bypasses_message_index(self):
        self.fake_switchboard(code=1, message='must not query the message index')
        for args in [('-t', 'FIRST'), ('--title', 'FIRST'), ('--grep', '-t', 'FIRST')]:
            with self.subTest(args=args):
                result = self.llmsearch(*args)
                self.assertEqual((result.returncode, result.stderr), (0, ''))
                blocks = result.stdout.strip().split('\n\n')
                self.assertEqual(len(blocks), 2)
                self.assertTrue(all(block.endswith('first') for block in blocks))
                self.assertEqual(self.switchboard_calls(), [])

    def test_grep_flag_forces_grep(self):
        self.fake_switchboard([self.index_result('claude', 'indexed', 'Indexed', 1, 'needle', 1)])
        for args in [('--grep', 'needle'), ('needle', '--grep')]:
            with self.subTest(args=args):
                self.assert_grep_results(self.llmsearch(*args))
                self.assertEqual(self.switchboard_calls(), [])
        result = self.llmsearch('-g', '--grep', 'needle')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.strip().split('\n\n')), 2)
        self.assertEqual(self.switchboard_calls(), [])

    def test_grep_flag_after_double_dash_is_search_text(self):
        self.fake_switchboard()
        self.assertEqual(self.llmsearch('--', '--grep').returncode, 0)
        self.assertEqual(self.switchboard_calls(),
                         ['[search][--json][--limit][200][--]["--grep"]'])

    def test_other_session_roots_and_records_mode_force_grep(self):
        self.fake_switchboard([self.index_result('claude', 'indexed', 'Indexed', 1, 'needle', 1)])
        for name, value in [('CLAUDESEARCH_ROOT', self.root / 'claude'),
                            ('CODEXSEARCH_ROOT', self.root / 'codex'),
                            ('CLAUDE_CONFIG_DIR', self.root / 'claude'),
                            ('SESSION_SEARCH_RECORDS', '1')]:
            with self.subTest(name=name):
                result = self.llmsearch('needle', **{name: str(value)})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('needle', result.stdout)
                self.assertNotIn('indexed', result.stdout)
                self.assertEqual(self.switchboard_calls(), [])

    def test_index_errors_are_reported_without_grep(self):
        for code, message in [(2, 'search: add a word or phrase that is not excluded'),
                              (1, 'search: the index is unreadable')]:
            with self.subTest(code=code):
                self.fake_switchboard(code=code, message=message)
                result = self.llmsearch('-needle')
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (code, '', message + '\n'))
        self.assertEqual(len(self.switchboard_calls()), 2)

    def test_help_describes_the_index_and_grep(self):
        result = self.llmsearch('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ("Search this machine's transcript index", '--grep', '"a phrase"',
                     'tool:claude|codex', 'after:YYYY-MM-DD'):
            self.assertIn(text, result.stdout)
        self.assertEqual(self.switchboard_calls(), [])


if __name__ == '__main__':
    unittest.main()
