"""Behavior across the source, transport, policy and actual Bash hook boundaries."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import unquote, urlsplit

from dep_install_guard import evaluate
from dep_guard_verdict import DAY, Downloads, Existence, Metadata, Stat, verdict
from types import SimpleNamespace


SCRIPTS = Path(__file__).resolve().parents[1] / 'sh'


class Registry:
    """Allowlisted test endpoints; unknown operations are recorded as errors."""
    def __init__(self):
        self.rows = {}
        self.requests = []
        self.errors = []

    def __call__(self, method, url, body, timeout, limit):
        parts = urlsplit(url)
        path = unquote(parts.path)
        self.requests.append((method, parts.hostname, path))
        status, data = 200, {}
        if parts.hostname == 'registry.npmjs.org' and path.startswith('/-/package/'):
            name = path[len('/-/package/'):-len('/dist-tags')]
            status = self.rows.get(name, {}).get('status', 200)
            if status is None:
                raise TimeoutError
            data = {'latest': '1.0.0'}
        elif parts.hostname in ('pypistats.org', 'api.npmjs.org'):
            names = (path.split('/')[-2] if parts.hostname == 'pypistats.org' else path.split('/')[-1]).split(',')
            rows = {name: self.rows.get(name, {}).get('count', 100_000) for name in names}
            if len(names) > 1:
                data = {k: {'downloads': v} if v is not None else None for k, v in rows.items()}
            else:
                value = rows[names[0]]
                status = 404 if value is None else 503 if value == 'error' else 200
                data = {'downloads': value, 'data': {'last_week': value}}
        elif parts.hostname == 'api.osv.dev' and path == '/v1/querybatch':
            query = json.loads(body) if not isinstance(body, dict) else body
            data = {'results': [({'vulns': [{'id': 'MAL-2025-123'}]} if q['package']['name'] == 'mal' else {})
                                for q in query['queries']]}
        elif parts.hostname == 'api.osv.dev' and path == '/v1/vulns/MAL-2025-123':
            data = {'id': 'MAL-2025-123', 'affected': [{'package': {'ecosystem': 'npm', 'name': 'mal'},
                    'ranges': [{'type': 'SEMVER', 'events': [{'introduced': '0'}]}]}]}
        elif parts.hostname in ('pypi.org', 'registry.npmjs.org'):
            name = path.split('/')[2] if parts.hostname == 'pypi.org' else path[1:]
            row = self.rows.get(name, {})
            stamp = (datetime.now(timezone.utc) - timedelta(days=row.get('age', 100))).isoformat()
            data = {'time': {'created': stamp}, 'versions': row.get('versions', {'a': {}, 'b': {}, 'c': {}}),
                    'maintainers': [{}], 'releases': {'1': [{'upload_time_iso_8601': stamp}]}}
        else:
            self.errors.append((method, parts.hostname, path))
            return 500, b''
        return status, b'' if method == 'HEAD' else json.dumps(data).encode()


class InstallGuardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.cwd = self.home / 'project'
        self.cwd.mkdir()
        self.api = Registry()
        self.addCleanup(lambda: self.assertEqual([], self.api.errors))

    def hook(self, command):
        return evaluate(command, self.cwd, env={'PATH': '/usr/bin'}, home=self.home, transport=self.api)

    def denied(self, result):
        self.assertEqual('deny', result['permissionDecision'])
        return result['permissionDecisionReason']

    def test_no_lookup_noninstall_bare_and_kill_switch(self):
        for command in ['git status', 'npm install', 'npm ci', 'uv sync', 'poetry install', 'echo npm install fake']:
            self.assertIsNone(self.hook(command), command)
        self.assertIsNone(evaluate('npx foo', self.cwd, env={'DEP_INSTALL_GUARD': 'off'}, home=self.home, transport=self.api))
        self.assertEqual([], self.api.requests)

    def test_popular_no_metadata_warm_zero_calls(self):
        self.assertIsNone(self.hook('npm install popular'))
        self.assertEqual(3, len(self.api.requests))
        self.assertEqual('/-/package/popular/dist-tags', self.api.requests[0][2])
        self.api.requests.clear()
        self.assertIsNone(self.hook('npm install popular'))
        self.assertEqual([], self.api.requests)

    def test_missing_never_sent_elsewhere_even_when_overridden(self):
        self.api.rows['unknown'] = {'status': 404}
        self.assertIn('private package', self.denied(self.hook('npm install unknown')))
        self.assertEqual(1, len(self.api.requests))
        self.assertIn('override', self.hook('DEP_GUARD_ALLOW=unknown npx unknown')['additionalContext'])
        self.assertEqual([self.api.requests[0]] * 2, self.api.requests)
        self.assertIn('not found', self.denied(self.hook('DEP_INSTALL_GUARD=off npm i unknown')))

    def test_one_command_override_is_not_another_commands_permission(self):
        self.api.rows['unknown'] = {'status': 404}
        reason = self.denied(self.hook('DEP_GUARD_ALLOW=unknown npm i unknown; npm i unknown'))
        self.assertIn('not found', reason)
        self.assertIn('DEP_GUARD_ALLOW=unknown', reason)

    def test_missing_and_mal_denies_survive_unavailable_and_override(self):
        self.api.rows.update(unknown={'status': 404}, offline={'status': 503})
        reason = self.denied(self.hook('DEP_GUARD_ALLOW=mal npm i mal unknown offline'))
        self.assertIn('MAL-2025-123', reason)
        self.assertIn('unknown: not found', reason)
        self.assertIn('offline: public registry existence check unavailable', reason)

    def test_young_low_override_refreshes_downloads(self):
        self.api.rows['newpkg'] = {'age': 6, 'count': 99, 'versions': {'1': {}}}
        reason = self.denied(self.hook('npm i newpkg'))
        self.assertIn('99 weekly downloads', reason)
        self.assertIn('one maintainer', reason)
        self.api.requests.clear()
        warning = self.hook('DEP_GUARD_ALLOW=newpkg npm i newpkg')['additionalContext']
        self.assertIn('override', warning)
        self.assertEqual(['api.npmjs.org'], [host for _, host, _ in self.api.requests])

    def test_recent_stats_states(self):
        for name, age, value, deny, text in [('first', 13, None, True, 'no weekly'), ('young', 14, None, False, 'use unconfirmed'),
                                             ('error', 89, 'error', False, 'downloads unavailable'),
                                             ('established', 90, 15, False, None), ('busy', 5, 1000, False, 'new package')]:
            self.api.rows[name] = {'age': age, 'count': value}
            result = self.hook('npm install ' + name)
            if text is None:
                self.assertIsNone(result)
            elif deny:
                self.assertIn(text, self.denied(result))
            else:
                self.assertIn(text, result['additionalContext'])

    def test_pypi_earliest_upload_and_only_matching_host_cools_down(self):
        self.api.rows['tiny-project'] = {'age': 10, 'count': 400}
        reason = self.denied(self.hook('pip install Tiny_Project'))
        self.assertIn('tiny-project: new package', reason)
        self.assertEqual(('HEAD', 'pypi.org', '/pypi/tiny-project/json'), self.api.requests[0])
        def timed_out_stats(*args):
            if urlsplit(args[1]).hostname == 'pypistats.org':
                raise TimeoutError
            return self.api(*args)
        def check(cmd):
            return evaluate(cmd, self.cwd, env={'PATH': '/usr/bin'}, home=self.home, transport=timed_out_stats)
        self.assertIn('downloads unavailable', check('pip install another')['additionalContext'])
        self.assertIsNone(check('npm install npm-public'))

    def test_unavailable_existence_gate_and_local_bin_make_no_telemetry_calls(self):
        self.api.rows['offline'] = {'status': None}
        self.assertIn('offline', self.hook('npm i offline')['additionalContext'])
        self.assertEqual(1, len(self.api.requests))
        self.assertIn('unavailable', self.hook('npm i offline-again')['additionalContext'])
        self.assertEqual(1, len(self.api.requests))
        bindir = self.cwd / 'node_modules/.bin'
        bindir.mkdir(parents=True)
        (bindir / 'known-bin').touch()
        (self.cwd / '.npmrc').write_text('registry=https://internal.invalid/\n')
        self.assertIsNone(self.hook('npx known-bin'))
        self.assertEqual(1, len(self.api.requests))

    def test_cutoff_on_exact_age_not_calendar_date(self):
        inv = SimpleNamespace(pin=None, override=False)
        for day, stat, expected in [(13, Downloads(Stat.NO_ROW), True), (14, Downloads(Stat.NO_ROW), False),
                                    (89, Downloads(Stat.MEASURED, 999), True), (90, Downloads(Stat.MEASURED, 999), False)]:
            deny, _ = verdict(inv, Existence.FOUND, stat, Metadata(0), None, day * DAY)
            self.assertEqual(expected, bool(deny))

    def test_missing_cache_storage_does_not_cancel_deny(self):
        (self.home / '.cache').write_text('not a directory')
        self.api.rows['absent'] = {'status': 404}
        self.assertIn('not found', self.denied(self.hook('npm i absent')))

    def test_names_over_limit_never_sent(self):
        names = ['pkg%d' % n for n in range(21)]
        result = self.hook('npm i ' + ' '.join(names))
        self.assertIn('too many', result['additionalContext'])
        self.assertFalse(any('pkg20' in path for _, _, path in self.api.requests))

    def test_hook_returns_with_deny_even_if_another_probe_never_returns(self):
        self.api.rows['absent'] = {'status': 404}
        called = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        def blocked(*args):
            if '/pending/' in args[1]:
                called.set()
                release.wait()
                return 503, b''
            return self.api(*args)
        start = time.monotonic()
        result = evaluate('npm i absent pending', self.cwd, env={}, home=self.home, transport=blocked)
        self.assertTrue(called.is_set())
        self.assertLess(time.monotonic() - start, 6.5)
        self.assertIn('absent: not found', self.denied(result))

    def test_flat_hook_stdin_stdout_without_repository_imports(self):
        hookdir = self.home / 'flat'
        hookdir.mkdir()
        cohort = [SCRIPTS / 'dep_install_guard.py', SCRIPTS / 'destructive_guard_shell.py', *SCRIPTS.glob('dep_guard_*.py')]
        for script in cohort:
            shutil.copyfile(script, hookdir / script.name)
        def invoke(command):
            payload = {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash', 'cwd': str(self.cwd), 'tool_input': {'command': command}}
            return subprocess.run([sys.executable, str(hookdir / 'dep_install_guard.py')], cwd=self.cwd,
                                  env={'HOME': str(self.home), 'PATH': '/usr/bin'}, input=json.dumps(payload),
                                  capture_output=True, text=True, check=True).stdout
        self.assertEqual('', invoke('npm install'))
        self.assertIn('not checked', json.loads(invoke('npm install --offline nonpublic'))['hookSpecificOutput']['additionalContext'])
        (hookdir / 'destructive_guard_shell.py').unlink()
        self.assertIn('broken', json.loads(invoke('npx sample'))['hookSpecificOutput']['additionalContext'])
