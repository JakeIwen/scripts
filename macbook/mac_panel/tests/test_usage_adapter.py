import copy
import json
import unittest

from panel_contracts import ApiError, Section
from panel_routes import PanelRoutes
from panel_test_support import FakeRunner, auth, json_command, request, running_server
from sampler import samples
from cli_runner import CommandResult
from usage_adapter import COMMAND, MODE_COMMAND, MODES, UsageAdapter, WINDOWS


def facts():
    return dict({key: dict(used=20, left=80, resets_at='2026-11-10T12:00:00+00:00') for key in WINDOWS},
        fetched_at='2026-11-05T12:00:00+00:00', age_minutes=3,
        credits=dict(left=1000, allowance_per_day=30, expires='2026-12-31', days_left=56),
        claude_position=dict(left=80, elapsed_pct=50, line_left=50, ahead=-30, resets_in_h=84),
        lean='gpt-heavy', reasons=['fixture'], route=dict(workers='fixture workers', review='fixture review'),
        availability=dict(claude='ok', gpt='constrained'))


LIVE = json.loads('''{"claude_weekly":{"used":12.0,"left":88.0,"resets_at":"2026-11-07T18:00:00+00:00"},"claude_session":{"used":19.0,"left":81.0,"resets_at":"2026-11-06T02:10:00+00:00"},"fable_weekly":{"used":17.0,"left":83.0,"resets_at":"2026-11-07T18:00:00+00:00"},"codex_weekly":{"used":9.0,"left":91.0,"resets_at":"2026-11-12T06:46:32+00:00"},"codex_session":null,"credits":{"left":10852.243326,"expires":"2026-12-31","days_left":56.199607994155095},"mode":"balanced-burn","positions":{"claude":{"left":88.0,"line":28.066265568824406,"position":59.933734431175594,"state":"surplus","reset":"2026-11-07T18:00:00+00:00"},"gpt":{"left":91.0,"line":89.02763404348546,"position":1.9723659565145368,"state":"surplus","reset":"2026-11-12T06:46:32+00:00","tolerance":28,"credits":10852.243326,"on_credits":false}},"route":{"builders":"claude first (further ahead of its plan; table Claude column); gpt when Claude has no room","review":"claude parents by astra (room now); gpt parents by opus (room now)","parents":"either (opus or astra)"},"availability":{"claude":"ok","gpt":"ok"},"fetched_at":"2026-11-05T12:00:00+00:00","age_minutes":3.6251888833333337}''')

ANNOUNCED = dict(
    positions=LIVE['positions'], mode='balanced-burn',
    generated_at='2026-01-05T12:00:00+00:00', builders='claude first', review='parent-based',
    parents='either', lines=['Claude 88% left', 'Codex 91% left'], mode_source='auto',
    expires_at='2026-12-31', knobs={'x': 1}, settings_source='private',
    availability=dict(claude=dict(state='ok', window_left=81.0, reset='2026-11-06T02:10:00+00:00'),
                      gpt=dict(state='constrained', window_left=None, reset='2026-11-12T06:46:32+00:00')))


class UsageTests(unittest.TestCase):
    def test_read_validates_and_strips_unknown_identity_fields(self):
        value = facts()
        value['account'] = 'private'
        value['credits']['identity'] = 'private'
        runner = FakeRunner([json_command(value)])
        result = UsageAdapter(runner).read()
        self.assertEqual(result, facts())
        self.assertEqual(runner.calls, [(COMMAND, 20)])

    def test_identity_keys_are_stripped_recursively(self):
        value = copy.deepcopy(LIVE)
        private = dict(email='private', account_id='private', org='private')
        for obj in (value, value['route'], value['positions']['claude']):
            obj.update(private)
        result = UsageAdapter(FakeRunner([json_command(value)])).read()

        def check(obj):
            if isinstance(obj, dict):
                self.assertTrue(private.keys().isdisjoint(obj))
                for child in obj.values():
                    check(child)
            elif isinstance(obj, list):
                for child in obj:
                    check(child)

        check(result)

    def test_invalid_availability_returns_502(self):
        for bad in ([], {'state': []}, {'state': {}}):
            value = copy.deepcopy(LIVE)
            value['availability']['claude'] = bad
            routes = PanelRoutes(runner=FakeRunner([json_command(value)]))
            self.addCleanup(routes.close)
            with self.subTest(bad=bad), running_server(routes) as server:
                code, _, _ = request(server, '/api/usage', headers=auth(server))
                self.assertEqual(code, 502)

    def test_nonfinite_availability_detail_returns_502(self):
        value = copy.deepcopy(LIVE)
        value['codex_session'] = None
        value['availability']['gpt'] = dict(state='ok', window_left=float('inf'))
        routes = PanelRoutes(runner=FakeRunner([json_command(value)]))
        self.addCleanup(routes.close)
        with running_server(routes) as server:
            code, _, _ = request(server, '/api/usage', headers=auth(server))
        self.assertEqual(code, 502)

    def test_bad_wire_values_fail_closed(self):
        for key, bad in [('age_minutes', True), ('age_minutes', float('nan')),
                         ('age_minutes', -1), ('fetched_at', 'not a timestamp'),
                         ('availability', dict(claude='fine', gpt='out')), ('credits', []),
                         ('claude_position', dict(left='bad')), ('reasons', [42])]:
            value = facts()
            value[key] = bad
            with self.subTest(key=key), self.assertRaises(ApiError):
                UsageAdapter(FakeRunner([json_command(value)])).read()

    def test_live_shape_decodes_with_derived_position(self):
        result = UsageAdapter(FakeRunner([json_command(LIVE)])).read()
        self.assertEqual(result['lean'], 'balanced-burn')
        self.assertEqual(result['claude_position']['line_left'], LIVE['positions']['claude']['line'])
        self.assertEqual(result['claude_position']['ahead'], LIVE['positions']['claude']['position'])
        self.assertIn('resets_in_h', result['claude_position'])
        self.assertNotIn('elapsed_pct', result['claude_position'])
        self.assertIs(result['gpt_position']['on_credits'], False)
        self.assertEqual(result['route'], LIVE['route'])
        self.assertEqual(result['reasons'], [])
        self.assertEqual(result['age_minutes'], LIVE['age_minutes'])

    def test_announced_shape_decodes_and_ignores_knobs(self):
        result = UsageAdapter(FakeRunner([json_command(ANNOUNCED)])).read()
        self.assertEqual(result['fetched_at'], ANNOUNCED['generated_at'])
        self.assertGreater(result['age_minutes'], 0)
        self.assertEqual(result['availability'], dict(claude='ok', gpt='constrained'))
        self.assertEqual(result['availability_detail']['claude']['window_left'], 81.0)
        # Windows and credits are no longer sent: rebuilt from positions and availability, else absent.
        self.assertEqual(result['claude_weekly']['left'], 88.0)
        self.assertEqual(result['claude_session'], dict(used=19.0, left=81.0, resets_at='2026-11-06T02:10:00+00:00'))
        self.assertEqual(result['codex_weekly']['left'], 91.0)
        self.assertIsNone(result['codex_session'])
        self.assertIsNone(result['fable_weekly'])
        self.assertEqual(result['credits'], dict(left=10852.243326))
        self.assertEqual(result['route'], dict(builders='claude first', review='parent-based', parents='either'))
        self.assertEqual((result['lines'], result['mode_source'], result['expires_at']),
                         (ANNOUNCED['lines'], 'auto', '2026-12-31'))
        self.assertEqual(result['lean'], 'balanced-burn')
        self.assertNotIn('knobs', result)
        self.assertNotIn('settings_source', result)

    def test_missing_only_optional_fields_still_answers_200(self):
        for shape in (LIVE, ANNOUNCED):
            routes = PanelRoutes(runner=FakeRunner([json_command(shape)]))
            self.addCleanup(routes.close)
            with running_server(routes) as server:
                code, _, raw = request(server, '/api/usage', headers=auth(server))
            self.assertEqual(code, 200)
            self.assertIn('availability', json.loads(raw))

    def test_missing_required_fields_fail(self):
        for key in ('availability',):
            value = copy.deepcopy(LIVE)
            del value[key]
            with self.subTest(key=key), self.assertRaises(ApiError):
                UsageAdapter(FakeRunner([json_command(value)])).read()
        value = copy.deepcopy(LIVE)
        del value['fetched_at']
        with self.assertRaises(ApiError):
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


class ModeTests(unittest.TestCase):
    REPORT = 'mode: gpt-conserve (manual; expires Thu Nov 12 00:46)'

    def adapter(self, *results):
        runner = FakeRunner(list(results))
        return UsageAdapter(runner), runner

    def test_every_mode_name_is_accepted_and_returns_fresh_facts(self):
        self.assertEqual(len(MODES), 7)
        for mode in MODES:
            adapter, runner = self.adapter(CommandResult(0, self.REPORT + '\n'), json_command(LIVE))
            with self.subTest(mode=mode):
                result = adapter.mutate({'action': 'mode', 'mode': mode, 'confirmed': True})
                self.assertEqual(runner.calls, [(MODE_COMMAND + (mode,), 20), (COMMAND, 20)])
                self.assertEqual(result['message'], self.REPORT)
                self.assertEqual(result['lean'], 'balanced-burn')

    def test_bad_name_and_bad_shape_are_refused_before_any_cli_call(self):
        for body in ({'action': 'mode', 'mode': 'turbo', 'confirmed': True},
                     {'action': 'mode', 'mode': 'gpt-burn; rm -rf', 'confirmed': True},
                     {'action': 'mode', 'mode': 7, 'confirmed': True},
                     {'action': 'other', 'mode': 'gpt-burn', 'confirmed': True},
                     {'action': 'mode', 'mode': 'gpt-burn', 'confirmed': True, 'hours': 4},
                     {'action': 'mode'}):
            adapter, runner = self.adapter()
            with self.subTest(body=body), self.assertRaises(ApiError) as caught:
                adapter.mutate(body)
            self.assertEqual(caught.exception.status, 400)
            self.assertEqual(runner.calls, [])

    def test_missing_confirmation_is_a_400_without_a_cli_call(self):
        for body in ({'action': 'mode', 'mode': 'gpt-burn'}, {'action': 'mode', 'mode': 'gpt-burn', 'confirmed': 'yes'}):
            adapter, runner = self.adapter()
            with self.subTest(body=body), self.assertRaises(ApiError) as caught:
                adapter.mutate(body)
            self.assertEqual((caught.exception.status, runner.calls), (400, []))

    def test_cli_failure_text_is_relayed_as_400(self):
        usage = 'usage: usage_budget.py mode gpt-burn|claude-burn|auto'
        for result, text in ((CommandResult(2, '', usage + '\n'), usage),
                             (CommandResult(1, '', 'could not save mode to /state'), 'could not save mode to /state'),
                             (CommandResult(2, 'stdout only\n', ''), 'stdout only')):
            adapter, runner = self.adapter(result)
            with self.subTest(text=text), self.assertRaises(ApiError) as caught:
                adapter.mutate({'action': 'mode', 'mode': 'gpt-conserve', 'confirmed': True})
            self.assertEqual((caught.exception.status, str(caught.exception)), (400, text))
            self.assertEqual(len(runner.calls), 1)

    def test_post_route_reaches_the_adapter(self):
        routes = PanelRoutes(runner=FakeRunner([CommandResult(0, self.REPORT), json_command(LIVE)]))
        self.addCleanup(routes.close)
        with running_server(routes) as server:
            code, _, raw = request(server, '/api/usage', 'POST',
                                   json.dumps({'action': 'mode', 'mode': 'auto', 'confirmed': True}), auth(server, post=True))
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)['message'], self.REPORT)
