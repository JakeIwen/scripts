import unittest
from unittest.mock import patch
from pi.apps.media_organizer import scan, records, status
from pi.tests.media._media_organizer_support import Fixture


class ScanTests(Fixture, unittest.TestCase):
    def test_joins_and_skips(self):
        self.show()
        self.payload('The.Example.S01E01')
        self.payload('Downloading', progress=.4)
        self.payload('Moving', state='moving')
        self.payload('Checking', state='checkingUP')
        self.payload('Loose', tracked=False)
        (self.new / 'nohup.out').write_text('log output')
        (self.new / '._Movie.mkv').write_bytes(b'finder metadata')
        (self.new / 'link').symlink_to(self.root)
        entries, shows = scan.scan(self.client)
        rows = {e.name: e for e in entries}
        self.assertTrue(rows['The.Example.S01E01'].tracked)
        self.assertIsNone(rows['The.Example.S01E01'].skip_reason)
        self.assertEqual(rows['Downloading'].skip_reason, records.SKIP_INCOMPLETE)
        self.assertEqual(rows['Checking'].skip_reason, records.SKIP_INCOMPLETE)
        self.assertEqual(rows['Moving'].skip_reason, records.SKIP_MOVING)
        self.assertEqual(rows['link'].skip_reason, records.SKIP_UNREADABLE)
        self.assertFalse(rows['Loose'].tracked)
        self.assertEqual(rows['nohup.out'].skip_reason, records.SKIP_NOT_MEDIA)
        self.assertEqual(rows['._Movie.mkv'].skip_reason, records.SKIP_HIDDEN)
        self.assertEqual(shows['TV'], ['The Example'])
        summary = status.status(self.client)
        self.assertEqual((summary.new_count, summary.tracked_count, summary.incomplete_count), (8, 4, 2))

    def test_failure_does_not_turn_torrents_into_untracked(self):
        self.payload('Movie.2025')
        self.transport.unavailable = True
        with self.assertRaises(RuntimeError):
            scan.scan(self.client)

    def test_ambiguous_owner_and_mismatched_save_path_are_skipped(self):
        torrent = self.payload('Movie.2025')
        self.transport.torrents.append(dict(torrent, hash='f' * 40))
        entries, _ = scan.scan(self.client)
        self.assertEqual(entries[0].skip_reason, records.SKIP_NESTED)
        self.transport.torrents.pop()
        torrent['save_path'] = str(self.new / 'somewhere')
        entries, _ = scan.scan(self.client)
        self.assertEqual(entries[0].skip_reason, records.SKIP_NESTED)

    def test_missing_mount_fails_closed(self):
        with patch('pathlib.Path.is_mount', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'not mounted'):
                scan.scan(self.client)

    def test_nested_torrent_payload_is_never_treated_as_untracked(self):
        torrent = self.payload('Misplaced.2025.torrent')
        container = self.new / torrent['name']
        nested = container / 'Actual.S01E01'
        nested.mkdir()
        torrent.update(name=nested.name, content_path=str(nested), save_path=str(container))
        entries, _ = scan.scan(self.client)
        self.assertTrue(entries[0].tracked)
        self.assertEqual(entries[0].torrent_hash, torrent['hash'])
        self.assertEqual(entries[0].skip_reason, records.SKIP_NESTED)
