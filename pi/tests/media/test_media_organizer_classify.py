import json
import unittest
from unittest.mock import patch
from pi.apps.media_organizer import config, classify, records, scan
from pi.tests.media._media_organizer_support import Fixture


class ClassifierTests(Fixture, unittest.TestCase):
    def test_offline_snaps_episode_and_season_packs(self):
        self.show('The Example')
        for name in ('the.example.S01E02.1080p', 'The_Example_S02', 'New.Show.S01', 'Film.2025', 'Unknown'):
            self.payload(name)
        entries, folders = scan.scan(self.client)
        proposals, used, error = classify.propose(entries, folders, records.Settings(classifier='offline'))
        self.assertEqual(used, 'offline')
        self.assertIsNone(error)
        self.assertEqual(proposals['the.example.S01E02.1080p'].show, 'The Example')
        self.assertEqual(proposals['The_Example_S02'].confidence, .9)
        self.assertEqual(proposals['Film.2025'].confidence, .3)
        self.assertNotIn('New.Show.S01', proposals)
        self.assertNotIn('Unknown', proposals)

    def fake_codex(self, text):
        answer = self.root / 'canned.json'
        answer.write_text(text)
        executable = self.root / 'codex'
        executable.write_text('#!/bin/sh\nwhile [ "$#" -gt 0 ]; do\n'
                              'if [ "$1" = -o ]; then shift; out="$1"; fi\nshift\ndone\n'
                              'cat >/dev/null\ncp "' + str(answer) + '" "$out"\n')
        executable.chmod(0o700)
        return patch.object(config, 'CODEX', str(executable))

    def test_real_subprocess_reads_canned_schema_answer(self):
        self.show()
        self.payload('The.Example.S01')
        entries, folders = scan.scan(self.client)
        answer = {'proposals': [{'name': entries[0].name, 'category': 'TV', 'show': 'the-example',
                                 'confidence': .97, 'reason': 'Same show'}]}
        with self.fake_codex(json.dumps(answer)):
            proposals, used, error = classify.propose(entries, folders, records.Settings())
        self.assertIsNone(error)
        self.assertEqual(used, records.CLASSIFIER_CODEX)
        self.assertEqual(proposals[entries[0].name].show, 'The Example')
        self.assertTrue(proposals[entries[0].name].existing_show)

    def test_invalid_codex_answer_falls_back(self):
        self.show()
        self.payload('The.Example.S01')
        entries, folders = scan.scan(self.client)
        for answer in ('bad json', '{"proposals": []}', '{"proposals": [{"name": "../escape"}]}'):
            with self.subTest(answer=answer), self.fake_codex(answer):
                proposals, used, error = classify.propose(entries, folders, records.Settings())
                self.assertEqual(used, records.CLASSIFIER_OFFLINE)
                self.assertTrue(error)
                self.assertIn(entries[0].name, proposals)

    def test_schema_limits_names_and_rejects_duplicates(self):
        self.show()
        self.payload('The.Example.S01')
        entries, folders = scan.scan(self.client)
        schema = classify.answer_schema(entries)
        self.assertEqual(schema['properties']['proposals']['items']['properties']['name']['enum'],
                         ['The.Example.S01'])
        row = {'name': entries[0].name, 'category': 'TV', 'show': 'The Example',
               'confidence': .99, 'reason': 'series'}
        with self.fake_codex(json.dumps({'proposals': [row, row]})):
            proposals, used, error = classify.propose(entries, folders, records.Settings())
        self.assertEqual(used, records.CLASSIFIER_OFFLINE)
        self.assertIn('duplicate', error)
        self.assertEqual(proposals[entries[0].name].confidence, .9)

    def test_offline_preserves_years_that_are_part_of_existing_show_names(self):
        self.show('SeaLab 2021')
        self.payload('SeaLab.2021.S01E02')
        entries, folders = scan.scan(self.client)
        proposals, _, _ = classify.propose(entries, folders, records.Settings(classifier='offline'))
        self.assertEqual(proposals[entries[0].name].show, 'SeaLab 2021')
        self.assertEqual(proposals[entries[0].name].confidence, .9)
