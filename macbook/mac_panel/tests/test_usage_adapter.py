import copy
import json
import unittest

from panel_contracts import ApiError, Section
from panel_routes import PanelRoutes
from panel_test_support import FakeRunner, auth, json_command, request, running_server
from sampler import samples
from usage_adapter import COMMAND, UsageAdapter, WINDOWS


def facts():
    return dict({key: dict(used=20, left=80, resets_at='2026-11-10T12:00:00+00:00') for key in WINDOWS},
        fetched_at='2026-11-05T12:00:00+00:00', age_minutes=3,
        credits=dict(left=1000, allowance_per_day=30, burn_per_day=None, expires='2026-12-31', days_left=56),
        claude_position=dict(left=80, elapsed_pct=50, line_left=50, ahead=-30, resets_in_h=84),
        lean='gpt-heavy', reasons=['fixture'], route=dict(workers='fixture workers', review='fixture review'),
        availability=dict(claude='ok', gpt='constrained'))


class UsageTests(unittest.TestCase):
    def test_read_validates_and_strips_unknown_identity_fields(self):
        value = facts()
        value['account'] = 'private'
        value['credits']['identity'] = 'private'
        runner = FakeRunner([json_command(value)])
        result = UsageAdapter(runner).read()
        self.assertEqual(result, facts())
        self.assertEqual(runner.calls, [(COMMAND, 20)])

    def test_bad_wire_values_fail_closed(self):
        for key, bad in [('age_minutes', True), ('age_minutes', float('nan')),
                         ('age_minutes', -1), ('fetched_at', 'not a timestamp'),
                         ('availability', dict(claude='fine', gpt='out')), ('credits', []),
                         ('claude_position', dict(left='bad')), ('reasons', [42])]:
            value = facts()
            value[key] = bad
            with self.subTest(key=key), self.assertRaises(ApiError):
                UsageAdapter(FakeRunner([json_command(value)])).read()

    def test_null_windows_and_position(self):
        value = facts()
        value.update(claude_weekly=None, claude_position=None)
        self.assertEqual(UsageAdapter(FakeRunner([json_command(value)])).read(), value)

    def test_usage_http_is_guarded_and_returns_facts_unchanged(self):
        routes = PanelRoutes(runner=FakeRunner([json_command(facts())]))
        self.addCleanup(routes.close)
        with running_server(routes) as server:
            self.assertEqual(request(server, '/api/usage', headers={'Host': auth(server)['Host']})[0], 403)
            code, _, raw = request(server, '/api/usage', headers=auth(server))
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(raw), facts())

    def test_sampling_all_usage_keys(self):
        value = {r['key']: r for r in samples(Section.USAGE, facts(), 10)}
        self.assertEqual(set(value), {key + '_left' for key in WINDOWS} |
                         {'credits', 'claude_line_left', 'lean'})
        self.assertEqual(value['codex_weekly_left']['level'], 'warn')
        self.assertEqual(value['claude_weekly_left']['value'], 80)
        self.assertEqual(value['lean']['text'], 'gpt-heavy')
