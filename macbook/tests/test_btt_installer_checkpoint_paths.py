from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import port_media_icons
import repair_media_actions
import repair_rps
import restore_media
import stabilize_media


class InstallerCheckpointWiringTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.database = self.directory / 'configuration.sqlite'
        self.backup = self.directory / 'backup.json'

    @staticmethod
    def checkpoint_mocks(stack, module):
        baseline = {'test': 'baseline'}
        before = stack.enter_context(patch.object(module, 'before_install', return_value=baseline))
        after = stack.enter_context(patch.object(module, 'after_install'))
        return baseline, before, after

    def test_port_media_icons_checkpoints_verified_button_edits(self):
        changes = [{'uuid': 'BUTTON-1', 'label': 'One', 'patch': {'color': 'black'}}]

        def worker(mode, path=''):
            if mode == 'snapshot':
                return json.dumps({'BTTUUID': 'MEDIA', 'BTTMenuItems': []})
            return 'ok'

        with ExitStack() as stack:
            baseline, before, after = self.checkpoint_mocks(stack, port_media_icons)
            stack.enter_context(patch.object(sys, 'argv', ['port_media_icons.py', '--no-checkpoint']))
            stack.enter_context(patch.object(port_media_icons, 'current_database', return_value=self.database))
            stack.enter_context(patch.object(port_media_icons, 'build_changes', return_value=changes))
            stack.enter_context(patch.object(port_media_icons, 'worker', side_effect=worker))
            stack.enter_context(patch.object(port_media_icons, 'preset_path', return_value='/preset'))
            stack.enter_context(patch.object(port_media_icons, 'backup_configuration', return_value=self.backup))
            saved = stack.enter_context(patch.object(port_media_icons, 'appearance_disk_errors', return_value=[]))
            port_media_icons.main()

        before.assert_called_once_with(no_checkpoint=True)
        saved.assert_called_once()
        after.assert_called_once_with(baseline, ['BUTTON-1'], 'port_media_icons', wrote=True)

    def test_repair_media_actions_checkpoints_verified_entities(self):
        plan = {
            'entities': [{'uuid': 'ACTION-1', 'parent': 'BUTTON-1', 'node': {}}],
            'buttons': [{'uuid': 'BUTTON-1'}],
            'snapshot': {'ACTION-ALREADY-RUNTIME': {'BTTUUID': 'ACTION-ALREADY-RUNTIME'}},
        }
        plan['entities'].append({'uuid': 'ACTION-ALREADY-RUNTIME', 'parent': 'BUTTON-1', 'node': {}})
        with ExitStack() as stack:
            baseline, before, after = self.checkpoint_mocks(stack, repair_media_actions)
            stack.enter_context(patch.object(repair_media_actions, 'current_database', return_value=self.database))
            stack.enter_context(patch.object(repair_media_actions, 'action_plan', return_value=plan))
            apply_plan = stack.enter_context(patch.object(repair_media_actions, 'apply_plan'))
            repair_media_actions.main(['--apply', '--no-checkpoint'])

        before.assert_called_once_with(no_checkpoint=True)
        apply_plan.assert_called_once_with(self.database, plan)
        after.assert_called_once_with(
            baseline, ['ACTION-1'], 'repair_media_actions', wrote=True)

    def test_action_repair_does_not_claim_already_present_runtime_records(self):
        plan = {'entities': [{'uuid': 'ACTION', 'parent': 'BUTTON', 'node': {}}],
                'buttons': [], 'snapshot': {'ACTION': {'BTTUUID': 'ACTION'}}}
        with ExitStack() as stack:
            baseline, _, after = self.checkpoint_mocks(stack, repair_media_actions)
            stack.enter_context(patch.object(repair_media_actions, 'current_database', return_value=self.database))
            stack.enter_context(patch.object(repair_media_actions, 'action_plan', return_value=plan))
            stack.enter_context(patch.object(repair_media_actions, 'apply_plan'))
            repair_media_actions.main(['--apply'])
        after.assert_called_once_with(baseline, [], 'repair_media_actions', wrote=False)

    def test_restore_media_checkpoints_only_records_written_during_resume(self):
        root = {'uuid': restore_media.MEDIA, 'parent': None, 'node': {}}
        existing = {'uuid': 'EXISTING-CHILD', 'parent': restore_media.MEDIA, 'node': {}}
        created = {'uuid': 'CREATED-CHILD', 'parent': restore_media.MEDIA, 'node': {}}
        plan = {'source': 'source.json', 'main_items': 2, 'entities': [root, existing, created]}
        before_records = {restore_media.MEDIA: {}, 'EXISTING-CHILD': {}}

        with ExitStack() as stack:
            baseline, before, after = self.checkpoint_mocks(stack, restore_media)
            stack.enter_context(patch.object(restore_media, 'build_plan', return_value=plan))
            stack.enter_context(patch.object(restore_media, 'current_database', return_value=self.database))
            stack.enter_context(patch.object(restore_media, 'preflight', return_value=(before_records, 7)))
            stack.enter_context(patch.object(restore_media, 'resume_origin', return_value='prior-backup'))
            worker = stack.enter_context(patch.object(restore_media, 'worker', return_value='ok'))
            stack.enter_context(patch.object(restore_media, 'backup_configuration', return_value=self.backup))
            wait_saved = stack.enter_context(patch.object(restore_media, 'wait_saved'))
            stack.enter_context(patch.object(restore_media, 'verify_unrelated'))
            restore_media.main(['--apply', '--resume', '--no-checkpoint', '--source', 'source.json'])

        before.assert_called_once_with(no_checkpoint=True)
        self.assertEqual(worker.call_count, 4)
        self.assertEqual(wait_saved.call_count, 3)
        after.assert_called_once_with(
            baseline, [restore_media.MEDIA, 'CREATED-CHILD'], 'restore_media', wrote=True)

    def test_stabilize_media_checkpoints_created_and_patched_records(self):
        plan = {
            'entities': [{'uuid': 'CREATED-1', 'parent': 'PARENT', 'node': {}}],
            'configChanges': [{'uuid': 'LAYOUT-1', 'patch': {'height': 40}}],
            'screen': {'width': 100, 'height': 100},
        }

        def worker(mode, argument, timeout=60):
            if mode == 'plan':
                return json.dumps(plan)
            return 'ok'

        with ExitStack() as stack:
            baseline, before, after = self.checkpoint_mocks(stack, stabilize_media)
            stack.enter_context(patch.object(sys, 'argv', ['stabilize_media.py', '--no-checkpoint']))
            stack.enter_context(patch.object(stabilize_media, 'current_database', return_value=self.database))
            stack.enter_context(patch.object(stabilize_media, 'worker', side_effect=worker))
            stack.enter_context(patch.object(stabilize_media, 'records', return_value={
                stabilize_media.MEDIA: {'ZBELONGSTOPRESET2': 7},
            }))
            stack.enter_context(patch.object(
                stabilize_media, 'pending_config_changes', return_value=plan['configChanges']))
            stack.enter_context(patch.object(stabilize_media, 'backup_configuration', return_value=self.backup))
            saved = stack.enter_context(patch.object(stabilize_media, 'disk_errors', return_value=[]))
            stack.enter_context(patch.object(stabilize_media, 'restart_btt'))
            stabilize_media.main()

        before.assert_called_once_with(no_checkpoint=True)
        self.assertGreaterEqual(saved.call_count, 2)
        after.assert_called_once_with(
            baseline, ['CREATED-1', 'LAYOUT-1'], 'stabilize_media', wrote=True)

    def test_repair_rps_checkpoints_saved_action(self):
        sync_script = self.directory / 'sync_scripts.sh'
        sync_script.write_text('#!/bin/sh\n')
        with ExitStack() as stack:
            baseline, before, after = self.checkpoint_mocks(stack, repair_rps)
            stack.enter_context(patch.object(sys, 'argv', ['repair_rps.py', '--no-checkpoint']))
            stack.enter_context(patch.object(repair_rps, 'COMMAND', str(sync_script)))
            stack.enter_context(patch.object(repair_rps, 'current_database', return_value=self.database))
            stack.enter_context(patch.object(repair_rps, 'preflight', side_effect=[False, False, True]))
            stack.enter_context(patch.object(
                repair_rps, 'worker', side_effect=[json.dumps({'snapshot': True}), 'applied']))
            stack.enter_context(patch.object(repair_rps, 'backup_configuration', return_value=self.backup))
            repair_rps.main()

        before.assert_called_once_with(no_checkpoint=True)
        after.assert_called_once_with(baseline, [repair_rps.ACTION], 'repair_rps', wrote=True)

    def test_repair_rps_noop_still_records_baseline(self):
        with ExitStack() as stack:
            baseline, before, after = self.checkpoint_mocks(stack, repair_rps)
            stack.enter_context(patch.object(sys, 'argv', ['repair_rps.py']))
            stack.enter_context(patch.object(repair_rps, 'current_database', return_value=self.database))
            stack.enter_context(patch.object(repair_rps, 'preflight', return_value=True))
            repair_rps.main()

        before.assert_called_once_with(no_checkpoint=False)
        after.assert_called_once_with(baseline, [], 'repair_rps', wrote=False)


if __name__ == '__main__':
    unittest.main()
