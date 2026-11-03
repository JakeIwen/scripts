"""Exercise the +links hook the way Claude Code and Codex call it."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'sh' / 'reply_links.py'
HOSTED = 'https://claude.ai/code/artifact/88448d7d-d824-466c-be38-c976a8da45e1'


def claude_row(text, sidechain=False, at='2026-11-02T22:00:00Z'):
    return {'type': 'assistant', 'isSidechain': sidechain, 'timestamp': at,
            'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': text}]}}


def codex_row(text, role='assistant'):
    return {'timestamp': '2026-11-02T22:00:00Z', 'type': 'response_item',
            'payload': {'type': 'message', 'role': role,
                        'content': [{'type': 'output_text', 'text': text}]}}


class ReplyLinksTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.store = self.home / '.agent' / 'artifacts'
        self.store.mkdir(parents=True)
        self.page = self.store / '20261102-1654-config-long-reply-guard.html'
        self.page.write_text('<!doctype html><head><title>Long reply guard</title></head>')
        self.transcript = self.home / 'session.jsonl'

    def write(self, *rows):
        self.transcript.write_text(''.join(json.dumps(row) + '\n' for row in rows))

    def run_script(self, *args, stdin=''):
        env = {**os.environ, 'HOME': str(self.home)}
        result = subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, env=env,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def hook(self, prompt, transcript=None):
        event = {'hook_event_name': 'UserPromptSubmit', 'session_id': 's', 'prompt': prompt,
                 'transcript_path': str(transcript or self.transcript)}
        out = self.run_script('--hook', stdin=json.dumps(event))
        return json.loads(out) if out else None

    def test_lists_each_link_once_with_page_titles(self):
        url = self.page.as_uri()
        self.write(claude_row(f'Summary page:\n{url}\nHosted: {HOSTED}.'),
                   {'type': 'user', 'message': {'content': 'file:///Users/x/typed.html'}},
                   claude_row(f'Repeating the report: **{url}**'))
        result = self.hook('+links')
        self.assertEqual(result['decision'], 'block')
        lines = result['reason'].splitlines()
        self.assertEqual(lines[0], "+links: 2 links from this session's replies, oldest first")
        self.assertTrue(lines[1].endswith('  Long reply guard'), lines[1])
        self.assertEqual(lines[2], f'    {url}')
        self.assertTrue(lines[3].endswith('  Hosted: Long reply guard'), lines[3])
        self.assertEqual(lines[4], f'    {HOSTED}')
        self.assertEqual(len(lines), 5)
        self.assertNotIn('typed.html', result['reason'])

    def test_marks_missing_files_and_skips_subagent_replies(self):
        self.write(claude_row('Gone: file:///nowhere/old-report.html'),
                   claude_row(f'Worker saw {self.page.as_uri()}', sidechain=True))
        reason = self.hook('+links')['reason']
        self.assertIn('old-report.html (not found)', reason)
        self.assertNotIn(self.page.name, reason)

    def test_reads_codex_rollouts(self):
        older = self.store / 'older.html'
        older.write_text('<title>Older &amp; plain</title>')
        self.write({'type': 'session_meta', 'payload': {'source': 'cli'}},
                   codex_row(f'Opened [the page]({self.page.as_uri()})'),
                   codex_row(f'[Full report]({older}) and [code]({self.home}/tool.py:12)'),
                   codex_row(f'file://{self.home}/from-user.html', role='user'))
        reason = self.hook('+links')['reason']
        self.assertIn('Long reply guard', reason)
        self.assertIn(f'    {self.page.as_uri()}\n', reason + '\n')
        self.assertIn(f'Older & plain\n    {older.as_uri()}', reason)
        self.assertNotIn('tool.py', reason)
        self.assertNotIn('from-user.html', reason)

    def test_points_at_the_index_and_says_when_there_are_none(self):
        (self.store / 'index.html').write_text('<title>Agent artifacts</title>')
        self.write(claude_row('No links here.'))
        reason = self.hook('+links')['reason']
        self.assertEqual(reason.splitlines(), [
            "+links: no links in this session's replies yet.",
            f'All saved pages: {(self.store / "index.html").as_uri()}',
        ])

    def test_hook_shows_the_newest_twenty(self):
        self.write(*[claude_row(f'file:///tmp/report-{n}.txt') for n in range(25)])
        reason = self.hook('+links')['reason']
        self.assertIn('(5 earlier ones left out', reason)
        self.assertNotIn('report-4.txt', reason)
        self.assertIn('report-5.txt', reason)
        self.assertIn('25. ', reason)
        self.assertEqual(len(self.run_script(str(self.transcript)).splitlines()), 51)

    def test_other_prompts_and_missing_transcripts_pass_or_explain(self):
        self.write(claude_row(self.page.as_uri()))
        self.assertIsNone(self.hook('what do +links show?'))
        self.assertEqual(self.run_script('--hook', stdin='not json'), '')
        missing = self.hook('+links', transcript=self.home / 'absent.jsonl')
        self.assertEqual(missing['reason'], '+links: this session has no transcript on disk yet.')


if __name__ == '__main__':
    unittest.main()
