"""Exercise public CLI and controller boundaries without router or torrent I/O."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest

from pi.apps.metered.cli import main
from pi.apps.metered.config import Paths
from pi.apps.metered.controller import Controller
from shared.metered.records import (
    Actual, ApprovalKind, Host, MemberKind, UplinkEvidence, UplinkMember,
)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        home = Path(temporary.name)
        self.paths = Paths(*(home / name for name in ('config.json', 'policy.json', 'state.json', 'mac.json')))
        self.now = 100.0
        self.network = 'denlink'
        self.calls = []
        def collect(home):
            return UplinkEvidence(self.now, 'balanced', [
                UplinkMember('wan', 100, True, MemberKind.UBNT, self.network, self.network)])
        self.controller = Controller(self.paths, collect,
            {'pi.torrents': lambda allowed, **_flags: self.calls.append(allowed) or
             (Actual.RUNNING if allowed else Actual.HELD)}, lambda: self.now)

    def cli(self, arguments, payload=None):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(arguments, self.controller, io.StringIO(json.dumps(payload)))
        return code, output.getvalue()

    def test_reconciles_without_dashboard_and_retries_unchanged_denials(self):
        for _ in range(2):
            state = self.controller.evaluate(enforce=True)
            self.assertEqual(state.consumers['pi.torrents'].actual, Actual.HELD)
        self.assertEqual(self.calls, [False, False])
        self.network = 'SafeWifi'; self.now += 10
        self.controller.evaluate(enforce=True)
        self.assertEqual(self.calls, [False, False, True])

    def test_timed_approval_expires_without_configuration_or_route_change(self):
        self.controller.evaluate(enforce=True)
        code, text = self.cli(['approve', 'pi.torrents', '10s'])
        self.assertEqual(code, 0, text)
        self.assertEqual(self.cli(['allows', 'pi.torrents'])[0], 0)
        self.controller.evaluate(enforce=True)
        self.now = 111
        self.assertEqual(self.cli(['allows', 'pi.torrents'])[0], 1)
        self.controller.evaluate(enforce=True)
        self.assertEqual(self.calls, [False, True, False])

    def test_episode_ends_and_cannot_reappear(self):
        self.controller.evaluate()
        self.assertEqual(self.cli(['approve', 'pi.torrents', 'episode'])[0], 0)
        self.network = 'free'; self.now += 10
        self.controller.evaluate()
        self.network = 'denlink'; self.now += 10
        self.controller.evaluate()
        self.assertEqual(self.cli(['allows', 'pi.torrents'])[0], 1)

    def test_config_revision_invalidates_a_fresh_cached_allow(self):
        self.network = 'hotel'
        self.controller.evaluate()
        self.assertEqual(self.cli(['allows', 'pi.torrents'])[0], 0)
        self.assertEqual(self.cli(['ssid', 'add', 'hotel'])[0], 0)
        self.assertEqual(self.cli(['allows', 'pi.torrents'])[0], 1)

    def test_invalid_policy_fails_closed_without_crashing_the_reconciler(self):
        self.controller.evaluate()
        self.paths.policy.write_text('not-json')
        state = self.controller.evaluate(enforce=True)
        self.assertIsNone(state.verdict.metered)
        self.assertEqual(self.calls, [False])
        self.assertEqual(self.cli(['is-metered'])[0], 0)
        self.assertEqual(self.cli(['allows', 'pi.torrents'])[0], 1)

    def test_cli_sync_receipts_and_policy_revision_errors(self):
        request = {'ops': [{'op_id': 'offline-1', 'op': 'add_ssid', 'ssid': 'hotel'}], 'mac_report': None}
        code, text = self.cli(['sync'], request)
        self.assertEqual(code, 0, text)
        response = json.loads(text)
        self.assertEqual(response['applied'], ['offline-1'])
        self.assertEqual(self.cli(['sync'], request)[0], 0)
        self.assertEqual(self.controller.config.load().revision, 1)
        code, _ = self.cli(['edit-ssids'], {'op': 'remove', 'ssid': 'hotel', 'revision': 0})
        self.assertEqual(code, 75)
        code, _ = self.cli(['edit-policy'], {'op': 'approve', 'revision': 0,
            'consumer': 'mac.codex_update', 'value': 'until', 'seconds': 60})
        self.assertEqual(code, 2)

    def test_route_change_is_not_consumed_by_a_decision_only_cli_read(self):
        flags = []
        self.controller.adapters = {'pi.torrents': lambda allowed, reset_connections:
            flags.append(reset_connections) or Actual.RUNNING}
        self.network = 'free-one'
        self.controller.evaluate(enforce=True)
        self.network = 'free-two'; self.now += 10
        self.controller.evaluate()
        self.controller.evaluate(enforce=True)
        self.controller.evaluate(enforce=True)
        self.assertEqual(flags, [True, True, False])

    def test_published_permission_deadline_never_outlives_an_approval(self):
        self.controller.evaluate()
        self.assertEqual(self.cli(['approve', 'pi.torrents', '10s'])[0], 0)
        state = self.controller.evaluate()
        self.assertEqual(state.valid_until, 110)
        self.now = 111
        self.assertFalse(self.controller.current().consumers['pi.torrents'].permitted)

    def test_status_is_read_only_and_does_not_start_the_collector(self):
        self.controller.collector = lambda _: self.fail('status must not collect')
        code, text = self.cli(['status', '--json'])
        self.assertEqual(code, 0, text)
        self.assertEqual(json.loads(text)['state']['host'], 'pi')
        self.assertFalse(self.paths.state.exists())


if __name__ == '__main__':
    unittest.main()
