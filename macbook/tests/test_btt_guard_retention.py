"""Guard history older than 90 days is pruned weekly; the active checkpoint never is."""
from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from macbook.bettertouchtool.btt_guard import checkpoint, cli, edit_acceptance, retention, storage
from macbook.bettertouchtool.btt_guard.retention import DAY, PRUNE_INTERVAL_SECONDS, RETENTION_SECONDS
from macbook.tests.btt_guard_fixtures import create_guard_database

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc).timestamp()
# mkdtemp suffixes can contain '_'.
OLD, RECENT = 'cp-20260601T000000Z-old_000a', 'cp-20261001T000000Z-recent0a'
ACTIVE_OLD = 'cp-20260501T000000Z-active0a'
REJECTED = '0fef7253-442a-40cc-8f13-93ca7dcc0761'


class RetentionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = storage.private_dir(Path(temp.name)/'state')
        for name in (OLD, RECENT, ACTIVE_OLD):
            storage.private_dir(self.state/'checkpoints'/name)
            (self.state/'checkpoints'/name/'manifest.json').write_text('{}')
        storage.write_json(self.state/checkpoint.ACTIVE_FILE, {'checkpoint_id': ACTIVE_OLD, 'schema_version': 1})
        rejected = storage.private_dir(self.state/'rejected-captures'/REJECTED)
        os.utime(rejected, (NOW - RETENTION_SECONDS - DAY,) * 2)

    def remaining(self):
        return sorted(p.name for p in (self.state/'checkpoints').iterdir())

    def test_preview_lists_only_expired_inactive_history(self):
        result = retention.prune(self.state, NOW)
        self.assertEqual(result['entries'], [f'checkpoints/{OLD}', f'rejected-captures/{REJECTED}'])
        self.assertFalse(result['applied'])
        self.assertEqual(self.remaining(), sorted((OLD, RECENT, ACTIVE_OLD)))

    def test_apply_deletes_expired_history_and_keeps_the_active_checkpoint(self):
        retention.prune(self.state, NOW, apply=True)
        self.assertEqual(self.remaining(), sorted((RECENT, ACTIVE_OLD)))
        self.assertFalse((self.state/'rejected-captures'/REJECTED).exists())

    def test_without_a_readable_active_pointer_no_checkpoint_is_pruned(self):
        (self.state/checkpoint.ACTIVE_FILE).write_text('{')
        self.assertEqual(retention.prune(self.state, NOW)['entries'], [f'rejected-captures/{REJECTED}'])

    def test_symlinks_and_unknown_names_are_never_pruned(self):
        target = storage.private_dir(self.state/'elsewhere')
        (self.state/'checkpoints'/'cp-20260101T000000Z-linked0a').symlink_to(target)
        storage.private_dir(self.state/'checkpoints'/'cp-not-a-stamp')
        entries = retention.prune(self.state, NOW, apply=True)['entries']
        self.assertEqual(entries, [f'checkpoints/{OLD}', f'rejected-captures/{REJECTED}'])
        self.assertTrue(target.is_dir())

    def test_weekly_pass_runs_once_per_interval_and_records_errors(self):
        self.assertEqual(len(retention.prune_if_due(self.state, NOW)['entries']), 2)
        self.assertIsNone(retention.prune_if_due(self.state, NOW + PRUNE_INTERVAL_SECONDS - 1))
        self.assertEqual(retention.prune_if_due(self.state, NOW + PRUNE_INTERVAL_SECONDS)['entries'], [])
        with patch.object(retention.shutil, 'rmtree', side_effect=OSError('busy')):
            storage.private_dir(self.state/'checkpoints'/'cp-20260602T000000Z-old0000b')
            self.assertIsNone(retention.prune_if_due(self.state, NOW + 2 * PRUNE_INTERVAL_SECONDS))
        self.assertEqual(storage.load_json(self.state/retention.STATE_NAME)['error'], 'busy')


class ScheduledPruneTests(unittest.TestCase):
    def test_managed_monitor_prunes_old_generations_but_not_the_active_one(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)/'state'
            database, exports = create_guard_database(directory)
            with patch.object(checkpoint, 'current_database', return_value=database), \
                    patch('macbook.bettertouchtool.btt_guard.database.current_database', return_value=database), \
                    patch.object(checkpoint.api, 'export_graph', side_effect=lambda _: deepcopy(exports)), \
                    patch.object(checkpoint, 'datetime') as clock, \
                    patch.object(edit_acceptance, 'running_btt', return_value=None):
                clock.now.return_value = datetime(2026, 6, 1, tzinfo=timezone.utc)
                old = checkpoint.capture(state, accept_current=True)['checkpoint_id']
                clock.now.return_value = datetime(2026, 10, 1, tzinfo=timezone.utc)
                active = checkpoint.capture(state, accept_current=True, replace=True)['checkpoint_id']
                with patch.object(cli.time, 'time', return_value=NOW):
                    first = cli.run_monitor(state, managed=True)
                    manual = cli.run_monitor(state, managed=False)
            self.assertEqual(first['pruned'], [f'checkpoints/{old}'])
            self.assertIsNone(manual['pruned'])
            self.assertEqual(sorted(p.name for p in (state/'checkpoints').iterdir()), [active])
            self.assertEqual(first['audit_status'], 'healthy')


if __name__ == '__main__':
    unittest.main()
