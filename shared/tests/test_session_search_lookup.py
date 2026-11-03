"""Session-id lookup, fork families and Ctrl+C, through the search commands."""

import json
import os
import signal
import subprocess
import time
import unittest

import test_session_search as fixtures


PROJECT = "cd ~/'dev/my project' && "


class LookupTestCase(fixtures.SessionFixtureTestCase):
    search = fixtures.SessionSearchTests.search
    fake_switchboard = fixtures.IndexSearchTests.fake_switchboard
    switchboard_calls = fixtures.IndexSearchTests.switchboard_calls
    index_environment = fixtures.IndexSearchTests.index_environment
    llmsearch = fixtures.IndexSearchTests.llmsearch
    index_result = fixtures.IndexSearchTests.index_result

    def write_fork(self, tool, session_id, parent, hours_ago, subagent=False):
        """A session forked from `parent`, marked the way each tool marks it."""
        path = self.write_session(tool, session_id, ['forked needle'])
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        if tool == 'claude':
            # Claude copies the parent's rows first, each naming the parent.
            rows.insert(0, {'type': 'user', 'message': {'content': 'copied'},
                            'forkedFrom': {'sessionId': parent, 'messageUuid': 'row'}})
        else:
            rows[0]['payload']['forked_from_id'] = parent
            if subagent:
                rows[0]['payload'].update(thread_source='subagent', source={
                    'subagent': {'thread_spawn': {'parent_thread_id': parent}}})
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        self.age(path, hours_ago)
        return path

    def age(self, path, hours_ago):
        modified = time.time() - hours_ago * 3600 - 30
        os.utime(path, (modified, modified))


class SessionLookupTests(LookupTestCase):
    def test_session_flag_finds_ids_by_their_start_in_every_command(self):
        self.fake_switchboard(code=1, message='must not query the message index')
        for tool, count in [('claude', 1), ('codex', 1), ('llm', 2)]:
            for args in [('-s', 'FIR'), ('--session', 'first'), ('fir', '-s')]:
                with self.subTest(tool=tool, args=args):
                    result = (self.llmsearch('--index', *args) if tool == 'llm'
                              else self.search(tool, *args))
                    self.assertEqual((result.returncode, result.stderr), (0, ''))
                    blocks = result.stdout.strip().split('\n\n')
                    self.assertEqual(len(blocks), count)
                    self.assertTrue(all(block.startswith('1 matches | 3 hours ago | first\n')
                                        for block in blocks))
        self.assertEqual(self.switchboard_calls(), [])

    def test_session_flag_matches_only_the_start_of_an_id(self):
        for args in [('-s', 'irst'), ('-s', 'needle')]:
            with self.subTest(args=args):
                result = self.search('llm', *args)
                self.assertEqual((result.returncode, result.stdout), (0, ''))

    def test_title_and_session_together_are_a_usage_error(self):
        for tool in ('claude', 'codex', 'llm'):
            with self.subTest(tool=tool):
                result = self.search(tool, '-s', '-n', 'first')
                self.assertEqual(result.returncode, 2)
                self.assertIn('Usage: ', result.stderr)


class ForkFamilyTests(LookupTestCase):
    def setUp(self):
        super().setUp()
        self.write_fork('claude', 'fork-a', 'first', 1)
        self.write_fork('claude', 'fork-b', 'first', 2)
        self.write_fork('claude', 'fork-a1', 'fork-a', 0.5)

    def test_family_is_a_tree_from_the_oldest_ancestor(self):
        result = self.search('claude', '-s', 'fork-a1', '-f')
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assertEqual(result.stdout,
                         '3 hours ago | first\n'
                         f'{PROJECT}claudepr first\n'
                         '├─ 1 hour ago | fork-a\n'
                         f'│  {PROJECT}claudepr fork-a\n'
                         '│  └─ 30 minutes ago | fork-a1 (match)\n'
                         f'│     {PROJECT}claudepr fork-a1\n'
                         '└─ 2 hours ago | fork-b\n'
                         f'   {PROJECT}claudepr fork-b\n\n')

    def test_matches_in_one_family_print_it_once(self):
        result = self.search('claude', '-n', 'fork', '--forks')
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assertEqual(result.stdout.count('3 hours ago | first\n'), 1)
        self.assertEqual(result.stdout.count(' (match)\n'), 3)

    def test_a_session_without_forks_says_so(self):
        for tool in ('claude', 'llm'):
            with self.subTest(tool=tool):
                result = self.search(tool, '-n', 'second', '-f')
                self.assertEqual((result.returncode, result.stderr), (0, ''))
                self.assertIn('3 hours ago | second\n'
                              f'{PROJECT}claudepr second\nno forks or branches\n\n', result.stdout)

    def test_a_deleted_parent_still_roots_the_tree(self):
        self.write_fork('claude', 'orphan', 'gone', 1)
        result = self.search('claude', '-s', 'orphan', '-f')
        self.assertEqual(result.stdout,
                         'claude session gone (transcript not found)\n'
                         '└─ 1 hour ago | orphan (match)\n'
                         f'   {PROJECT}claudepr orphan\n\n')

    def test_codex_forks_leave_out_subagent_threads_unless_they_match(self):
        self.write_fork('codex', 'branch', 'first', 1)
        self.write_fork('codex', 'worker', 'first', 2, subagent=True)
        result = self.search('codex', '-s', 'branch', '-f')
        self.assertEqual(result.stdout,
                         '3 hours ago | first\n'
                         f'{PROJECT}codexpr first\n'
                         '└─ 1 hour ago | branch (match)\n'
                         f'   {PROJECT}codexpr branch\n\n')
        result = self.search('codex', '-s', 'worker', '-f')
        self.assertIn('2 hours ago | worker (match)', result.stdout)

    def test_index_matches_show_their_families(self):
        self.fake_switchboard([
            self.index_result('claude', 'fork-b', 'fork-b', 1, 'forked needle', 2),
            dict(self.index_result('claude', 'second', 'second', 1, 'needle', 3), host='pi')])
        result = self.llmsearch('--index', 'needle', '-f')
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assertTrue(result.stdout.startswith('3 hours ago | first\n'))
        self.assertIn('└─ 2 hours ago | fork-b (match)\n', result.stdout)
        # Another host's result has no transcript here to look in.
        self.assertNotIn('second', result.stdout)


class InterruptTests(LookupTestCase):
    def test_ctrl_c_prints_one_line_and_exits_130(self):
        folder = self.root / 'bin'
        folder.mkdir()
        started = folder / 'started'
        switchboard = folder / 'llm-switchboard'
        switchboard.write_text(f'#!/bin/sh\ntouch {started}\nexec sleep 30\n')
        switchboard.chmod(0o755)
        with subprocess.Popen([str(fixtures.SCRIPTS / 'llmsearch.sh'), '--index', 'needle'],
                              env=self.index_environment(), start_new_session=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) as process:
            deadline = time.time() + 30
            while not started.exists() and time.time() < deadline:
                time.sleep(0.05)
            # The terminal sends Ctrl+C to the whole foreground process group.
            os.killpg(process.pid, signal.SIGINT)
            output, errors = process.communicate(timeout=30)
        self.assertEqual((process.returncode, output, errors),
                         (130, '', '\nllmsearch: aborted\n'))

    def test_extractor_stops_without_a_traceback(self):
        command = ['python3', str(fixtures.SCRIPTS / 'session_search_extract.py'), '--title',
                   '--ids', '--codex-root', str(self.root / 'codex'), 'first']
        transcript = self.root / 'claude/projects/project/first.jsonl'
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as process:
            process.stdin.write(b'claude\0' + bytes(transcript) + b'\0')
            process.stdin.flush()
            # Its first answer shows it is past startup and waiting for more paths.
            self.assertEqual(process.stdout.read(len(b'claude\0first\0')), b'claude\0first\0')
            process.send_signal(signal.SIGINT)
            errors = process.stderr.read()
            process.wait(timeout=30)
        self.assertEqual((process.returncode, errors), (-signal.SIGINT, b''))


if __name__ == '__main__':
    unittest.main()
