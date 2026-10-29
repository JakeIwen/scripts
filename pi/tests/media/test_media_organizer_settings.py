import json
import unittest
from pi.apps.media_organizer import config, settings, records, busy
from pi.tests.media._media_organizer_support import Fixture


class SettingsTests(Fixture, unittest.TestCase):
    def test_round_trip_sparse_private_settings(self):
        self.assertEqual(settings.load(), records.Settings())
        settings.update(['auto_apply=false', 'auto_apply_min_confidence=0.95', 'codex_model=gpt-test'])
        stored = json.loads(config.SETTINGS_PATH.read_text())
        self.assertEqual(set(stored), {'auto_apply', 'auto_apply_min_confidence', 'codex_model'})
        self.assertEqual(config.SETTINGS_PATH.stat().st_mode & 0o777, 0o600)
        self.assertEqual(settings.load().codex_model, 'gpt-test')
        settings.update(['auto_apply=true', 'auto_apply_min_confidence=0.85', 'codex_model='])
        self.assertEqual(json.loads(config.SETTINGS_PATH.read_text()), {})

    def test_reject_bad_types_and_ranges(self):
        for assignment in ('unknown=foo', 'auto_apply=0', 'auto_apply_min_confidence=NaN',
                           'auto_apply_min_confidence=1.01', 'classifier=untrusted'):
            with self.subTest(assignment=assignment), self.assertRaises(ValueError):
                settings.update([assignment])
        with self.assertRaises(ValueError):
            settings.validate({'auto_apply': 'false'})
        with self.assertRaises(ValueError):
            settings.validate({'auto_apply_min_confidence': True})

    def test_busy_reports_current_process_only_and_cleans_on_error(self):
        self.assertIsNone(busy.current())
        with self.assertRaises(RuntimeError):
            with busy.operation(records.JOB_PROPOSE):
                self.assertEqual(busy.current()['stage'], records.JOB_PROPOSE)
                raise RuntimeError('failure')
        self.assertIsNone(busy.current())
