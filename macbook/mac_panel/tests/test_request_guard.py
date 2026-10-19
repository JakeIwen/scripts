from email.message import Message
import json
import unittest

from panel_contracts import ApiError
from request_guard import guard_request

from panel_paths import HOST
from panel_test_support import auth, request, running_server


class RequestTests(unittest.TestCase):
    def test_duplicate_headers_fail_closed(self):
        headers = Message()
        headers['Host'] = HOST
        headers['Host'] = 'evil.example:8769'
        with self.assertRaises(ApiError) as raised:
            guard_request('GET', '/healthz', headers, 'fixture-token')
        self.assertEqual(raised.exception.status, 403)
        headers = Message()
        for name, value in {'Host': HOST, 'X-Panel-Token': 'fixture-token',
                            'Origin': f'http://{HOST}', 'Content-Type': 'application/json'}.items():
            headers[name] = value
        headers['Content-Length'] = '2'
        headers['Content-Length'] = '4'
        with self.assertRaises(ApiError) as raised:
            guard_request('POST', '/api/stop', headers, 'fixture-token')
        self.assertEqual(raised.exception.status, 400)

    def test_host_allowlist_health_and_security_headers(self):
        with running_server() as server:
            self.assertEqual(server.server_address[0], '127.0.0.1')
            for host in (None, 'evil.example:8769', '127.0.0.1:9999', 'localhost', '[::1]:8769'):
                with self.subTest(host=host):
                    status, headers, _ = request(server, '/healthz', headers={'Host': host})
                    self.assertEqual(status, 403)
                    self.assertNotIn('Access-Control-Allow-Origin', headers)
            for host in (HOST, 'localhost:8769'):
                status, headers, body = request(server, '/healthz', headers={'Host': host})
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)['pid'], server.record['pid'])
                self.assertNotIn('token', json.loads(body))
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
                self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
                self.assertNotIn('Access-Control-Allow-Origin', headers)

    def test_token_on_all_api_reads_and_page_injection(self):
        with running_server() as server:
            for token in (None, '', 'wrong'):
                status, _, _ = request(server, '/api/settings', headers={'Host': HOST, 'X-Panel-Token': token})
                self.assertEqual(status, 403)
            status, _, body = request(server, '/', headers={'Host': HOST})
            self.assertEqual(status, 200)
            self.assertIn(f'content="{server.record["token"]}"', body)
            self.assertNotIn('__PANEL_TOKEN__', body)
            self.assertEqual(request(server, '/api/settings', headers=auth(server))[0], 200)

    def test_post_origin_json_limits_and_no_cors(self):
        with running_server() as server:
            cases = [({'Origin': None}, '{}', 403),
                     ({'Origin': 'http://localhost:8769'}, '{}', 403),
                     ({'Origin': 'http://evil.example:8769'}, '{}', 403),
                     ({'Content-Type': 'text/plain'}, '{}', 415),
                     ({}, '{', 400), ({}, '[]', 400), ({}, '{"x":NaN}', 400),
                     ({'Content-Length': '65537'}, b'', 413)]
            for overrides, body, expected in cases:
                with self.subTest(overrides=overrides, body=body):
                    status, headers, _ = request(server, '/api/settings', 'POST', body,
                                                 {**auth(server, True), **overrides})
                    self.assertEqual(status, expected)
                    self.assertNotIn('Access-Control-Allow-Origin', headers)
            self.assertEqual(request(server, '/api/settings', 'POST', '{}', auth(server, True))[0], 200)
            status, headers, _ = request(server, '/api/settings', 'OPTIONS', headers=auth(server))
            self.assertEqual(status, 405)
            self.assertNotIn('Access-Control-Allow-Origin', headers)

    def test_stop_requires_token_and_origin(self):
        with running_server() as server:
            for overrides in ({'X-Panel-Token': 'wrong'}, {'Origin': None}):
                self.assertEqual(request(server, '/api/stop', 'POST', '{}',
                                         {**auth(server, True), **overrides})[0], 403)
                self.assertEqual(request(server, '/healthz', headers={'Host': HOST})[0], 200)
            self.assertEqual(request(server, '/api/stop', 'POST', '{"force":true}', auth(server, True))[0], 400)
            status, _, body = request(server, '/api/stop', 'POST', '{}', auth(server, True))
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)['stopping'])


if __name__ == '__main__':
    unittest.main()
