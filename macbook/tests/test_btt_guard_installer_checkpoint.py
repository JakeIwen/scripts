"""Installer checkpoints use private fixtures, never the live BTT database/state."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from macbook.bettertouchtool.btt_guard import checkpoint, cli, installer_checkpoint as installer, storage
from macbook.bettertouchtool.btt_guard.model import (
    AuditStatus, ExportUnavailableError, InstallerCheckpointReason as Reason,
    InstallerCheckpointStatus as Status,
)
from macbook.tests.btt_guard_fixtures import create_guard_database


def report(status=AuditStatus.HEALTHY, checkpoint_id='cp-original', findings=None):
    return dict(status=status.value, checkpoint_id=checkpoint_id, findings=findings or [],
                checked_at=1, btt_version='fixture')


class InstallerCheckpointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name)/'state'
        self.audit = patch.object(cli, 'audit_state', return_value=report()).start()
        self.capture = patch.object(checkpoint, 'capture', return_value={'checkpoint_id': 'cp-next'}).start()
        self.addCleanup(patch.stopall)

    def baseline(self):
        return installer.record_baseline(self.state)

    def accept(self, baseline, touched=('owned',), wrote=True):
        return installer.accept_installer_edits(baseline, touched, 'fixture-installer', wrote=wrote)

    def test_healthy_baseline_records_report_then_accepts_owned_drift(self):
        baseline = self.baseline()
        self.assertIsNone(baseline['reason'])
        self.assertEqual(baseline['audit'], report())
        self.audit.return_value['findings'].append({'uuid': 'later', 'kind': 'changed'})
        self.assertEqual(baseline['audit']['findings'], [])
        self.audit.return_value = report(AuditStatus.DRIFT, findings=[
            {'kind': 'action_changed', 'uuid': 'owned', 'detail': 'payload'}])
        result = self.accept(baseline)
        self.assertEqual(result['status'], Status.TAKEN)
        self.capture.assert_called_once_with(str(self.state), accept_current=True, replace=True)
        self.assertEqual(self.audit.call_count, 2)

    def test_pure_addition_is_checkpointed_even_when_audit_is_healthy(self):
        self.assertEqual(self.accept(self.baseline())['status'], Status.TAKEN)
        self.capture.assert_called_once()

    def test_ineligible_baselines_do_not_audit_again_or_capture(self):
        for before, reason in [
            (report(checkpoint_id=None), Reason.NO_CHECKPOINT),
            (report(AuditStatus.DRIFT), Reason.BASELINE_DRIFT),
            (report(AuditStatus.UNAVAILABLE), Reason.BASELINE_UNAVAILABLE),
        ]:
            with self.subTest(reason=reason):
                self.audit.reset_mock()
                self.audit.return_value = before
                baseline = self.baseline()
                self.assertEqual(baseline['reason'], reason)
                self.assertEqual(self.accept(baseline)['reason'], reason)
                self.audit.assert_called_once()
                self.capture.assert_not_called()

    def test_baseline_failure_and_opt_out_are_nonfatal(self):
        self.audit.side_effect = OSError('private diagnostic')
        self.assertEqual(self.baseline()['reason'], Reason.BASELINE_FAILED)
        self.audit.reset_mock()
        baseline = installer.record_baseline(self.state, no_checkpoint=True)
        self.assertEqual(self.accept(baseline)['reason'], Reason.OPT_OUT)
        self.audit.assert_not_called()
        self.capture.assert_not_called()

    def test_changed_checkpoint_foreign_findings_and_unavailable_are_refused(self):
        baseline = self.baseline()
        for after, reason in [
            (report(checkpoint_id='cp-replaced'), Reason.CHECKPOINT_CHANGED),
            (report(AuditStatus.DRIFT, findings=[{'uuid': 'foreign'}]), Reason.FOREIGN_FINDINGS),
            (report(AuditStatus.UNAVAILABLE), Reason.AUDIT_UNAVAILABLE),
        ]:
            with self.subTest(reason=reason):
                self.audit.return_value = after
                self.assertEqual(self.accept(baseline)['reason'], reason)
                self.capture.assert_not_called()

    def test_zero_writes_and_invalid_edits_do_not_capture(self):
        baseline = self.baseline()
        self.assertEqual(self.accept(baseline, wrote=False)['reason'], Reason.NO_WRITES)
        for edits in ([], [''], 'owned', [None]):
            self.assertEqual(self.accept(baseline, edits)['reason'], Reason.INVALID_EDITS)
        self.capture.assert_not_called()

    def test_audit_and_capture_failures_never_raise_or_leak_error_details(self):
        baseline = self.baseline()
        self.audit.side_effect = OSError('secret native details')
        self.assertEqual(self.accept(baseline)['reason'], Reason.AUDIT_FAILED)
        self.audit.side_effect = None
        for error, reason in [(RuntimeError('secret native details'), Reason.CAPTURE_FAILED),
                              (ExportUnavailableError('native error'), Reason.EXPORT_UNAVAILABLE)]:
            self.capture.side_effect = error
            result = self.accept(baseline)
            self.assertEqual(result['status'], Status.FAILED)
            self.assertEqual(result['reason'], reason)
            self.assertNotIn('native', json.dumps(result))

    def test_same_guard_lock_serializes_capture_and_refuses_busy_state(self):
        baseline = self.baseline()
        def capture_under_lock(*args, **kwargs):
            with self.assertRaises(BlockingIOError), storage.state_lock(self.state):
                pass
            return {'checkpoint_id': 'cp-next'}
        self.capture.side_effect = capture_under_lock
        self.assertEqual(self.accept(baseline)['status'], Status.TAKEN)
        with storage.state_lock(self.state):
            self.assertEqual(self.accept(baseline)['status'], Status.FAILED)

    def test_public_client_prints_status_and_exact_terminal_command(self):
        baseline = self.baseline()
        cases = [dict(status=Status.TAKEN.value, reason=None, checkpoint_id='cp-next'),
                 dict(status=Status.SKIPPED.value, reason=Reason.FOREIGN_FINDINGS.value, checkpoint_id=None),
                 dict(status=Status.FAILED.value, reason=Reason.EXPORT_UNAVAILABLE.value, checkpoint_id=None)]
        for result in cases:
            with patch.object(installer, '_request', return_value=result), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(installer.after_install(baseline, ['owned'], 'fixture'), result)
            self.assertIn('BTT Guard checkpoint '+result['status'], output.getvalue())
            self.assertEqual(installer.MANUAL_COMMAND in output.getvalue(), result['status'] != Status.TAKEN)
        with patch.object(installer, '_request', side_effect=RuntimeError('private failure')):
            self.assertEqual(installer.before_install()['reason'], Reason.BASELINE_FAILED)
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(installer.after_install(baseline, ['owned'], 'fixture')['status'], Status.FAILED)
            self.assertNotIn('private failure', output.getvalue())
        with redirect_stdout(io.StringIO()) as output:
            installer.after_install(baseline, [], 'fixture', wrote=False)
        self.assertIn('no records were written', output.getvalue())
        self.assertNotIn(installer.MANUAL_COMMAND, output.getvalue())

    def test_opt_out_and_noop_client_do_not_start_subprocess(self):
        with patch.object(installer, '_request') as request, redirect_stdout(io.StringIO()):
            baseline = installer.before_install(no_checkpoint=True)
            self.assertEqual(installer.after_install(baseline, ['owned'], 'fixture')['reason'], Reason.OPT_OUT)
            self.assertEqual(installer.after_install(baseline, [], 'fixture', wrote=False)['reason'], Reason.NO_WRITES)
            self.assertEqual(installer.skip_checkpoint('fixture', Reason.SAVED_UNVERIFIED)['status'], Status.SKIPPED)
        request.assert_not_called()

    def test_package_bridge_dispatches_only_to_its_configured_state(self):
        with patch.object(checkpoint, 'DEFAULT_STATE', self.state), \
                patch('sys.stdin', io.StringIO(json.dumps({'operation': 'before'}))), \
                redirect_stdout(io.StringIO()) as output:
            installer.main()
        baseline = json.loads(output.getvalue())
        self.assertEqual(baseline['state_dir'], str(self.state))
        request = dict(operation='after', baseline=baseline, wrote=True,
                       touched_uuids=['owned'], installer_name='fixture')
        with patch.object(checkpoint, 'DEFAULT_STATE', self.state), \
                patch('sys.stdin', io.StringIO(json.dumps(request))), \
                redirect_stdout(io.StringIO()) as output:
            installer.main()
        self.assertEqual(json.loads(output.getvalue())['status'], Status.TAKEN)
        request['baseline']['state_dir'] = '/untrusted-state'
        with patch.object(checkpoint, 'DEFAULT_STATE', self.state), \
                patch('sys.stdin', io.StringIO(json.dumps(request))), self.assertRaises(ValueError):
            installer.main()

    def test_worker_receipts_are_validated(self):
        good = dict(message='verified', wrote=True, touched_uuids=['owned'])
        self.assertEqual(installer.parse_edit_receipt(json.dumps(good)), good)
        for change in ({'wrote': 'yes'}, {'touched_uuids': []}, {'touched_uuids': [None]}, {'message': None}):
            with self.assertRaises(ValueError):
                installer.parse_edit_receipt(json.dumps({**good, **change}))


class InstallerFixtureTests(unittest.TestCase):
    def test_verified_edit_advances_only_private_fixture_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)/'guard'
            database, exports = create_guard_database(directory)
            with patch.object(checkpoint, 'current_database', return_value=database), \
                    patch('macbook.bettertouchtool.btt_guard.database.current_database', return_value=database), \
                    patch.object(checkpoint.api, 'export_graph', side_effect=lambda _: deepcopy(exports)):
                original = checkpoint.capture(state, accept_current=True)
                baseline = installer.record_baseline(state)
                with sqlite3.connect(database) as connection:
                    connection.execute("UPDATE ZBTTBASEENTITY SET ZLAUNCHPATH='new-task' WHERE ZUNIQUEIDENTIFIER='named'")
                exports[1]['BTTShellTaskActionScript'] = 'new-task'
                result = installer.accept_installer_edits(baseline, ['named'], 'fixture', wrote=True)
                self.assertEqual(result['status'], Status.TAKEN)
                self.assertNotEqual(result['checkpoint_id'], original['checkpoint_id'])
                self.assertEqual(cli.audit_state(state)['status'], AuditStatus.HEALTHY)
                checkpoint.load_checkpoint(state, original['checkpoint_id'])


if __name__ == '__main__':
    unittest.main()
