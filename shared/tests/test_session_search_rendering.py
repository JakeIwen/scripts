"""Public search contracts for extraction, byte windows and terminal colour."""

import errno
import json
import os
from pathlib import Path
import pty
import shlex
import shutil
import subprocess
import unittest

import test_session_search as fixtures


class SearchRenderingTests(fixtures.SessionFixtureTestCase):
    fake_switchboard = fixtures.IndexSearchTests.fake_switchboard
    index_result = fixtures.IndexSearchTests.index_result

    def run_search(self, tool, *args, terminal=False, index=False, **overrides):
        environment = dict(os.environ, HOME=str(self.home))
        for name in fixtures.GREP_ONLY_ENVIRONMENT:
            environment.pop(name, None)
        environment.pop('NO_COLOR', None)
        if index:
            for provider in ('claude', 'codex'):
                link = self.home / ('.' + provider)
                if not link.exists():
                    link.symlink_to(self.root / provider)
            environment['PATH'] = str(self.root / 'bin') + os.pathsep + environment['PATH']
        else:
            for provider in ('claude', 'codex'):
                environment[provider.upper() + 'SEARCH_ROOT'] = str(self.root / provider)
        environment.update(overrides)
        command = ['/bin/bash', str(fixtures.SCRIPTS / (tool + 'search.sh')), *args]
        if not terminal:
            result = subprocess.run(command, env=environment, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout
        master, slave = pty.openpty()
        try:
            with subprocess.Popen(command, env=environment, stdout=slave, stderr=subprocess.PIPE) as process:
                os.close(slave)
                slave = None
                chunks = []
                while True:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError as error:
                        if error.errno != errno.EIO:
                            raise
                        break
                    if not chunk:
                        break
                    chunks.append(chunk)
                _, errors = process.communicate(timeout=60)
                self.assertEqual(process.returncode, 0, errors)
                return b''.join(chunks).replace(b'\r\n', b'\n')
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)

    def test_color_is_terminal_only_in_plain_grouped_title_and_index_paths(self):
        self.fake_switchboard([self.index_result('claude', 'first', 'first', 2, 'needle', 3)])
        cases = [(tool, flags, False) for tool in ('claude', 'codex', 'llm')
                 for flags in ([], ['-g'], ['-t'])]
        cases += [('llm', flags, True) for flags in ([], ['-g'])]
        for tool, flags, index in cases:
            with self.subTest(tool=tool, flags=flags, index=index):
                query = 'first' if '-t' in flags else 'needle'
                plain = self.run_search(tool, *flags, query, index=index)
                self.assertNotIn(b'\x1b', plain)
                colored = self.run_search(tool, *flags, query, index=index, terminal=True)
                self.assertIn(b'\x1b[1;36m', colored)
                self.assertRegex(colored, rb'3 hours ago \| (first|second)\x1b\[0m\n')
                disabled = self.run_search(tool, *flags, query, index=index,
                                           terminal=True, NO_COLOR='1')
                self.assertNotIn(b'\x1b[1;36m', disabled)
                if not (tool == 'llm' and flags == ['-g'] and not index):
                    self.assertEqual(disabled, plain)
                    self.assertEqual(colored.replace(b'\x1b[1;36m', b'').replace(b'\x1b[0m', b''), plain)
        records = self.run_search('llm', 'needle', terminal=True, SESSION_SEARCH_RECORDS='1')
        self.assertNotIn(b'\x1b', records)
        self.assertEqual(len([json.loads(line) for line in records.splitlines()]), 4)

    def test_claude_content_rules_choose_one_string_per_event_and_skip_meta(self):
        path = self.write_session('claude', 'extraction', [])
        rows = [
            {'type': 'user', 'isMeta': True, 'message': {'content': 'target hidden'}},
            {'type': 'user', 'isMeta': 0, 'message': {'content': 'target also hidden'}},
            {'type': 'progress', 'message': {'content': 'target hidden progress'}},
            {'type': 'assistant', 'message': {'content': [
                {'type': 'thinking', 'thinking': 'target thought'},
                {'type': 'text', 'text': 'target second string'}]}},
            {'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'name': 'hidden target',
                 'input': {'nested': [{'value': 'target input'}, 'target second input']}}]}},
            {'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'content': [{'type': 'text', 'text': 'target result'},
                                                   {'type': 'image', 'text': 'target hidden'}]}]}},
            {'type': 'attachment', 'attachment': {'type': 'queued_command',
                                                'prompt': 'target queued'}},
            {'type': 'attachment', 'attachment': {'type': 'other', 'prompt': 'target hidden'}},
        ]
        with path.open('a') as stream:
            stream.write(''.join(json.dumps(row) + '\n' for row in rows))
            stream.write('{bad json}\n')
            stream.write(json.dumps({'type': 'user', 'message': {'content': 'target after malformed'}}))
            stream.write('\n{"partial":')
        output = self.run_search('claude', 'target').decode()
        contexts = [block.split('\n', 2)[2] for block in output.rstrip('\n').split('\n\n')]
        self.assertEqual(contexts, ['target after malformed', 'target queued', 'target result',
                                    'target input', 'target thought'])
        records = self.run_search('claude', 'target', SESSION_SEARCH_RECORDS='1')
        self.assertEqual(json.loads(records)['count'], 5)

    def test_codex_searches_every_string_but_counts_events_not_strings(self):
        path = self.write_session('codex', 'extraction', [])
        rows = [{'type': 'custom', 'key target is not a value': 'no',
                 'nested': ['target first', {'key': 'target second'}],
                 'timestamp': '2026-10-18T12:00:00.123Z'},
                {'type': 'target type matches too', 'payload': 'target later'}]
        with path.open('a') as stream:
            stream.write(''.join(json.dumps(row) + '\n' for row in rows))
            stream.write('{broken}\n')
            stream.write(json.dumps({'text': 'target ignored after malformed'}) + '\n')
        output = self.run_search('codex', 'target').decode()
        contexts = [block.split('\n', 2)[2] for block in output.rstrip('\n').split('\n\n')]
        self.assertEqual(contexts, ['target type matches too', 'target first'])
        records = self.run_search('codex', 'target', SESSION_SEARCH_RECORDS='1')
        self.assertEqual(json.loads(records)['count'], 2)

    def test_provider_specific_utf8_windows_are_preserved(self):
        text = '€' * 70 + 'TARGET' + '€' * 90
        for provider in ('claude', 'codex'):
            self.write_session(provider, 'unicode', [text])
        length = subprocess.check_output(['awk', 'BEGIN {print length("€")}'],
                                         env=dict(os.environ, LC_ALL='en_US.UTF-8')).strip()
        self.assertIn(length, (b'1', b'3'))
        encoded = text.encode()
        position = encoded.index(b'TARGET')
        codex = (b'...' + encoded[position - 50:position + 56] + b'...' if length == b'3'
                 else ('...' + '€' * 50 + 'TARGET' + '€' * 50 + '...').encode())
        expected = {'claude': ('...' + '€' * 17 + 'TARGET' + '€' * 17 + '...').encode(),
                    'codex': codex}
        for provider in ('claude', 'codex'):
            output = self.run_search(provider, 'target', LC_ALL='en_US.UTF-8')
            self.assertEqual(output.split(b'\n', 2)[2], expected[provider] + b'\n\n')
            record = json.loads(self.run_search(provider, 'target', LC_ALL='en_US.UTF-8',
                                                SESSION_SEARCH_RECORDS='1'))
            self.assertEqual(record['context'], expected[provider].decode(errors='replace'))

    def test_grep_batches_each_files_excerpts_and_never_starts_jq(self):
        folder = self.root / 'bin'
        folder.mkdir()
        log = self.root / 'awk-starts'
        awk = folder / 'awk'
        awk.write_text('#!/bin/sh\n'
                       'echo start >> ' + shlex.quote(str(log)) + '\n'
                       'exec ' + shlex.quote(shutil.which('awk')) + ' "$@"\n')
        jq = folder / 'jq'
        jq.write_text('#!/bin/sh\necho unexpected jq >&2\nexit 99\n')
        for executable in (awk, jq):
            executable.chmod(0o755)
        output = self.run_search('llm', 'needle', PATH=str(folder) + os.pathsep + os.environ['PATH'])
        self.assertEqual(len(output.rstrip(b'\n').split(b'\n\n')), 6)
        self.assertEqual(log.read_text().splitlines(), ['start'] * 4)

    def test_embedded_nul_preserves_plain_split_but_records_count_events(self):
        for provider in ('claude', 'codex'):
            self.write_session(provider, 'nul', ['target first\0target second'])
            output = self.run_search(provider, '-g', 'target')
            self.assertTrue(output.startswith(b'2 matches | '))
            self.assertTrue(output.endswith(b'target second\n\n'))
            record = json.loads(self.run_search(provider, 'target', SESSION_SEARCH_RECORDS='1'))
            self.assertEqual(record['count'], 1)

    def test_title_whitespace_uses_claudes_unicode_normalization_only(self):
        for provider in ('claude', 'codex'):
            self.write_session(provider, 'Title target', [])
            output = self.run_search(provider, '-t', 'target').decode()
            expected = 'Title target' if provider == 'claude' else 'Title target'
            self.assertTrue(output.split('\n', 1)[0].endswith(expected))


if __name__ == '__main__':
    unittest.main()
