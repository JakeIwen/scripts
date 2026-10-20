"""The scheduled monitor accepts edits made inside one running BTT and alerts on the rest."""
from copy import deepcopy
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from macbook.bettertouchtool.btt_guard import checkpoint, cli, edit_acceptance, storage
from macbook.bettertouchtool.btt_guard.edit_acceptance import SETTLE_SECONDS, decide, initial_state, running_btt
from macbook.bettertouchtool.btt_guard.model import EditAcceptanceAction as Action
from macbook.bettertouchtool.btt_guard.monitor import DEBOUNCE_SECONDS
from macbook.tests.btt_guard_fixtures import create_guard_database

SESSION = {'pid': 7490, 'started': 'Sat Oct 17 09:23:35 2026'}
RELAUNCHED = {'pid': 9001, 'started': 'Mon Oct 19 18:00:00 2026'}


def report(status='healthy', checkpoint_id='cp-1', version='6.885', findings=None):
    return dict(status=status, checkpoint_id=checkpoint_id, btt_version=version, checked_at=1.0,
                findings=findings if findings is not None else
                ([] if status == 'healthy' else [{'kind': 'action_changed', 'uuid': 'u', 'detail': 'd'}]))


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.state = initial_state()
        self.assertEqual(decide(self.state, report(), SESSION, 0), Action.NOT_NEEDED)

    def test_drift_in_the_same_session_settles_then_accepts(self):
        self.assertEqual(decide(self.state, report('drift'), SESSION, 100), Action.SETTLING)
        self.assertEqual(decide(self.state, report('drift'), SESSION, 100 + SETTLE_SECONDS - 1), Action.SETTLING)
        self.assertEqual(decide(self.state, report('drift'), SESSION, 100 + SETTLE_SECONDS), Action.ACCEPTED)

    def test_a_further_edit_restarts_the_settle_period(self):
        decide(self.state, report('drift'), SESSION, 100)
        changed = report('drift', findings=[{'kind': 'config_changed', 'uuid': 'v', 'detail': 'd'}])
        self.assertEqual(decide(self.state, changed, SESSION, 100 + SETTLE_SECONDS), Action.SETTLING)

    def test_drift_after_relaunch_update_or_new_checkpoint_is_ineligible(self):
        for drift, process in [(report('drift'), RELAUNCHED), (report('drift', version='6.9'), SESSION),
                               (report('drift', checkpoint_id='cp-2'), SESSION), (report('drift'), None),
                               (report('unavailable'), SESSION)]:
            with self.subTest(drift=drift, process=process):
                self.assertEqual(decide(deepcopy(self.state), drift, process, 100), Action.INELIGIBLE)

    def test_healthy_without_btt_running_leaves_no_session_to_trust(self):
        state = initial_state()
        decide(state, report(), None, 0)
        self.assertEqual(decide(state, report('drift'), SESSION, 100), Action.INELIGIBLE)

    def test_refused_drift_stays_refused_until_it_changes(self):
        self.state['refused_key'] = 'other'
        decide(self.state, report('drift'), SESSION, 100)
        self.state['refused_key'] = self.state['candidate']['key']
        self.assertEqual(decide(self.state, report('drift'), SESSION, 200 + SETTLE_SECONDS), Action.REFUSED)


class RunningBttTests(unittest.TestCase):
    def listing(self, stdout, code=0):
        return lambda *args, **kwargs: subprocess.CompletedProcess(args, code, stdout, '')

    def test_main_process_is_identified_and_helpers_ignored(self):
        stdout = (' 1164 Sat Oct 17 09:14:26 2026     /Applications/BetterTouchTool.app/Contents/PlugIns/'
                  'BetterTouchTool WidgetsExtension.appex/Contents/MacOS/BetterTouchTool WidgetsExtension\n'
                  ' 7490 Sat Oct 17 09:23:35 2026     /Applications/BetterTouchTool.app/Contents/MacOS/BetterTouchTool\n'
                  '10003 Sat Oct 17 09:23:37 2026     /Applications/BetterTouchTool.app/Contents/Resources/'
                  'BTTRelaunch.app/Contents/MacOS/BTTRelaunch\n')
        self.assertEqual(running_btt(self.listing(stdout)), SESSION)

    def test_absent_ambiguous_or_failed_listing_is_none(self):
        main = ' 7490 Sat Oct 17 09:23:35 2026 /Applications/BetterTouchTool.app/Contents/MacOS/BetterTouchTool\n'
        for stdout, code in [('', 0), (main + main.replace('7490', '7491'), 0), (main, 1)]:
            self.assertIsNone(running_btt(self.listing(stdout, code)))


class ScheduledMonitorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name)/'state'
        self.database, self.exports = create_guard_database(temp.name)
        self.addCleanup(patch.stopall)
        patch.object(checkpoint, 'current_database', return_value=self.database).start()
        patch('macbook.bettertouchtool.btt_guard.database.current_database', return_value=self.database).start()
        patch.object(checkpoint.api, 'export_graph', side_effect=lambda _: deepcopy(self.exports)).start()
        self.process = patch.object(edit_acceptance, 'running_btt', return_value=SESSION).start()
        self.sender = patch.object(cli, 'send_warning_notification', return_value=True).start()
        self.original = checkpoint.capture(self.state, accept_current=True)['checkpoint_id']

    def run_at(self, now, *, managed=True):
        with patch.object(cli.time, 'time', return_value=now):
            return cli.run_monitor(self.state, managed=managed)

    def edit(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute("UPDATE ZBTTBASEENTITY SET ZLAUNCHPATH='new-task' WHERE ZUNIQUEIDENTIFIER='named'")
        self.exports[1]['BTTShellTaskActionScript'] = 'new-task'

    def active(self):
        return storage.load_json(self.state/checkpoint.ACTIVE_FILE)['checkpoint_id']

    def test_edit_in_the_running_session_is_accepted_without_an_alert(self):
        self.assertEqual(self.run_at(1000)['acceptance'], 'not_needed')
        self.edit()
        waiting = self.run_at(1060)
        self.assertEqual((waiting['audit_status'], waiting['acceptance'], waiting['notification']),
                         ('drift', 'settling', 'deferred'))
        self.assertEqual(edit_acceptance.status(self.state)['accepts_at'], 1060 + SETTLE_SECONDS)
        accepted = self.run_at(1060 + SETTLE_SECONDS)
        self.assertEqual((accepted['audit_status'], accepted['acceptance']), ('healthy', 'accepted'))
        self.assertNotEqual(self.active(), self.original)
        self.assertEqual(edit_acceptance.status(self.state)['last_accepted']['checkpoint_id'], self.active())
        checkpoint.load_checkpoint(self.state, self.original)
        self.sender.assert_not_called()

    def test_drift_after_a_relaunch_is_alerted_and_never_accepted(self):
        self.run_at(1000)
        self.edit()
        self.process.return_value = RELAUNCHED
        first = self.run_at(1060)
        second = self.run_at(1060 + DEBOUNCE_SECONDS + 1)
        self.assertEqual([first['acceptance'], second['acceptance']], ['ineligible', 'ineligible'])
        self.assertEqual(second['notification'], 'sent')
        self.run_at(1060 + SETTLE_SECONDS * 2)
        self.assertEqual(self.active(), self.original)

    def test_refused_capture_is_alerted_and_not_retried(self):
        self.run_at(1000)
        self.edit()
        self.run_at(1060)
        with patch.object(edit_acceptance, 'capture', side_effect=ValueError('empty active button')) as capture:
            refused = self.run_at(1060 + SETTLE_SECONDS)
            again = self.run_at(1060 + SETTLE_SECONDS + DEBOUNCE_SECONDS + 1)
        self.assertEqual([refused['acceptance'], again['acceptance']], ['refused', 'refused'])
        self.assertEqual(again['notification'], 'sent')
        capture.assert_called_once()
        self.assertEqual(edit_acceptance.status(self.state)['last_refused']['reason'], 'capture_refused')
        self.assertEqual(self.active(), self.original)

    def test_manual_passes_never_accept_or_record_a_session(self):
        self.run_at(1000, managed=False)
        self.edit()
        result = self.run_at(1000 + SETTLE_SECONDS * 2, managed=False)
        self.assertIsNone(result['acceptance'])
        self.assertFalse((self.state/edit_acceptance.STATE_NAME).exists())
        self.assertEqual(self.active(), self.original)

    def test_status_reports_missing_and_unreadable_state(self):
        self.assertEqual(edit_acceptance.status(self.state)['state'], 'none')
        (self.state/edit_acceptance.STATE_NAME).write_text('{')
        self.assertEqual(edit_acceptance.status(self.state)['state'], 'unreadable')
        self.assertEqual(self.run_at(1000)['acceptance'], 'not_needed')
        self.assertEqual(edit_acceptance.status(self.state)['state'], 'ok')


if __name__ == '__main__':
    unittest.main()
