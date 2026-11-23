"""Progress at the hook boundary: partial evidence survives independent failures."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from dep_guard_cache import Cache
from dep_guard_commands import parse_commands
from dep_guard_lookup import fact_key
from dep_guard_verdict import Existence
from dep_install_guard import evaluate
from shared.tests.test_dep_install_guard import Registry


# The upper bound distinguishes a real early return from a stub that merely
# finished late. Stubs are released after every check and have a failsafe.
BUDGET = 0.8
LATEST_RETURN = 3.0
FAILSAFE = 5.0


class ProgressTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.cwd = self.root / 'project'
        self.cwd.mkdir()
        self.env = {'PATH': '/usr/bin', 'HOME': str(self.home)}
        self.api = Registry()
        self.addCleanup(lambda: self.assertEqual([], self.api.errors))

    def check(self, command, transport=None, seconds=5):
        return evaluate(command, self.cwd, env=self.env, home=self.home,
                        transport=transport or self.api, seconds=seconds)

    def independent(self, commands, transport=None, seconds=5):
        # Exercise refresh/override reconciliation for separately proven routes.
        # A shell install is *not* a route-neutral predecessor of a second one.
        invocations = [inv for cmd in commands for inv in parse_commands(cmd, self.cwd, env=self.env, home=self.home)]
        with patch('dep_guard_run.parse_commands', return_value=invocations):
            return self.check(commands[0], transport, seconds)

    def denial(self, result):
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get('permissionDecision'), 'deny', result)
        return result['permissionDecisionReason']

    def stalled(self, value):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def block(*args, **kwargs):
            entered.set()
            release.wait(FAILSAFE)
            return value

        return block, entered

    def bounded(self, command, transport=None):
        start = time.monotonic()
        result = self.check(command, transport, BUDGET)
        self.assertLess(time.monotonic() - start, LATEST_RETURN, 'hook waited for stalled work')
        return result

    def test_exact_cached_mal_denies_when_expired_existence_probe_returns_503(self):
        def version_specific(method, url, body, timeout, limit):
            status, raw = self.api(method, url, body, timeout, limit)
            if '/v1/vulns/' in url:
                payload = json.loads(raw)
                affected = payload['affected'][0]
                affected.pop('ranges')
                affected['versions'] = ['1.2.3']
                raw = json.dumps(payload).encode()
            return status, raw

        cmd = 'npm i mal@1.2.3'
        self.assertIn('MAL-2025-123', self.denial(self.check(cmd, version_specific)))
        cache = Cache(self.home, env=self.env)
        key = fact_key(('npm', 'mal'), 'exists')
        self.assertEqual(cache.get(key), Existence.FOUND.value)
        cache.put(key, Existence.FOUND.value, ttl_seconds=-1)
        self.assertTrue(cache.save())
        self.assertIsNone(Cache(self.home, env=self.env).get(key))
        self.api.rows['mal'] = {'status': 503}
        self.api.requests.clear()
        reason = self.denial(self.check('DEP_GUARD_ALLOW=mal ' + cmd, version_specific))
        self.assertIn('MAL-2025-123', reason)
        self.assertIn('1.2.3', reason)
        self.assertEqual(self.api.requests, [('GET', 'registry.npmjs.org', '/-/package/mal/dist-tags')])

    def test_plain_invocation_keeps_cached_404_when_override_refresh_fails(self):
        self.api.rows['missing'] = {'status': 404}
        self.assertIn('not found', self.denial(self.check('npm i missing')))
        self.api.rows['missing']['status'] = 503
        for cmd in (self.mixed('missing', True), self.mixed('missing', False)):
            with self.subTest(cmd=cmd):
                self.api.requests.clear()
                self.assertIn('not found', self.denial(self.independent(cmd)))
                self.assertEqual(self.api.requests, [('GET', 'registry.npmjs.org', '/-/package/missing/dist-tags')])
        fresh_only = self.check('DEP_GUARD_ALLOW=missing npm i missing')
        self.assertNotIn('permissionDecision', fresh_only)
        self.assertIn('unavailable', fresh_only['additionalContext'])

    def test_plain_invocation_keeps_cached_low_use_when_override_refresh_fails(self):
        self.api.rows['newpkg'] = {'count': 17, 'age': 4}
        self.assertIn('17 weekly downloads', self.denial(self.check('npm i newpkg')))
        self.api.rows['newpkg']['count'] = 'error'
        for cmd in (self.mixed('newpkg', True), self.mixed('newpkg', False)):
            with self.subTest(cmd=cmd):
                self.api.requests.clear()
                self.assertIn('17 weekly downloads', self.denial(self.independent(cmd)))
                self.assertEqual(self.api.requests, [('GET', 'api.npmjs.org', '/downloads/point/last-week/newpkg')])
        fresh_only = self.check('DEP_GUARD_ALLOW=newpkg npm i newpkg')
        self.assertNotIn('permissionDecision', fresh_only)
        self.assertIn('downloads unavailable', fresh_only['additionalContext'])

    @staticmethod
    def mixed(name, marked_first):
        plain = 'npm i ' + name
        marked = 'DEP_GUARD_ALLOW=' + name + ' ' + plain
        return (marked, plain) if marked_first else (plain, marked)

    def test_fresh_existence_replaces_cached_404_for_both_invocations(self):
        for marked_first in (True, False):
            name = 'published-' + str(int(marked_first))
            cmd = self.mixed(name, marked_first)
            with self.subTest(cmd=cmd):
                self.api.rows[name] = {'status': 404}
                probe = ('GET', 'registry.npmjs.org', '/-/package/' + name + '/dist-tags')
                self.api.requests.clear()
                self.assertIn('not found', self.denial(self.check('npm i ' + name)))
                self.assertIn('not found', self.denial(self.independent(cmd)))
                self.assertEqual(self.api.requests, [probe, probe], 'no telemetry after either 404')
                self.api.requests.clear()
                self.api.rows[name]['status'] = 200
                self.assertIsNone(self.independent(cmd))
                self.assertEqual(self.api.requests[0], probe)
                self.assertCountEqual([row[1] for row in self.api.requests],
                                      ['registry.npmjs.org', 'api.npmjs.org', 'api.osv.dev'])
                self.api.requests.clear()
                self.assertIsNone(self.check('npm i ' + name))
                self.assertEqual(self.api.requests, [])

    def test_fresh_download_count_supersedes_cached_low_count_for_both_invocations(self):
        for marked_first in (True, False):
            name = 'popular-' + str(int(marked_first))
            cmd = self.mixed(name, marked_first)
            with self.subTest(cmd=cmd):
                self.api.rows[name] = {'count': 17, 'age': 20}
                self.assertIn('17 weekly downloads', self.denial(self.check('npm i ' + name)))
                self.api.rows[name]['count'] = 100_000
                self.api.requests.clear()
                self.assertIsNone(self.independent(cmd))
                self.assertEqual(self.api.requests, [('GET', 'api.npmjs.org', '/downloads/point/last-week/' + name)])
                self.api.requests.clear()
                self.assertIsNone(self.check('npm i ' + name))
                self.assertEqual(self.api.requests, [])

    def test_confirmed_no_statistics_replaces_old_count_without_caching_zero(self):
        name = 'no-statistics'
        self.api.rows[name] = {'count': 17, 'age': 20}
        self.assertIn('17 weekly downloads', self.denial(self.check('npm i ' + name)))
        self.api.rows[name]['count'] = None
        self.api.requests.clear()
        result = self.independent(self.mixed(name, marked_first=False))
        self.assertNotIn('permissionDecision', result)
        self.assertIn('use unconfirmed', result['additionalContext'])
        probe = ('GET', 'api.npmjs.org', '/downloads/point/last-week/' + name)
        self.assertEqual(self.api.requests, [probe])
        self.api.rows[name]['count'] = 100_000
        self.api.requests.clear()
        self.assertIsNone(self.check('npm i ' + name))
        self.assertEqual(self.api.requests, [probe], 'next plain install must fetch the missing count')

    def test_stalled_pypi_existence_does_not_starve_npm_mal_lookup(self):
        block, entered = self.stalled((503, b''))
        def transport(*args):
            if urlsplit(args[1]).hostname == 'pypi.org':
                return block()
            return self.api(*args)

        start = time.monotonic()
        result = self.independent(('python3 -m pip install slow', 'npm i mal@1.2.3'), transport, BUDGET)
        self.assertLess(time.monotonic() - start, LATEST_RETURN)
        self.assertTrue(entered.is_set(), 'test must actually reach the stalled existence probe')
        self.assertIn('MAL-2025-123', self.denial(result))
        self.assertIn(('GET', 'api.osv.dev', '/v1/vulns/MAL-2025-123'), self.api.requests)

    def test_stalled_advisory_coordinator_preserves_completed_low_use_deny(self):
        self.api.rows['newpkg'] = {'count': 17, 'age': 4}
        block, entered = self.stalled({})
        with patch('dep_guard_run.lookup_advisories', side_effect=block):
            result = self.bounded('npm i newpkg')
        self.assertTrue(entered.is_set())
        self.assertIn('17 weekly downloads', self.denial(result))
        self.assertIn(('GET', 'registry.npmjs.org', '/newpkg'), self.api.requests)

    def test_source_discovery_and_cache_initialization_share_hook_deadline(self):
        for lookup, value in [('public_source', False), ('Cache', Cache(self.home, env=self.env))]:
            with self.subTest(lookup=lookup):
                block, entered = self.stalled(value)
                with patch('dep_guard_run.' + lookup, side_effect=block):
                    result = self.bounded('npm i package')
                self.assertTrue(entered.is_set())
                self.assertIsInstance(result, dict)
                self.assertNotIn('permissionDecision', result)
                self.assertTrue(result.get('additionalContext'))
                self.assertEqual([], self.api.requests)

    def test_xdg_cache_home_isolates_hook_cache_only_for_absolute_paths(self):
        self.api.rows['missing'] = {'status': 404}
        xdg = self.root / 'isolated-cache'
        key = fact_key(('npm', 'missing'), 'exists')
        self.env['XDG_CACHE_HOME'] = str(xdg)
        self.assertIn('not found', self.denial(self.check('npm i missing')))
        self.assertTrue((xdg / 'dep-install-guard/lookups.json').is_file())
        self.assertFalse((self.home / '.cache').exists())
        self.assertEqual(Cache(self.home, env=self.env).get(key), Existence.MISSING.value)
        ignored = self.root / 'relative-ignored'
        self.env['XDG_CACHE_HOME'] = os.path.relpath(ignored)
        self.assertIsNone(Cache(self.home, env=self.env).get(key))
        self.api.rows['missing'] = {'status': 200}
        self.assertIsNone(self.check('npm i missing'))
        self.assertFalse(ignored.exists())
        self.assertEqual(Cache(self.home, env={}).get(key), Existence.FOUND.value)
        self.assertEqual(Cache(self.home, env={'XDG_CACHE_HOME': str(xdg)}).get(key), Existence.MISSING.value)


if __name__ == '__main__':
    unittest.main()
