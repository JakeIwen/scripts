import unittest
from pathlib import Path
from pi.apps.media_organizer.qbittorrent_client import Client
from pi.tests.media._media_organizer_support import Fixture


class ClientTests(Fixture, unittest.TestCase):
    def test_login_on_forbidden_and_wait_for_location(self):
        torrent = self.payload('Movie.2024')
        self.transport.auth_required = True
        client = Client(self.transport, ('test-user', 'test-password'))
        self.assertTrue(client.available())
        target = self.media / 'torrent/Movies'
        target.mkdir()
        client.set_location(torrent['hash'], target)
        self.assertEqual(client.wait_for_location(torrent['hash'], target, 0)['save_path'], str(target))
        self.assertTrue(self.transport.logged_in)

    def test_reject_path_escape_without_transport_mutation(self):
        torrent = self.payload('Movie.2024')
        for target in (self.new, self.root / 'outside', self.media / 'torrent/TV',
                       self.media / 'torrent/TV/.hidden', self.media / 'torrent/TV/../Movies'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.client.set_location(torrent['hash'], target)
        self.assertEqual(self.transport.moves, [])

    def test_wait_timeout_and_disappeared_torrent(self):
        torrent = self.payload('Movie.2024', state='moving')
        with self.assertRaises(TimeoutError):
            self.client.wait_for_location(torrent['hash'], self.new, 0)
        with self.assertRaisesRegex(RuntimeError, 'disappeared'):
            self.client.wait_for_location('f' * 40, self.new, 0)
