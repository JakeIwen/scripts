"""-D/--here: only sessions started in the current directory or below it."""

import json
import os
from pathlib import Path
import re
import subprocess
import unittest

import test_session_search as fixtures


class HereFlagTests(fixtures.SessionFixtureTestCase):
    """The grep scan, through every command, run from different directories."""

    def setUp(self):
        super().setUp()
        self.sub = self.project + '/sub dir'
        for tool in ('claude', 'codex'):
            self.write_session(tool, 'inside', ['scope hit'])
            self.write_session(tool, 'below', ['scope hit', 'scope hit'], cwd=self.sub)
            self.write_session(tool, 'slashed', ['scope hit'], cwd=self.project + '/')
            # A sibling sharing the directory's name as a prefix is outside it.
            self.write_session(tool, 'sibling', ['scope hit'], cwd=self.project + '2')
            self.write_session(tool, 'above', ['scope hit'], cwd=str(self.home / 'dev'))
            self.write_session(tool, 'nowhere', ['scope hit'], cwd='')
        for folder in (self.sub, str(self.home / 'dev')):
            Path(folder).mkdir(parents=True, exist_ok=True)

    def search_from(self, cwd, tool, *args, **overrides):
        environment = dict(os.environ, HOME=str(self.home))
        for provider in ('claude', 'codex'):
            environment[provider.upper() + 'SEARCH_ROOT'] = str(self.root / provider)
        environment.update(overrides)
        return subprocess.run([str(fixtures.SCRIPTS / (tool + 'search.sh')), *args], cwd=cwd,
                              env=environment, capture_output=True, text=True, timeout=60)

    def resumed(self, result):
        """The session ids the result's resume commands name, in print order."""
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        return re.findall(r'(?:claude|codex)pr (\S+)$', result.stdout, re.MULTILINE)

    def test_here_keeps_the_directory_and_its_subdirectories_in_every_command(self):
        for tool, expected in (('claude', ['inside', 'below', 'slashed']),
                               ('codex', ['inside', 'below', 'slashed']),
                               ('llm', ['inside', 'below', 'slashed'] * 2)):
            for flag in ('-D', '--here'):
                with self.subTest(tool=tool, flag=flag):
                    found = self.resumed(self.search_from(self.project, tool, flag, 'scope'))
                    self.assertCountEqual(found, expected)
        self.assertCountEqual(self.resumed(self.search_from(self.sub, 'llm', '-D', 'scope')),
                              ['below', 'below'])
        above = self.resumed(self.search_from(str(self.home / 'dev'), 'llm', '-D', 'scope'))
        self.assertCountEqual(above, ['inside', 'below', 'slashed', 'sibling', 'above'] * 2)

    def test_here_resolves_a_symlinked_working_directory(self):
        link = self.home / 'link'
        link.symlink_to(self.project)
        found = self.resumed(self.search_from(str(link), 'claude', '-D', 'scope'))
        self.assertCountEqual(found, ['inside', 'below', 'slashed'])

    def test_here_applies_to_titles_single_excerpts_records_and_directory_rollups(self):
        # The fixture's own "second" is titled with an e too; "above" is outside.
        titles = self.resumed(self.search_from(self.project, 'llm', '-t', '-D', 'e'))
        self.assertCountEqual(titles, ['inside', 'below', 'slashed', 'second'] * 2)
        single = self.resumed(self.search_from(self.project, 'claude', '-e', '-D', 'scope'))
        self.assertCountEqual(single, ['inside', 'below', 'below', 'slashed'])
        records = self.search_from(self.project, 'llm', '-D', 'scope', SESSION_SEARCH_RECORDS='1')
        self.assertEqual((records.returncode, records.stderr), (0, ''))
        cwds = {json.loads(line)['cwd'] for line in records.stdout.splitlines()}
        self.assertEqual(cwds, {self.project, self.sub, self.project + '/'})
        rollup = self.search_from(self.project, 'llm', '-d', '-D', 'scope')
        self.assertEqual((rollup.returncode, rollup.stderr), (0, ''))
        self.assertNotIn('sibling', rollup.stdout)
        self.assertNotIn('above', rollup.stdout)
        self.assertIn('below', rollup.stdout)
        forks = self.search_from(self.project, 'llm', '-f', '-D', 'scope')
        self.assertEqual((forks.returncode, forks.stderr), (0, ''))
        self.assertIn('inside', forks.stdout)
        self.assertNotIn('sibling', forks.stdout)

    def test_here_keeps_directory_names_with_newlines_whole(self):
        # $(pwd) would drop a trailing newline; the display quirk cuts at the first.
        for name, lookalike in (('mid\nline', 'mid'), ('trailing\n', 'trailing')):
            folder = self.project + '/' + name
            Path(folder).mkdir()
            Path(self.project, lookalike).mkdir()
            for tool in ('claude', 'codex'):
                self.write_session(tool, 'odd', ['odd hit'], cwd=folder)
            with self.subTest(name=name):
                found = self.resumed(self.search_from(folder, 'llm', '-D', 'odd hit'))
                self.assertEqual(found, ['odd', 'odd'])
                other = self.search_from(self.project + '/' + lookalike, 'llm', '-D', 'odd hit')
                self.assertEqual((other.returncode, other.stdout), (0, ''))

    def test_here_after_double_dash_is_search_text(self):
        self.write_session('claude', 'literal', ['the -D flag'], cwd='/tmp/elsewhere')
        found = self.resumed(self.search_from(self.project, 'llm', '--', '-D'))
        self.assertEqual(found, ['literal'])

    def test_help_describes_the_flag_in_every_command(self):
        for tool in ('claude', 'codex', 'llm'):
            result = self.search_from(self.project, tool, '--help')
            self.assertEqual(result.returncode, 0)
            self.assertIn('-D, --here   Only sessions started in the current directory', result.stdout)


class IndexHereTests(fixtures.SessionFixtureTestCase):
    fake_switchboard = fixtures.IndexSearchTests.fake_switchboard
    index_result = fixtures.IndexSearchTests.index_result
    index_environment = fixtures.IndexSearchTests.index_environment

    def setUp(self):
        super().setUp()
        self.sub = self.project + '/sub dir'
        Path(self.sub).mkdir(parents=True)
        self.fake_switchboard([
            self.index_result('claude', 'inside', 'Inside', 1, 'inside needle', 1),
            self.index_result('codex', 'below', 'Below', 2, 'below needle', 2, cwd=self.sub),
            self.index_result('claude', 'slashed', 'Slashed', 1, 'slashed needle', 3,
                              cwd=self.project + '/'),
            self.index_result('claude', 'sibling', 'Sibling', 1, 'sibling needle', 4,
                              cwd=self.project + '2'),
            self.index_result('codex', 'above', 'Above', 1, 'above needle', 5,
                              cwd=str(self.home / 'dev')),
            self.index_result('codex', 'nowhere', 'Nowhere', 1, 'nowhere needle', 6, cwd=''),
        ])

    def llmsearch_from(self, cwd, *args):
        return subprocess.run([str(fixtures.SCRIPTS / 'llmsearch.sh'), *args], cwd=cwd,
                              env=self.index_environment(), capture_output=True, text=True,
                              timeout=60)

    def headers(self, cwd, *flags):
        result = self.llmsearch_from(cwd, '--index', *flags, 'scope')
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        return [block.splitlines()[0] for block in result.stdout.strip().split('\n\n') if block]

    def test_index_results_outside_the_directory_are_left_out(self):
        self.assertEqual(self.headers(self.project, '-D'), [
            '1 matches | 1 hour ago | Inside', '2 matches | 2 hours ago | Below',
            '1 matches | 3 hours ago | Slashed'])
        self.assertEqual(self.headers(self.sub, '-D'), ['2 matches | 2 hours ago | Below'])
        self.assertEqual(len(self.headers(self.project)), 6)
        # From the root everything with a directory is within it; no cwd is not.
        self.assertEqual(len(self.headers('/', '-D')), 5)

    def test_index_directory_rollups_and_forks_respect_the_directory(self):
        self.assertEqual(self.headers(self.project, '-d', '-D'), [
            '1 matches | 1 hour ago | Inside', '2 matches | 2 hours ago | Below',
            '1 matches | 3 hours ago | Slashed'])
        for provider, session in (('claude', 'inside'), ('claude', 'sibling')):
            self.write_session(provider, session, ['needle'],
                               cwd=self.project if session == 'inside' else self.project + '2')
        forks = self.llmsearch_from(self.project, '--index', '-f', '-D', 'scope')
        self.assertEqual((forks.returncode, forks.stderr), (0, ''))
        self.assertIn('inside', forks.stdout)
        self.assertNotIn('sibling', forks.stdout)


if __name__ == '__main__':
    unittest.main()
