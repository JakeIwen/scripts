"""Exercise glossary recording, offline rendering and peer copies through the CLI."""

from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse


SCRIPT = Path(__file__).resolve().parents[1] / 'sh' / 'glossary.py'
FAKE_SSH = """#!/bin/sh
[ -d "$FAKE_PEER_HOME" ] || exit 255
for last; do :; done
cd "$FAKE_PEER_HOME" && exec sh -c "$last"
"""


class Page(HTMLParser):
    """Inspect the rendered document, not the implementation's source text."""
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.cards = []
        self.current = None
        self.tags = []
        self.json_parts = []
        self.in_data = False
        self.feed(text)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        self.tags.append(tag)
        if tag == 'article':
            self.current = {'text': [], 'classes': [], 'links': []}
            self.cards.append(self.current)
        if self.current is not None:
            self.current['classes'].extend(attributes.get('class', '').split())
            if tag == 'a':
                self.current['links'].append(attributes.get('href'))
        if tag == 'script' and attributes.get('type') == 'application/json':
            self.in_data = True

    def handle_endtag(self, tag):
        if tag == 'article':
            self.current = None
        if tag == 'script':
            self.in_data = False

    def handle_data(self, text):
        if self.current is not None:
            self.current['text'].append(text)
        if self.in_data:
            self.json_parts.append(text)

    @property
    def groups(self):
        return json.loads(''.join(self.json_parts))


class GlossaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'mac'
        self.home.mkdir()
        self.file = self.home / '.agent/glossary/terms.jsonl'
        self.page = self.home / '.agent/glossary/glossary.html'
        self.known_file = self.home / '.claude/KNOWN-TERMS.md'
        self.peer_home = self.root / 'pi'
        self.peer_file = self.peer_home / '.agent/glossary/terms.jsonl'
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'ssh').write_text(FAKE_SSH)
        (bin_dir / 'ssh').chmod(0o755)
        self.env = {**os.environ, 'HOME': str(self.home),
                    'PATH': f"{bin_dir}:{os.environ['PATH']}",
                    'PYTHONDONTWRITEBYTECODE': '1', 'FAKE_PEER_HOME': str(self.peer_home)}

    def run_script(self, *args, stdin='', peer='', pi=False):
        command = [sys.executable, str(SCRIPT)]
        if pi:
            command = [sys.executable, '-c',
                       "import runpy, sys; sys.platform = 'linux'; runpy.run_path(sys.argv.pop(1), run_name='__main__')",
                       str(SCRIPT)]
        return subprocess.run([*command, '--file', str(self.file), '--peer', peer,
                               '--known-file', str(self.known_file), '--page', str(self.page), *args],
                              input=stdin, capture_output=True, text=True, env=self.env,
                              cwd=SCRIPT.parent, check=False)

    def hook(self, kind, peer='', **event):
        result = self.run_script('--hook', peer=peer,
                                 stdin=json.dumps({'hook_event_name': kind, **event}))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout else None

    def stop(self, message, session='session-1', **event):
        self.assertIsNone(self.hook('Stop', session_id=session, last_assistant_message=message, **event))

    def entries(self, path=None):
        return [json.loads(line) for line in (path or self.file).read_text().splitlines()]

    def write_entries(self, path, entries):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(entry) + '\n' for entry in entries))

    def entry(self, term, identity, at='2026-10-25T10:00:00-07:00', **fields):
        return {'id': identity, 'term': term, 'definition': f'{term} definition', 'at': at,
                'host': 'pi', 'cwd': '/home/pi/project', 'repo': 'project',
                'session': identity, 'link': None, **fields}

    def test_claude_and_codex_blocks_and_continuations(self):
        self.stop('''Result.

**Terms**
- **Bubble sort**: Repeatedly swaps neighbouring out-of-order items.
  It does **O(n²)** comparisons and uses `swap`.
- **Hash set:** Finds items by hashing.
''')
        entries = self.entries()
        self.assertEqual([entry['term'] for entry in entries], ['Bubble sort', 'Hash set'])
        self.assertEqual(entries[0]['definition'],
                         'Repeatedly swaps neighbouring out-of-order items.\n'
                         '  It does **O(n²)** comparisons and uses `swap`.')
        self.stop('**Terms:**\n* **Bubble sort / O(n²):** Quadratic comparisons.', session='codex')
        self.assertEqual(self.entries()[-1]['term'], 'Bubble sort / O(n²)')
        self.assertEqual(self.entries()[-1]['definition'], 'Quadratic comparisons.')

    def test_openers_separators_and_multiple_blocks(self):
        self.stop('''**Terms**:
* **Adapter** — Converts an interface.

Some other prose.
- **Not a term**: Ignore this.

### Terms
- **Observer** - Watches events.

## Next steps
- **Another non-term:** Ignore this too.

# Terms:
- **Seam:** A substitution boundary.
''')
        self.assertEqual([entry['term'] for entry in self.entries()], ['Adapter', 'Observer', 'Seam'])

    def test_messages_without_terms_or_with_non_definition_bullets_record_nothing(self):
        for message in ('No Terms here.\n- **Not a term:** Ordinary list.',
                        '**Terms**\n- Ordinary bullet.\n- **Not a term**: Block has ended.',
                        '```markdown\n**Terms**\n- **Example:** Not a real block.\n```'):
            self.stop(message)
        self.assertFalse(self.file.exists())

    def test_repeated_stop_and_session_term_deduplication(self):
        self.stop('**Terms**\n- **Hash set:** First definition.')
        identity = self.entries()[0]['id']
        self.stop('**Terms**\n- **HASH SET**: Changed definition.')
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(self.entries()[0]['id'], identity)
        self.stop('**Terms**\n- **Hash set:** First definition.', session='session-2')
        self.assertEqual(len(self.entries()), 2)
        self.assertNotEqual(self.entries()[1]['id'], identity)

    def test_concurrent_stop_hooks_keep_every_session_and_deduplicate(self):
        def finish(number):
            return self.run_script('--hook', stdin=json.dumps({
                'hook_event_name': 'Stop', 'session_id': f's-{number % 6}',
                'last_assistant_message': f'**Terms**\n- **Term {number % 6}:** A definition.'}))
        with ThreadPoolExecutor(max_workers=12) as workers:
            results = list(workers.map(finish, range(12)))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '')
        self.assertEqual(len(self.entries()), 6)
        self.assertEqual({entry['term'] for entry in self.entries()}, {f'Term {n}' for n in range(6)})

    def test_metadata_nearest_repo_cwd_and_first_file_url(self):
        outer = self.root / 'outer'
        nested = outer / 'nested'
        cwd = nested / 'src'
        (outer / '.git').mkdir(parents=True)
        cwd.mkdir(parents=True)
        (nested / '.git').write_text('gitdir: /elsewhere')
        self.stop('See [lesson](file:///tmp/lesson.html) and file:///tmp/other.html\n'
                  '**Terms**\n- **Fixture:** Test input.', cwd=str(cwd))
        entry = self.entries()[0]
        self.assertEqual(entry['cwd'], str(cwd))
        self.assertEqual(entry['repo'], 'nested')
        self.assertEqual(entry['link'], 'file:///tmp/lesson.html')
        self.assertEqual(entry['session'], 'session-1')
        self.assertEqual(entry['host'], 'mac' if sys.platform == 'darwin' else 'pi')
        self.assertRegex(entry['at'], r'T\d\d:\d\d:.*[+-]\d\d:\d\d$')
        self.stop('**Terms**\n- **Seam:** Boundary.', session='s2', cwd=str(self.home))
        self.assertIsNone(self.entries()[-1]['repo'])
        self.assertIsNone(self.entries()[-1]['link'])

    def test_bad_hook_inputs_missing_fields_and_unwritable_store_fail_open(self):
        for stdin in ('garbage', '', 'null', '[]', '42', '{"hook_event_name":"Stop"}',
                      '{"hook_event_name":"Stop","last_assistant_message":42}',
                      '{"hook_event_name":"UserPromptSubmit","prompt":[]}'):
            result = self.run_script('--hook', stdin=stdin)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '')
        self.file.parent.parent.write_text('not a directory')
        self.stop('**Terms**\n- **Atomic:** All or nothing.')

    def test_corrupt_store_is_not_overwritten_by_stop(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text('not JSON\n')
        self.stop('**Terms**\n- **Atomic:** All or nothing.')
        self.assertEqual(self.file.read_text(), 'not JSON\n')

    def test_terms_hook_blocks_with_count_url_and_encoded_search(self):
        self.stop('**Terms**\n- **Hash map:** Keyed storage.')
        reply = self.hook('UserPromptSubmit', prompt='  +terms hash map / O(n²)  ')
        self.assertEqual(reply['decision'], 'block')
        self.assertIn('1 term.', reply['reason'])
        url = reply['reason'].splitlines()[0].split(' ', 2)[2]
        self.assertEqual(urlparse(url).path, str(self.page))
        self.assertEqual(parse_qs(urlparse(url).fragment), {'q': ['hash map / O(n²)']})
        self.assertIn('#q=hash%20map%20%2F%20O%28n%C2%B2%29', url)
        self.assertTrue(self.page.is_file())

    def test_pi_url_uses_mac_mount_and_default_paths(self):
        result = self.run_script('--hook', stdin=json.dumps({
            'hook_event_name': 'UserPromptSubmit', 'prompt': '+terms'}), pi=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['reason'],
                         '0 terms. file:///Volumes/pihome/.agent/glossary/glossary.html')

    def test_page_groups_terms_escapes_definitions_and_badges_known(self):
        hostile = '<!-- <script>alert("lesson")</script> --> and `code` **bold** *em* <img src=x onerror=evil()>'
        self.write_entries(self.file, [self.entry('Hash set', 'old'),
                           self.entry('HASH SET', 'new', at='2026-10-26T10:00:00-07:00',
                                      definition=hostile, link='file:///tmp/lesson.html'),
                           self.entry('Adapter', 'adapter', link='javascript:alert(1)')])
        self.known_file.parent.mkdir(parents=True)
        self.known_file.write_text('# Known terms\n\n- hash SET\n')
        result = self.run_script('render')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip().endswith('/.agent/glossary/glossary.html'))
        text = self.page.read_text()
        page = Page(text)
        self.assertEqual(len(page.cards), 2)
        self.assertIn('badge', page.cards[0]['classes'])
        self.assertNotIn('badge', page.cards[1]['classes'])
        self.assertIn(hostile.split(' and ')[0], ''.join(page.cards[0]['text']))
        self.assertIn('Older definitions (1)', ''.join(page.cards[0]['text']))
        self.assertEqual(page.cards[0]['links'], ['file:///tmp/lesson.html'])
        self.assertEqual(page.cards[1]['links'], [])
        self.assertEqual(page.tags.count('script'), 2)
        self.assertNotIn('img', page.tags)
        for tag in ('code', 'strong', 'em'):
            self.assertIn(tag, page.tags)
        self.assertEqual(page.groups[0]['entries'][0]['definition'], hostile)
        self.assertTrue(page.groups[0]['known'])
        self.assertIn('<\\/script>', text)

    def test_other_prompts_and_events_pass_through(self):
        for prompt in ('fix +terms', '+termsish', '+known fixture', 'terms'):
            self.assertIsNone(self.hook('UserPromptSubmit', prompt=prompt), prompt)
        self.assertIsNone(self.hook('SessionStart', prompt='+terms'))
        self.assertIsNone(self.hook('Other', last_assistant_message='**Terms**\n- **X:** Ignore.'))
        self.assertFalse(self.file.exists())
        self.assertFalse(self.page.exists())

    def test_peer_merge_both_ways_is_union_by_id(self):
        local = self.entry('Adapter', 'mac', host='mac')
        remote = self.entry('Observer', 'pi')
        self.write_entries(self.file, [local])
        self.write_entries(self.peer_file, [remote, local])
        reply = self.hook('UserPromptSubmit', peer='pi', prompt='+terms')
        self.assertIn('2 terms.', reply['reason'])
        self.assertIn('synced with pi', reply['reason'])
        self.assertEqual({entry['id'] for entry in self.entries()}, {'mac', 'pi'})
        self.assertEqual(self.entries(), self.entries(self.peer_file))
        self.assertEqual(self.run_script('sync', peer='pi').returncode, 0)
        self.assertEqual(len(self.entries()), 2)

    def test_unreachable_peer_keeps_local_and_later_sync_carries_it(self):
        self.stop('**Terms**\n- **Memoization:** Cache prior calls.')
        reply = self.hook('UserPromptSubmit', peer='pi', prompt='+terms')
        self.assertIn('pi unreachable; kept local', reply['reason'])
        self.assertTrue(self.page.is_file())
        result = self.run_script('sync', peer='pi')
        self.assertEqual(result.returncode, 1)
        self.peer_home.mkdir()
        result = self.run_script('sync', peer='pi')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.entries(self.peer_file), self.entries())

    def test_remove_drops_all_occurrences_on_both_machines(self):
        self.write_entries(self.file, [self.entry('Adapter', 'a1'), self.entry('Observer', 'o')])
        self.write_entries(self.peer_file, [self.entry('ADAPTER', 'a2'), self.entry('Seam', 's')])
        result = self.run_script('remove', 'adapter, SEAM', peer='pi')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([entry['term'] for entry in self.entries()], ['Observer'])
        self.assertEqual(self.entries(), self.entries(self.peer_file))

    def test_list_defaults_to_newest_occurrence_per_term_and_handles_timezones(self):
        self.write_entries(self.file, [self.entry('Adapter', 'a1', at='2026-10-25T23:00:00-07:00'),
                           self.entry('Observer', 'o', at='2026-10-26T03:00:00+00:00'),
                           self.entry('ADAPTER', 'a2', at='2026-10-25T22:00:00-07:00')])
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(),
                         ['2026-10-25 · project · Adapter', '2026-10-26 · project · Observer'])
        self.assertEqual(self.run_script('list').stdout, result.stdout)
        self.assertEqual(self.run_script('sync').returncode, 1)
        self.assertEqual(self.run_script('--help').returncode, 0)
        self.assertEqual(self.run_script('remove').returncode, 2)


if __name__ == '__main__':
    unittest.main()
