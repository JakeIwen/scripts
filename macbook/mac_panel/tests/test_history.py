from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from history_store import HistoryStore
from panel_contracts import ApiError, Section
from panel_routes import PanelRoutes
from panel_test_support import auth, request, running_server
from sampler import Sampler, samples


def sample(ts, key='a', section='services'):
    return dict(section=section, key=key, ts=ts, level='ok', value=ts, text='running')


class HistoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = HistoryStore(Path(directory.name) / 'state')

    def test_persistence_permissions_wal_and_filtering(self):
        self.store.record([sample(10), sample(20, 'b'), sample(30), sample(20, section='metered')])
        self.assertEqual(self.store.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        with closing(sqlite3.connect(self.store.path)) as connection:
            self.assertEqual(connection.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            self.assertEqual([r[1] for r in connection.execute('PRAGMA table_info(samples)')],
                             ['section', 'key', 'ts', 'level', 'value', 'text'])
        value = HistoryStore(self.store.directory).series('services', 10, 25)
        self.assertEqual(set(value['series']), {'a', 'b'})
        self.assertEqual(value['series']['a'], [dict(ts=10.0, level='ok', value=10.0, text='running')])
        self.assertEqual(self.store.series('services', 0, 50, 'b')['series']['b'][0]['ts'], 20)
        self.assertEqual(self.store.series('absent', 0, 50), {'series': {}, 'spacing': {}})

    def test_even_per_series_thinning_and_pruning(self):
        self.store.record([sample(i, str(i % 3)) for i in range(10001)])
        value = self.store.series('services', 0, 10000, limit=1000)
        self.assertEqual(value['spacing'], {'0': 9, '1': 9, '2': 9})
        for key, rows in value['series'].items():
            self.assertEqual(len(rows), 1000)
            self.assertEqual(rows[0]['ts'], int(key))
            self.assertLessEqual(max(b['ts'] - a['ts'] for a, b in zip(rows, rows[1:])), 12)
        last = self.store.series('services', 0, 10000, limit=1)
        self.assertEqual({key: rows[0]['ts'] for key, rows in last['series'].items()},
                         {'0': 9999, '1': 10000, '2': 9998})
        self.assertEqual(last['spacing'], {'0': 0, '1': 0, '2': 0})
        with patch('history_store.time.time', return_value=30 * 86400 + 500):
            self.store.prune()
        left = self.store.series('services', 0, 1000)
        self.assertEqual(min(p['ts'] for rows in left['series'].values() for p in rows), 500)

    def chart_coverage(self, payload, duration):
        # Exercise the actual drawn geometry with SQLite's retained series and reported spacing.
        module = (Path(__file__).parents[1] / 'static/charts.mjs').as_uri()
        script = """
import {readFileSync} from 'node:fs';
const {stateBands, stateGap} = await import(process.argv[1]);
const {payload, duration} = JSON.parse(readFileSync(0, 'utf8'));
const coverage = Object.fromEntries(Object.entries(payload.series).map(([key, samples]) => {
  const bands = stateBands(samples, {from: 0, to: duration, width: 320, maxGap: stateGap(payload.spacing[key])});
  return [key, bands.filter(band => band.level !== 'nodata').reduce((sum, band) => sum + band.end - band.start, 0) / duration];
}));
console.log(JSON.stringify(coverage));
"""
        result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script, module],
            input=json.dumps(dict(payload=payload, duration=duration)), text=True,
            capture_output=True, check=True, timeout=30)
        return json.loads(result.stdout)

    def test_twenty_continuous_services_keep_over_95_percent_seven_day_coverage(self):
        duration = 7 * 86400
        self.store.record(sample(ts, str(service)) for ts in range(0, duration + 1, 60)
                          for service in range(20))
        payload = self.store.series('services', 0, duration)
        self.assertEqual(len(payload['series']), 20)
        self.assertTrue(all(len(rows) == 5000 for rows in payload['series'].values()))
        self.assertEqual(set(payload['spacing'].values()), {120})
        self.assertTrue(all(value > .95 for value in self.chart_coverage(payload, duration).values()))

    def test_thinning_still_draws_a_real_panel_outage_as_a_gap(self):
        duration, gap_start, gap_end = 7 * 86400, 3 * 86400, 4 * 86400
        self.store.record(sample(ts) for ts in range(0, duration + 1, 60)
                          if not gap_start <= ts < gap_end)
        payload = self.store.series('services', 0, duration)
        coverage = self.chart_coverage(payload, duration)['a']
        self.assertLess(coverage, .87)
        self.assertGreater(coverage, .85)

    def test_sparse_unthinned_samples_do_not_redefine_an_outage_as_cadence(self):
        self.store.record([sample(0), sample(86400)])
        payload = self.store.series('services', 0, 86460)
        self.assertEqual(payload['spacing'], {})
        self.assertLess(self.chart_coverage(payload, 86460)['a'], .01)

    def test_environment_override(self):
        with patch.dict(os.environ, {'MAC_PANEL_STATE_DIR': str(self.store.directory)}):
            self.assertEqual(HistoryStore().path, self.store.path)

    def test_history_http_query_and_token(self):
        self.store.record([sample(10, 'com.example')])
        routes = PanelRoutes(history=self.store)
        self.addCleanup(routes.close)
        with running_server(routes) as server:
            path = '/api/history?section=services&since=0&until=20&key=com.example'
            self.assertEqual(request(server, path, headers={'Host': auth(server)['Host']})[0], 403)
            code, _, raw = request(server, path, headers=auth(server))
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(raw), self.store.series('services', 0, 20, 'com.example'))
            for query in ('', 'section=services&since=nan&until=20',
                          'section=services&since=20&until=0', 'section=nope&since=0&until=20',
                          'section=services&since=0&until=20&key=a&key=b',
                          'section=services&since=0&until=20&limit=2'):
                self.assertEqual(request(server, '/api/history?' + query, headers=auth(server))[0], 400)


class SamplingTests(unittest.TestCase):
    def test_services_levels_and_values(self):
        rows = []
        for i, (loaded, disabled, pid, last_exit) in enumerate([
                (True, False, 123, 0), (True, False, None, 1),
                (False, True, None, None), (False, False, None, None), (None, False, None, None)]):
            rows.append(dict(label=str(i), loaded=loaded, disabled=disabled, pid=pid,
                             last_exit=last_exit, state='running'))
        result = samples(Section.SERVICES, {'services': rows}, 12)
        self.assertEqual([r['level'] for r in result], ['ok', 'warn', 'idle', 'warn', 'unknown'])
        self.assertEqual(result[0]['value'], 123)
        self.assertEqual(result[1]['text'], 'running; last exit 1')
        self.assertTrue(all(r['ts'] == 12 for r in result))

    def test_metered_stale_and_fresh(self):
        consumer = dict(actual='held', permitted=False)
        payload = dict(fresh=True, state=dict(verdict=dict(metered=True,
            matched=[dict(ssid='Fixture')]), consumers={'mac.test': consumer}),
            pi_state=dict(evaluated_at=1, valid_until=20, consumers={'pi.test': consumer}),
            low_data_mode=dict(enabled=True, error=None))
        result = {r['key']: r for r in samples(Section.METERED, payload, 10)}
        self.assertEqual(result['verdict']['value'], 1)
        self.assertEqual(result['ssid']['text'], 'Fixture')
        self.assertEqual(result['consumer:pi.test']['text'], 'held')
        payload['fresh'] = False
        result = {r['key']: r for r in samples(Section.METERED, payload, 30)}
        self.assertIsNone(result['verdict']['value'])
        self.assertIsNone(result['ssid']['text'])
        self.assertEqual(result['consumer:mac.test']['level'], 'unknown')
        self.assertEqual(result['consumer:pi.test']['text'], 'unknown')

    def test_jobs_and_restore(self):
        result = samples(Section.CRONTAB, {'jobs': [dict(id='job', paused=True,
                         log_modified=123, command='fixture', expression='* * * * *')]}, 10)
        self.assertEqual(result[0]['value'], 123)
        self.assertEqual(result[0]['level'], 'idle')
        result = samples(Section.DEFERRED, {'jobs': [dict(id='job', valid=True,
                         due='2026-11-06T12:00', state='failed'), dict(id='broken', valid=False)]}, 10)
        self.assertGreater(result[0]['value'], 0)
        self.assertEqual(result[1]['text'], 'invalid')
        result = samples(Section.RESTORE, dict(sessions_open=3, last_snapshot=123), 10)
        self.assertEqual([(r['key'], r['value']) for r in result], [('sessions', 3), ('snapshot', 123)])

    def test_cron_history_persists_no_argument_string(self):
        from crontab_adapter import display_job
        from test_crontab_adapter import status_payload
        job = status_payload()['jobs'][0]
        job['command'] = '/usr/bin/curl https://x/tok3n --header "secret: tok3n"'
        rows = samples(Section.CRONTAB, {'jobs': [display_job(job)]}, 10)
        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(directory)
            store.record(rows)
            with closing(sqlite3.connect(store.path)) as connection:
                stored = connection.execute('SELECT key, text FROM samples').fetchall()
        self.assertNotIn('tok3n', json.dumps(stored))
        self.assertNotIn('https://x', json.dumps(stored))
        self.assertEqual(stored, [(job['id'], '0 3 * * * · curl')])
        for command in ('bash -c "echo tok3n"', 'env SECRET=tok3n curl https://x/tok3n',
                        '"unterminated tok3n', '> https://x/tok3n'):
            job['command'] = command
            self.assertNotIn('tok3n', json.dumps(samples(Section.CRONTAB, {'jobs': [display_job(job)]}, 10)))

    def test_thread_stops_promptly_and_logs_failures(self):
        called = threading.Event()
        class Adapter:
            def read(self):
                raise RuntimeError('fixture failure')
        class Store:
            def prune(self):
                called.set()
        sampler = Sampler({Section.SERVICES: Adapter()}, Store(), {Section.SERVICES: 0.01})
        with self.assertLogs('sampler', level='ERROR') as logs:
            sampler.start()
            self.assertTrue(called.wait(1))
            sampler.stop()
        self.assertFalse(sampler.thread.is_alive())
        self.assertIn('fixture failure', '\n'.join(logs.output))

    def test_thread_records_with_supplied_clock(self):
        called = threading.Event()
        recorded = []
        class Adapter:
            def read(self):
                return dict(sessions_open=2, last_snapshot=42)
        class Store:
            def record(self, rows):
                recorded.extend(rows)
                called.set()
            def prune(self):
                pass
        sampler = Sampler({Section.RESTORE: Adapter()}, Store(), {Section.RESTORE: 60},
                          wall_clock=lambda: 123)
        sampler.start()
        try:
            self.assertTrue(called.wait(1))
        finally:
            sampler.stop()
        self.assertEqual(len(recorded), 2)
        self.assertEqual(recorded[0]['ts'], 123)
