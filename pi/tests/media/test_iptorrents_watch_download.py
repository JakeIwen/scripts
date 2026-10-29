from unittest.mock import Mock
from pi.apps.iptorrents_watch import config, download, state, status
from pi.apps.iptorrents_watch.records import CANDIDATE_DOWNLOADED
from pi.tests.media.iptorrents_watch_support import WatcherTest, candidate

TORRENT = b'd4:infod4:name4:test6:lengthi1eee'


class DownloadTests(WatcherTest):
    def setUp(self):
        super().setUp()
        state.save_candidates([candidate()])
        self.site = Mock()
        self.site.download.return_value = TORRENT

    def test_explicit_atomic_download_and_repeat_is_noop(self):
        row = download.download('123', self.site)
        self.assertEqual(row.status, CANDIDATE_DOWNLOADED)
        self.assertIsNotNone(row.downloaded_at)
        files = list(config.TFILES_DIR.iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, candidate().name + ' [IPT].torrent')
        self.assertEqual(files[0].read_bytes(), TORRENT)
        self.assertEqual(download.download('123', self.site), row)
        self.assertEqual(self.site.download.call_count, 1)

    def test_html_and_oversize_write_nothing_and_save_failure(self):
        for body in (b'<html>login needed</html>', b'd4:info' + b'x' * config.MAX_TORRENT_BYTES + b'e'):
            self.site.download.return_value = body
            with self.assertRaises(ValueError):
                download.download('123', self.site)
            self.assertEqual(list(config.TFILES_DIR.iterdir()), [])
            self.assertIsNotNone(state.candidates()[0].error)
            self.assertEqual(state.candidates()[0].status, 'new')

    def test_existing_file_not_overwritten(self):
        path = config.TFILES_DIR / download.filename(candidate().name)
        path.write_bytes(b'previous torrent')
        with self.assertRaises(FileExistsError):
            download.download('123', self.site)
        self.assertEqual(path.read_bytes(), b'previous torrent')
        self.assertEqual(list(config.TFILES_DIR.iterdir()), [path])

    def test_dismiss_hides_and_refuses_download(self):
        row = download.dismiss('123')
        self.assertEqual(row.status, 'dismissed')
        self.assertEqual(status.status().candidates, [])
        with self.assertRaisesRegex(ValueError, 'dismissed'):
            download.download('123', self.site)
        self.site.download.assert_not_called()

    def test_filename_sanitization(self):
        name = '../../Silo S01/E02\\1080p x264\x00'
        output = download.filename(name)
        self.assertNotIn('/', output)
        self.assertNotIn('\\', output)
        self.assertNotIn('\x00', output)
        self.assertLessEqual(len(download.filename('é' * 400).encode()), 255)

    def test_bad_id_or_path_never_reaches_transport(self):
        for value in ('../123', '999'):
            with self.assertRaises(ValueError):
                download.download(value, self.site)
        row = candidate()
        row.download_path = 'https://other.test/stolen'
        state.save_candidates([row])
        with self.assertRaises(ValueError):
            download.download('123', self.site)
        self.site.download.assert_not_called()
