"""Exercise session search output through both shell entry points."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'sh'


class SessionSearchTests(unittest.TestCase):
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

    def search(self, tool, *args):
        environment = dict(os.environ, HOME=str(self.home))
        for provider in ('claude', 'codex'):
            environment[provider.upper() + 'SEARCH_ROOT'] = str(self.root / provider)
        # The Pi can spend over ten seconds starting jq processes under load.
        return subprocess.run([str(SCRIPTS / (tool + 'search.sh')), *args],
                              env=environment, capture_output=True, text=True, timeout=60)

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
            self.assertIn('Usage: llmsearch [flags] [--] <term>', result.stdout)
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


if __name__ == '__main__':
    unittest.main()
