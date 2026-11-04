"""llmsearch's per-session default and opt-in directory roll-ups through the CLI."""

import json
import os
import shlex
import time
import unittest

import test_session_search as fixtures


class GrepGroupingTests(fixtures.SessionFixtureTestCase):
    search = fixtures.SessionSearchTests.search

    def setUp(self):
        super().setUp()
        for provider, session, hours, messages in (
                ('claude', 'older', 3, ['old target', 'latest older target']),
                ('codex', 'middle', 2, ['old target', 'middle target', 'latest middle target']),
                ('claude', 'newer', 1, ['latest newer target'])):
            path = self.write_session(provider, session, messages)
            modified = time.time() - hours * 3600 - 30
            os.utime(path, (modified, modified))

    def blocks(self, *flags):
        result = self.search('llm', *flags, 'target')
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        return result.stdout.strip().split('\n\n')

    def test_default_lists_each_session_by_activity_like_the_provider_searches(self):
        blocks = self.blocks()
        self.assertEqual([block.splitlines()[0] for block in blocks], [
            '1 matches | 1 hour ago | newer',
            '3 matches | 2 hours ago | middle',
            '2 matches | 3 hours ago | older',
        ])
        self.assertEqual([shlex.split(block.splitlines()[1])[-2:] for block in blocks], [
            ['claudepr', 'newer'], ['codexpr', 'middle'], ['claudepr', 'older'],
        ])
        self.assertEqual([block.splitlines()[-1] for block in blocks], [
            'latest newer target', 'latest middle target', 'latest older target',
        ])
        provider_blocks = []
        for provider in ('claude', 'codex'):
            result = self.search(provider, 'target')
            self.assertEqual((result.returncode, result.stderr), (0, ''))
            provider_blocks.extend(result.stdout.strip().split('\n\n'))
        self.assertCountEqual(blocks, provider_blocks)
        for flags in (('-g',), ('--group',), ('-e', '-g')):
            self.assertEqual(self.blocks(*flags), blocks)

    def test_dirs_sums_matches_and_names_the_latest_session_per_provider(self):
        blocks = self.blocks('-d')
        self.assertEqual([block.splitlines()[0] for block in blocks], [
            '3 matches | 1 hour ago | newer', '3 matches | 2 hours ago | middle',
        ])
        self.assertEqual([shlex.split(block.splitlines()[1])[-2:] for block in blocks], [
            ['claudepr', 'newer'], ['codexpr', 'middle'],
        ])
        self.assertEqual([block.splitlines()[-1] for block in blocks], [
            'latest newer target', 'latest middle target',
        ])
        self.assertEqual(self.blocks('--dirs'), blocks)

    def test_last_grouping_flag_wins(self):
        sessions, directories, excerpts = self.blocks(), self.blocks('-d'), self.blocks('-e')
        self.assertEqual(len(excerpts), 6)
        self.assertTrue(all('matches | ' not in block.splitlines()[0] for block in excerpts))
        for flags, expected in (
                (('-d', '-g'), sessions), (('--dirs', '--group'), sessions),
                (('-g', '-d'), directories), (('--single', '--dirs'), directories),
                (('-d', '-e'), excerpts), (('--dirs', '--single'), excerpts)):
            with self.subTest(flags=flags):
                self.assertEqual(self.blocks(*flags), expected)

    def test_records_are_ungrouped_and_byte_identical_for_all_modes(self):
        baseline = self.search('llm', 'target', SESSION_SEARCH_RECORDS='1')
        self.assertEqual((baseline.returncode, baseline.stderr), (0, ''))
        records = [json.loads(line) for line in baseline.stdout.splitlines()]
        self.assertEqual(len(records), 3)
        self.assertEqual(sorted(record['count'] for record in records), [1, 2, 3])
        for flags in (('-d',), ('--dirs',), ('-e',), ('-g',), ('-d', '-e')):
            with self.subTest(flags=flags):
                result = self.search('llm', *flags, 'target', SESSION_SEARCH_RECORDS='1')
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (0, baseline.stdout, ''))

    def test_dirs_flags_after_double_dash_and_in_provider_searches_are_literal(self):
        for provider in ('claude', 'codex'):
            self.write_session(provider, 'literal-dirs', ['literal --dirs and -d'])
        for flag in ('-d', '--dirs'):
            for provider in ('claude', 'codex'):
                result = self.search(provider, flag)
                self.assertEqual((result.returncode, result.stderr), (0, ''))
                self.assertIn('literal --dirs and -d\n\n', result.stdout)
            result = self.search('llm', '--', flag)
            self.assertEqual((result.returncode, result.stderr), (0, ''))
            self.assertEqual(result.stdout.count('literal --dirs and -d\n\n'), 2)
            self.assertEqual(self.search('llm', flag).returncode, 2)

    def test_help_describes_default_dirs_and_flag_precedence_without_stores(self):
        for provider in ('claude', 'codex'):
            (self.root / provider).rename(self.root / (provider + '-unused'))
        for flag in ('-h', '--help'):
            result = self.search('llm', flag)
            self.assertEqual((result.returncode, result.stderr), (0, ''))
            for text in ('one result per\nsession, newest activity first',
                         '-g, --group  One result per session', '-d, --dirs',
                         'Group by directory and tool', 'undoes an earlier -e or -d',
                         'The last -d, -g or -e flag wins', 'Directory roll-ups (-d) update live'):
                self.assertIn(text, result.stdout)


class IndexGroupingTests(fixtures.SessionFixtureTestCase):
    fake_switchboard = fixtures.IndexSearchTests.fake_switchboard
    index_result = fixtures.IndexSearchTests.index_result
    index_environment = fixtures.IndexSearchTests.index_environment
    llmsearch = fixtures.IndexSearchTests.llmsearch
    switchboard_calls = fixtures.IndexSearchTests.switchboard_calls

    def setUp(self):
        super().setUp()
        self.fake_switchboard([
            self.index_result('claude', 'shared', 'Older', 1, 'latest older target', 3,
                              matched_hours_ago=0.5),
            self.index_result('claude', 'newer', 'Newer', 1, 'newer target', 1),
            self.index_result('codex', 'shared', 'Middle', 3, 'middle target', 2),
            self.index_result('claude', 'shared', 'Older', 2, 'earlier older target', 3,
                              matched_hours_ago=4),
        ])

    def blocks(self, *flags):
        result = self.llmsearch('--index', *flags, 'target')
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        return result.stdout.strip().split('\n\n')

    def test_index_default_groups_by_session_not_directory_and_orders_by_activity(self):
        blocks = self.blocks()
        self.assertEqual([block.splitlines()[0] for block in blocks], [
            '1 matches | 1 hour ago | Newer',
            '3 matches | 2 hours ago | Middle',
            '3 matches | 3 hours ago | Older',
        ])
        self.assertEqual([shlex.split(block.splitlines()[1])[-2:] for block in blocks], [
            ['claudepr', 'newer'], ['codexpr', 'shared'], ['claudepr', 'shared'],
        ])
        self.assertEqual([block.splitlines()[-1] for block in blocks], [
            'newer target', 'middle target', 'latest older target',
        ])
        self.assertEqual(self.switchboard_calls(), ['[search][--json][--limit][200][--][target]'])

    def test_index_dirs_keeps_summed_counts_and_latest_matching_session(self):
        blocks = self.blocks('-d')
        self.assertEqual([block.splitlines()[0] for block in blocks], [
            '3 matches | 2 hours ago | Middle', '4 matches | 3 hours ago | Older',
        ])
        self.assertEqual([shlex.split(block.splitlines()[1])[-2:] for block in blocks], [
            ['codexpr', 'shared'], ['claudepr', 'shared'],
        ])
        self.assertEqual([block.splitlines()[-1] for block in blocks], [
            'middle target', 'latest older target',
        ])
        self.assertEqual(self.blocks('--dirs'), blocks)

    def test_index_last_grouping_flag_wins_and_single_keeps_each_excerpt(self):
        sessions, directories, excerpts = self.blocks(), self.blocks('-d'), self.blocks('-e')
        self.assertEqual([block.splitlines()[-1] for block in excerpts], [
            'latest older target', 'newer target', 'middle target', 'earlier older target',
        ])
        for flags, expected in (
                (('-g',), sessions), (('--group',), sessions), (('-d', '-g'), sessions),
                (('--dirs', '--group'), sessions), (('-g', '-d'), directories),
                (('--group', '--dirs'), directories), (('-d', '-e'), excerpts),
                (('--dirs', '--single'), excerpts)):
            with self.subTest(flags=flags):
                self.assertEqual(self.blocks(*flags), expected)


if __name__ == '__main__':
    unittest.main()
