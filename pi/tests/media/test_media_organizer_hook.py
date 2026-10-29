import unittest
from unittest.mock import patch
from pi.apps.media_organizer import records, hook, config
from pi.tests.media._media_organizer_support import Fixture


class HookTests(Fixture, unittest.TestCase):
    def test_auto_flow_and_single_rebuild(self):
        self.show()
        self.payload('The.Example.S01')
        with patch('pi.apps.media_organizer.link_rebuild.schedule', return_value='scheduled') as alias:
            result = hook.post_download(records.Settings(classifier='offline'))
        self.assertIsNone(result['error'])
        self.assertEqual(result['run']['moved_count'], 1)
        self.assertEqual(result['plan']['status'], 'applied')
        self.assertEqual(result['alias'], 'scheduled')
        alias.assert_called_once_with()
        self.assertEqual(len(config.LOG_PATH.read_text().splitlines()), 4)

    def test_review_mode_and_failure_never_raise(self):
        self.payload('Movie.2024')
        result = hook.post_download(records.Settings(classifier='offline', auto_apply=False))
        self.assertIsNone(result['run'])
        self.assertTrue((self.new / 'Movie.2024').exists())
        self.transport.unavailable = True
        result = hook.post_download(records.Settings(classifier='offline'))
        self.assertIn('offline', result['error'])
