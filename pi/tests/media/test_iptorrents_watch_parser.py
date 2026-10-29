import gzip
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch
from urllib.error import URLError
from pi.apps.iptorrents_watch import config, parser, transport
from pi.apps.iptorrents_watch.site import Site
from pi.tests.media.iptorrents_watch_support import FIXTURE, WatcherTest


class ParserTests(unittest.TestCase):
    def test_actual_optional_end_tag_rows(self):
        rows = parser.parse(FIXTURE.read_text())
        self.assertGreaterEqual(len(rows), 2)
        row = rows[1]
        self.assertEqual(row.torrent_id, '7779341')
        self.assertEqual(row.name, 'Silo S03 1080p WEBRip DS4K AV1 Opus 5 1-heTOrico')
        self.assertEqual(row.size_bytes, int(8.82 * 1024 ** 3))
        self.assertEqual((row.seeders, row.leechers, row.snatches), (43, 1, 144))
        self.assertEqual(row.category, 'TV/Packs')
        self.assertTrue(row.freeleech)
        self.assertEqual(row.age_text, '1.8 weeks ago')
        self.assertTrue(row.download_path.startswith('/download.php/7779341/'))
        self.assertEqual(rows[-1].category, 'Books')
        self.assertEqual(rows[-1].age_text, '5.6 months ago')

    def test_tableless_and_changed_columns_fail_explicitly(self):
        for text in ('<html>login</html>', FIXTURE.read_text().replace('seeders', 'peers').replace('Seeders', 'Peers')):
            with self.subTest(text=text[:30]), self.assertRaisesRegex(parser.ParseError, 'torrent table/columns'):
                parser.parse(text)

    def test_bad_row_is_not_silently_ignored(self):
        with self.assertRaisesRegex(parser.ParseError, 'row/columns'):
            parser.parse(FIXTURE.read_text().replace('8.82 GB', 'unknown size'))

    def test_gzip_and_logged_in_detection(self):
        paths = []
        def get(path, limit):
            paths.append(path)
            return gzip.compress(FIXTURE.read_bytes())
        self.assertEqual(Site(get).search('Silo & Friends')[1].torrent_id, '7779341')
        self.assertEqual(paths, ['/t?q=Silo%20%26%20Friends;qf=ti'])
        for body in (b'<html>login</html>', FIXTURE.read_bytes().replace(b'/lout.php', b'/login.php')):
            with self.assertRaises(transport.LoginNeeded):
                Site(lambda path, limit: body).search('Silo')

    def test_empty_valid_table(self):
        header = FIXTURE.read_text().split('<tbody>')[0]
        self.assertEqual(parser.parse(header + '<tbody></table>'), [])

    def test_redesigned_or_truncated_body_is_not_an_empty_search(self):
        header = FIXTURE.read_text().split('<tbody>')[0]
        for body in ('<section>new cards</section>', '<tr><td>unfinished row'):
            with self.assertRaisesRegex(parser.ParseError, 'torrent table/columns'):
                parser.parse(header + '<tbody>' + body + '</table>')
        self.assertEqual(parser.parse(header + '<tbody><tr><td colspan="9">No torrents found</tr></table>'), [])

    def test_bounded_gzip(self):
        with self.assertRaisesRegex(ValueError, 'size limit'):
            transport.decode_body(gzip.compress(b'x' * 1000), 100)
        with self.assertRaisesRegex(ValueError, 'compressed'):
            transport.decode_body(b'\x1f\x8bgarbage', 100)


class TransportTests(WatcherTest):
    def setUp(self):
        super().setUp()
        self.requests = []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append((self.path, self.headers.get('Cookie')))
                if self.path.startswith('/redirect'):
                    self.send_response(302)
                    self.send_header('Location', 'http://127.0.0.1:1/stolen')
                    self.end_headers()
                elif self.path.startswith('/forbidden'):
                    self.send_error(403)
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(gzip.compress(FIXTURE.read_bytes()))
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.stack.enter_context(patch.object(config, 'SITE_URL', f'http://127.0.0.1:{self.server.server_port}'))
        config.COOKIE_PATH.write_text(json.dumps({'uid': 'fixture-user', 'pass': 'fixture-secret'}))
        config.COOKIE_PATH.chmod(0o600)

    def close_server(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def test_real_http_request_auth_and_unlabelled_gzip(self):
        self.assertEqual(Site().search('Silo')[1].torrent_id, '7779341')
        self.assertEqual(self.requests[0][0], '/t?q=Silo;qf=ti')
        self.assertTrue(self.requests[0][1] == 'uid=fixture-user; pass=fixture-secret')

    def test_foreign_redirect_and_absolute_path_never_forward_credentials(self):
        with self.assertRaisesRegex(transport.SiteUnavailable, 'outside'):
            transport.site_get('/redirect')
        self.assertEqual(len(self.requests), 1)
        for path in ('//other.test/a', 'https://other.test/a', '/\\other.test/a'):
            with self.assertRaises(ValueError):
                transport.site_get(path)
        self.assertEqual(len(self.requests), 1)

    def test_http_403_means_login_needed(self):
        with self.assertRaises(transport.LoginNeeded):
            transport.site_get('/forbidden')

    def test_bad_cookie_errors_do_not_contain_values(self):
        for payload in ({'uid': 'fixture-user', 'pass': 'fixture-secret\n'}, {'uid': ['fixture-secret']}):
            config.COOKIE_PATH.write_text(json.dumps(payload))
            with self.assertRaises(transport.LoginNeeded) as caught:
                transport.site_get('/t')
            self.assertNotIn('fixture-secret', str(caught.exception))
        self.assertFalse(self.requests)

    def test_cookie_permissions_are_enforced(self):
        config.COOKIE_PATH.chmod(0o644)
        with self.assertRaisesRegex(transport.LoginNeeded, 'mode 600'):
            transport.site_get('/t')

    def test_transport_exception_is_credential_safe(self):
        opener = Mock()
        opener.open.side_effect = URLError('fixture-secret')
        with patch.object(transport.request, 'build_opener', return_value=opener):
            with self.assertRaises(transport.SiteUnavailable) as caught:
                transport.site_get('/t')
        self.assertNotIn('fixture-secret', str(caught.exception))
