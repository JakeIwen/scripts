"""Offline budget, wire-protocol and cache acceptance for one guard invocation."""

from concurrent.futures import ThreadPoolExecutor
import fcntl
import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from shared.sh.dep_guard_cache import Cache
from shared.sh.dep_guard_network import Network


ROOT = Path(__file__).resolve().parents[2]
METADATA_URL = 'https://registry.npmjs.org/example'
STATS_URL = 'https://pypistats.org/api/packages/example/recent'


class Limits(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.home = Path(scratch.name)
        self.cache = Cache(self.home)
        self.trace = self.home / 'requests.jsonl'

    def network(self, transport=None):
        return Network(self.cache, transport, trace=self.trace)

    def rows(self):
        return [json.loads(row) for row in self.trace.read_text().splitlines()]

    def test_map_and_process_exit_on_permanently_blocked_transport(self):
        script = '''
import json, sys, threading
from pathlib import Path
from shared.sh.dep_guard_cache import Cache
from shared.sh.dep_guard_network import Network
home = Path(sys.argv[1])
cache = Cache(home)
def transport(*args):
    threading.Event().wait()
net = Network(cache, transport, trace=home/'requests.jsonl', seconds=.3)
def fail():
    raise ValueError('unavailable')
print(json.dumps(net.map({'ok': lambda: False, 'failure': fail,
                          'stuck': lambda: net.get_json('https://pypi.org/stuck')})))
cache.save(max_wait=net.remaining())
'''
        out = subprocess.run([sys.executable, '-B', '-c', script, str(self.home)], cwd=ROOT,
                             capture_output=True, text=True, check=True, timeout=4)
        self.assertEqual(json.loads(out.stdout), {'ok': False})
        self.assertEqual(self.rows(), [{'host': 'pypi.org', 'operation': 'metadata', 'bytes': 0}])
        refreshed = Cache(self.home)
        calls = []
        net = Network(refreshed, lambda *args: calls.append(args), trace=self.trace)
        self.assertEqual(net.get_json('https://pypi.org/stuck'), (0, None))
        self.assertEqual(calls, [])

    def test_one_host_cools_down_other_hosts_and_known_facts_remain_available(self):
        calls = []

        def transport(method, url, body, timeout, limit):
            calls.append(url)
            if url == STATS_URL:
                raise TimeoutError
            return 200, b'true'

        self.cache.put('npm:exists:cached', True, 3600)
        net = self.network(transport)
        self.assertEqual(net.get_json(STATS_URL), (0, None))
        self.assertEqual(net.get_json(STATS_URL), (0, None))
        self.assertTrue(self.cache.save())
        fresh = Cache(self.home)
        net = Network(fresh, transport, trace=self.trace)
        self.assertEqual(net.get_json(STATS_URL), (0, None))
        self.assertEqual(net.get_json(METADATA_URL), (200, True))
        self.assertEqual(net.cooldown_hosts, ('pypistats.org',))
        self.assertIs(fresh.get('npm:exists:cached'), True)
        self.assertEqual(calls, [STATS_URL, METADATA_URL])
        with patch('time.time', return_value=time.time() + 121):
            self.assertEqual(net.get_json(STATS_URL), (0, None))
        self.assertEqual(calls[-2:], [METADATA_URL, STATS_URL])

    def test_http_errors_bad_json_and_oversize_do_not_cool_down_host(self):
        responses = iter([(404, b''), (429, b'{}'), (503, b''), (200, b'bad'),
                          (200, b'true '), HTTPError(STATS_URL, 500, '', {}, None), (200, b'true')])

        def transport(*args):
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response

        net = self.network(transport)
        values = [net.get_json(STATS_URL, limit=4) for _ in range(7)]
        self.assertEqual(values, [(404, None), (429, {}), (503, None), (200, None),
                                  (200, None), (500, None), (200, True)])
        self.assertEqual([r['bytes'] for r in self.rows()], [0, 2, 0, 3, 5, 0, 4])

    def test_request_cap_shared_by_all_maps_and_direct_calls(self):
        net = self.network(lambda *args: (200, b'0'))
        lookup = lambda: net.get_json(METADATA_URL)
        result = net.map({i: lookup for i in range(28)})
        result.update(net.map({i: lookup for i in range(28, 52)}))
        self.assertEqual(sum(v == (200, 0) for v in result.values()), 40)
        self.assertEqual(sum(v == (0, None) for v in result.values()), 12)
        self.assertEqual(lookup(), (0, None))
        self.assertEqual(len(self.rows()), 40)

    def test_six_concurrent_on_wire_across_two_callers(self):
        condition = threading.Condition()
        release = threading.Event()
        active = peak = 0

        def transport(*args):
            nonlocal active, peak
            with condition:
                active += 1
                peak = max(peak, active)
                condition.notify_all()
            release.wait(4)
            with condition:
                active -= 1
            return 200, b'true'

        net = self.network(transport)
        tasks = {i: lambda: net.get_json(STATS_URL) for i in range(10)}
        with ThreadPoolExecutor(2) as callers:
            batches = [callers.submit(net.map, tasks) for _ in range(2)]
            with condition:
                try:
                    self.assertTrue(condition.wait_for(lambda: active >= 6, timeout=3))
                finally:
                    release.set()
            for batch in batches:
                self.assertEqual(list(batch.result().values()), [(200, True)] * 10)
        self.assertEqual(peak, 6)

    def test_aggregate_and_per_route_response_caps(self):
        allowances = []

        def transport(method, url, body, timeout, limit):
            allowances.append(limit)
            return 200, b'1' + b' ' * (limit - 1)

        net = self.network(transport)
        lookup = lambda: net.get_json(METADATA_URL, limit=16 * 1024 * 1024)
        self.assertEqual(net.get_json(STATS_URL, limit=16 * 1024 * 1024), (200, 1))
        values = net.map({i: lookup for i in range(12)}).values()
        self.assertEqual(sum(v == (200, 1) for v in values), 4)
        self.assertEqual(allowances, [256 * 1024] + [2 * 1024 * 1024] * 3 + [1792 * 1024])
        self.assertEqual(sum(r['bytes'] for r in self.rows()), 8 * 1024 * 1024)
        self.assertEqual(lookup(), (0, None))

    def test_trace_redacted_and_not_created_for_disallowed_routes(self):
        seen = []

        def transport(method, url, body, timeout, limit):
            seen.append((method, url, body))
            return 200, b'{}'

        net = self.network(transport)
        for url in ('https://private.invalid/p', 'file:///x', 'http://pypi.org/test',
                    'https://other@api.osv.dev/api', 'https://pypi.org:8443/test'):
            self.assertEqual(net.get_json(url), (0, None))
        self.assertEqual(seen, [])
        self.assertFalse(self.trace.exists())
        cases = [('GET', 'registry.npmjs.org/-/package/secret-name/dist-tags', 'exists'),
                 ('GET', 'registry.npmjs.org/secret-name', 'metadata'),
                 ('GET', 'api.npmjs.org/downloads/point/last-week/secret-name', 'downloads'),
                 ('GET', 'pypistats.org/api/packages/secret-name/recent', 'stats'),
                 ('HEAD', 'pypi.org/pypi/secret-name/json', 'exists'),
                 ('POST', 'api.osv.dev/v1/query', 'osv')]
        for method, target, kind in cases:
            url = 'https://' + target + '?api-key=secret-query'
            body = {'name': 'secret-body'} if method == 'POST' else None
            self.assertEqual(net.get_json(url, method=method, body=body),
                             (200, None if method == 'HEAD' else {}))
            self.assertEqual(seen[-1], (method, url, body))
            self.assertEqual(self.rows()[-1], {'host': target.split('/')[0], 'operation': kind, 'bytes': 2})
        self.assertEqual(len(self.rows()), len(cases))
        self.assertNotIn('secret', self.trace.read_text())
        self.assertEqual(self.trace.stat().st_mode & 0o777, 0o600)

    def wire(self, headers, body, expected_connections=1, **options):
        # Exercise the real HTTP reader over a socket pair; only TLS connect is
        # replaced. No request can leave this process or follow a real redirect.
        client, server = socket.socketpair()
        self.addCleanup(client.close)
        self.addCleanup(server.close)
        server.sendall(headers + b'\r\n\r\n' + body)
        server.shutdown(socket.SHUT_WR)

        class Connected(http.client.HTTPConnection):
            def connect(self):
                self.sock = client

        with patch('http.client.HTTPSConnection', side_effect=Connected) as opened:
            result = self.network().get_json(METADATA_URL, **options)
        self.assertEqual(opened.call_count, expected_connections)
        self.assertIn(b'/example HTTP/1.1', server.recv(1024))
        return result

    def test_wire_stream_stops_at_cap_even_without_a_content_length(self):
        self.assertEqual(self.wire(b'HTTP/1.0 200 OK', b'0' + b' ' * 1000, limit=17), (200, None))
        self.assertEqual(self.rows()[-1]['bytes'], 17)
        self.assertEqual(self.wire(b'HTTP/1.0 200 OK\r\nContent-Length: 4', b'true', limit=4), (200, True))
        self.assertEqual(self.wire(b'HTTP/1.0 200 OK\r\nContent-Length: 6', b'true', limit=6), (200, None))
        self.assertEqual(self.wire(b'HTTP/1.0 200 OK\r\nContent-Length: 5', b'true ', limit=4), (200, None))
        self.assertEqual(self.rows()[-1]['bytes'], 0)

    def test_wire_http_error_head_and_redirect(self):
        self.assertEqual(self.wire(b'HTTP/1.0 404 Not Found', b'{"found":false}'), (404, {'found': False}))
        self.assertEqual(self.wire(b'HTTP/1.0 200 OK\r\nContent-Length: 400', b'', method='HEAD'), (200, None))
        self.assertEqual(self.wire(b'HTTP/1.0 302 Found\r\nLocation: https://foreign.invalid/copy', b''),
                         (302, None))
        self.assertEqual(len(self.rows()), 3)

    def test_lock_contention_skips_write_but_preserves_pending_facts(self):
        self.cache.put('baseline', 1, 600)
        self.assertTrue(self.cache.save())
        lock = self.cache.path.with_suffix('.json.lock')
        self.cache.put('new', 2, 600)
        with lock.open() as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            started = time.monotonic()
            self.assertFalse(self.cache.save(max_wait=90))
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertIsNone(Cache(self.home).get('new'))
        self.assertEqual(self.cache.get('new'), 2)
        self.assertTrue(self.cache.save())
        self.assertEqual(Cache(self.home).get('new'), 2)

    def test_independent_writers_merge_recent_facts_and_evict_expired_facts(self):
        stale = Cache(self.home)
        stale.put('overlap', 1, 120)
        stale.put('short', [], 5)
        self.cache.put('overlap', 2, 120)
        self.cache.put('only-one-writer', True, 120)
        self.assertTrue(self.cache.save())
        self.assertTrue(stale.save())
        read = Cache(self.home)
        self.assertEqual(read.get('overlap'), 2)
        self.assertEqual(read.get('short'), [])
        self.assertIs(read.get('only-one-writer'), True)
        with patch('time.time', return_value=time.time() + 6):
            self.assertIsNone(read.get('short'))
            self.assertIsNone(Cache(self.home).get('short'))
            read.put('tick', {}, 100)
            self.assertTrue(read.save())
            self.assertNotIn('short', json.loads(read.path.read_bytes()))

    def test_threaded_put_save_and_mutable_values(self):
        def put(i):
            value = [i]
            self.cache.put(str(i), value, 3600)
            value.clear()
            self.cache.get(str(i)).append('local only')
            if i % 7 == 0:
                self.cache.save(max_wait=0)

        with ThreadPoolExecutor(6) as threads:
            list(threads.map(put, range(100)))
        self.assertTrue(self.cache.save())
        read = Cache(self.home)
        self.assertEqual([read.get(str(i)) for i in range(100)], [[i] for i in range(100)])

    def test_cache_keeps_newest_2000_entries_within_one_mib(self):
        for i in range(2100):
            self.cache.put(str(i), i, 3600)
        self.assertTrue(self.cache.save())
        self.assertEqual(set(json.loads(self.cache.path.read_bytes())), {str(i) for i in range(100, 2100)})
        for i in range(20):
            self.cache.put('big' + str(i), '!' * (64 * 1024), 3600)
        self.cache.put('never-fits', '!' * (1024 * 1024), 3600)
        self.assertTrue(self.cache.save())
        self.assertLessEqual(self.cache.path.stat().st_size, 1024 * 1024)
        self.assertEqual(self.cache.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(json.loads(self.cache.path.read_bytes())), {'big' + str(i) for i in range(5, 20)})

    def test_unreadable_or_malformed_cache_never_discards_an_in_memory_fact(self):
        self.cache.path.parent.mkdir(parents=True)
        self.cache.path.write_bytes(b'{')
        recovering = Cache(self.home)
        recovering.put('deny', {'exists': False}, 60)
        self.assertTrue(recovering.save())
        self.cache.path.unlink()
        self.cache.path.mkdir()
        recovering.put('deny', {'exists': False}, 60)
        self.assertFalse(recovering.save())
        self.assertEqual(recovering.get('deny'), {'exists': False})


if __name__ == '__main__':
    unittest.main()
