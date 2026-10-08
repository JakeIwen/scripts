"""Known-good promotion, integrity, and public CLI behavior without live BTT."""
import contextlib
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from macbook.bettertouchtool.btt_common import MEDIA
from macbook.bettertouchtool.btt_guard import checkpoint, cli, storage
from macbook.bettertouchtool.btt_guard.model import AuditStatus
from macbook.tests.btt_guard_fixtures import create_guard_database


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.state = self.directory/'state'
        self.database, self.exports = create_guard_database(self.directory)
        self.addCleanup(patch.stopall)
        patch.object(checkpoint,'current_database',return_value=self.database).start()
        patch('macbook.bettertouchtool.btt_guard.database.current_database',return_value=self.database).start()
        self.exporter = patch.object(checkpoint.api,'export_graph',side_effect=lambda _: deepcopy(self.exports)).start()

    def capture(self, **options):
        return checkpoint.capture(self.state,accept_current=True,**options)

    def mutate(self, command, *parameters):
        with sqlite3.connect(self.database) as connection:
            connection.execute(command,parameters)

    def test_explicit_promotion_and_private_artifacts(self):
        with self.assertRaisesRegex(ValueError,'accept-current'):
            checkpoint.capture(self.state)
        self.exporter.assert_not_called()
        manifest = self.capture()
        loaded, snapshot, definitions = checkpoint.load_checkpoint(self.state)
        self.assertEqual(loaded,manifest)
        self.assertEqual(set(snapshot['records']),{MEDIA,'button','action','named'})
        self.assertEqual(definitions['action']['BTTOrder'],0)
        self.assertEqual(definitions['button']['BTTMenuConfig']['BTTMenuAttributedText'],'a private label')
        for filename in manifest['files']:
            path=self.state/'checkpoints'/manifest['checkpoint_id']/filename
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
        self.assertEqual(cli.audit_state(self.state)['status'],AuditStatus.HEALTHY)

    def test_corrupt_artifact_fails_before_repair_api(self):
        manifest=self.capture()
        file=self.state/'checkpoints'/manifest['checkpoint_id']/'definitions.json'
        file.write_text('{}')
        self.assertEqual(cli.audit_state(self.state)['status'],AuditStatus.UNAVAILABLE)
        with patch.object(cli.api,'call_worker') as worker:
            with self.assertRaisesRegex(ValueError,'integrity'):
                cli.repair_checkpoint(self.state,apply=True)
        worker.assert_not_called()

    def test_bad_current_cannot_replace_good_checkpoint(self):
        self.capture()
        before=(self.state/checkpoint.ACTIVE_FILE).read_bytes()
        self.mutate("DELETE FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER='action'")
        self.assertEqual(cli.audit_state(self.state)['status'],AuditStatus.DRIFT)
        with self.assertRaisesRegex(ValueError,'unresolved'):
            self.capture(replace=True)
        self.assertEqual((self.state/checkpoint.ACTIVE_FILE).read_bytes(),before)

    def test_checkpoint_replacement_requires_explicit_flag_and_retains_old_generation(self):
        first=self.capture()
        with self.assertRaisesRegex(ValueError,'replace'):
            self.capture()
        second=self.capture(replace=True)
        self.assertNotEqual(first['checkpoint_id'],second['checkpoint_id'])
        self.assertEqual(checkpoint.load_checkpoint(self.state,first['checkpoint_id'])[0],first)

    def test_failed_runtime_capture_keeps_known_good_pointer(self):
        self.capture()
        before=(self.state/checkpoint.ACTIVE_FILE).read_bytes()
        self.exporter.side_effect=RuntimeError('API unavailable')
        with self.assertRaises(RuntimeError): self.capture(replace=True)
        self.assertEqual((self.state/checkpoint.ACTIVE_FILE).read_bytes(),before)

    def test_detached_action_is_not_mistaken_for_deleted(self):
        self.capture()
        self.mutate("UPDATE ZBTTBASEENTITY SET ZPARENT=NULL WHERE ZUNIQUEIDENTIFIER='action'")
        report=cli.audit_state(self.state)
        self.assertIn('reparented',{x['kind'] for x in report['findings'] if x['uuid']=='action'})
        result=cli.repair_checkpoint(self.state)
        self.assertEqual(result['conflicts'],[])
        self.assertEqual(result['operations'],[{'uuid':'action','mode':'reattach','parent':'button'}])

    def test_payload_changes_block_repair_and_never_overwrite(self):
        self.capture()
        self.mutate("UPDATE ZBTTBASEENTITY SET ZLAUNCHPATH='a changed command' WHERE ZUNIQUEIDENTIFIER='named'")
        with patch.object(cli.api,'call_worker') as worker:
            result=cli.repair_checkpoint(self.state,apply=True)
        self.assertTrue(result['conflicts'])
        worker.assert_not_called()

    def test_whole_missing_menu_plan_preserves_existing_named_dependency(self):
        self.capture()
        self.mutate('DELETE FROM ZBTTBASEENTITY WHERE Z_PK IN (1,2,3)')
        result=cli.repair_checkpoint(self.state)
        self.assertEqual(result['conflicts'],[])
        self.assertEqual([x['uuid'] for x in result['operations']],[MEDIA,'button','action'])

    def test_unavailable_database_is_not_a_clean_audit(self):
        self.capture()
        with patch.object(cli,'current_for',side_effect=sqlite3.OperationalError('private path')):
            report=cli.audit_state(self.state)
        self.assertEqual(report['status'],'unavailable')
        self.assertNotIn('private path',json.dumps(report))

    def test_monitor_no_notify_never_contacts_api_or_sends(self):
        self.capture()
        with patch.object(cli,'send_warning_notification') as sender,patch.object(cli.api,'call_worker') as api:
            result=cli.run_monitor(self.state,notify=False)
        self.assertEqual(result['audit_status'],'healthy')
        sender.assert_not_called();api.assert_not_called()
        self.assertTrue((self.state/'last-audit.json').exists())
        self.assertTrue((self.state/'manual-audit-health.json').exists())
        self.assertFalse((self.state/'monitor-health.json').exists())

    def test_malformed_monitor_state_uses_persistent_fallback_and_warns_once(self):
        self.capture()
        broken=self.state/'monitor-state.json'
        broken.write_text('broken JSON');broken.chmod(0o600)
        with patch.object(cli,'send_warning_notification',return_value=True) as sender:
            with patch.object(cli.time,'time',return_value=1000): first=cli.run_monitor(self.state,managed=True)
            with patch.object(cli.time,'time',return_value=1061): second=cli.run_monitor(self.state,managed=True)
            with patch.object(cli.time,'time',return_value=1122): third=cli.run_monitor(self.state,managed=True)
        self.assertEqual([x['notification'] for x in (first,second,third)],['pending','sent','deduplicated'])
        self.assertTrue(all(x['audit_status']=='unavailable' for x in (first,second,third)))
        sender.assert_called_once()
        self.assertEqual(broken.read_text(),'broken JSON')
        self.assertTrue((self.state/'monitor-recovery-state.json').exists())
        self.assertEqual(storage.load_json(self.state/'monitor-health.json')['audit_status'],'unavailable')

    def test_repaired_monitor_state_preserves_delivered_alert_and_sends_recovery(self):
        self.capture()
        broken=self.state/'monitor-state.json'
        broken.write_text('{');broken.chmod(0o600)
        with patch.object(cli,'send_warning_notification',return_value=True) as sender:
            with patch.object(cli.time,'time',return_value=1000):cli.run_monitor(self.state)
            with patch.object(cli.time,'time',return_value=1061):cli.run_monitor(self.state)
            broken.unlink()  # Simulate the owner's explicit recovery of bad state.
            with patch.object(cli.time,'time',return_value=1122):a=cli.run_monitor(self.state)
            with patch.object(cli.time,'time',return_value=1183):b=cli.run_monitor(self.state)
        self.assertEqual(a['notification'],'pending')
        self.assertEqual(b['notification'],'sent')
        self.assertEqual(sender.call_count,2)
        self.assertFalse((self.state/'monitor-recovery-state.json').exists())

    def test_hash_valid_but_semantically_wrong_definition_is_rejected(self):
        manifest=self.capture();folder=self.state/'checkpoints'/manifest['checkpoint_id']
        definitions=storage.load_json(folder/'definitions.json')
        definitions['action']['BTTNamedTriggerToTrigger']='A different task'
        storage.write_json(folder/'definitions.json',definitions)
        manifest['files']['definitions.json']=storage.file_hash(folder/'definitions.json')
        storage.write_json(folder/'manifest.json',manifest)
        with self.assertRaisesRegex(ValueError,'payload'):
            checkpoint.load_checkpoint(self.state)

    def test_public_cli_audit_requires_no_mutation_or_api(self):
        self.capture()
        before=self.database.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result=cli.main(['--state-dir',str(self.state),'audit'])
        self.assertEqual(result,0)
        self.assertEqual(json.loads(output.getvalue())['status'],'healthy')
        self.assertEqual(self.database.read_bytes(),before)

    def test_owner_state_rejects_symlinks_and_public_json(self):
        storage.private_dir(self.state)
        public=self.directory/'public.json';public.write_text('{}');public.chmod(0o644)
        with self.assertRaises(ValueError):storage.load_json(public)
        link=self.state/'linked.json';link.symlink_to(public)
        with self.assertRaises((ValueError,OSError)):storage.load_json(link)
        with self.assertRaises(ValueError):storage.write_json(link,{})


if __name__ == '__main__':unittest.main()
