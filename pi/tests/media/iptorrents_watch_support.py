"""Isolated watcher state and public API payloads for watcher tests."""
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from pi.apps.iptorrents_watch import config
from pi.apps.iptorrents_watch.records import Candidate, WantedShow, SOURCE_MANUAL

FIXTURE = Path(__file__).with_name('iptorrents_search_fixture.html')


def wanted(name='Silo', season=1, episode=1):
    return WantedShow(name, SOURCE_MANUAL, None, None, season, episode, '2026-01-01T00:00:00+00:00')


def candidate(name='Silo S01E02 1080p WEB H264-TEST', torrent_id='123'):
    return Candidate(torrent_id, name, 'Silo', 1, 2, 1024, 5, 1, 10, False, '1 day ago',
                     'TV/x264', f'/download.php/{torrent_id}/name.torrent', '2026-01-02T00:00:00+00:00')


class WatcherTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for key, value in {
            'STATE_DIR': self.root / 'state', 'WATCHLIST_PATH': self.root / 'state/watchlist.json',
            'CANDIDATES_PATH': self.root / 'state/candidates.json', 'STATUS_PATH': self.root / 'state/status.json',
            'LOG_PATH': self.root / 'watch.log', 'COOKIE_PATH': self.root / 'cookies.json',
            'TFILES_DIR': self.root / 'tfiles',
        }.items():
            self.stack.enter_context(patch.object(config, key, value))
        config.TFILES_DIR.mkdir()

    def library(self, url):
        if url.endswith('/api/library'):
            return {'shows': [], 'up_next': []}
        if url.endswith('/api/v2/torrents/info'):
            return []
        raise AssertionError('unexpected local API request')
