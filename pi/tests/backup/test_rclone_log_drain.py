"""rclone completion records: per-command matching and the drain after a client is reaped."""
import json
import tempfile
import unittest

import icloud_backup as backup
from icloud_progress import RcloneStats
from pi.tests.backup.test_icloud_uplink_hold_worker import LOST, OK, Child, WorkerHoldFixture

NAME = 'a' * 64


class UnlistedDeletion(RuntimeError):
    """A caller's tripwire, raised from on_completed."""


def record(msg, name=NAME, level='info', **extra):
    return json.dumps({'level': level, 'msg': msg, 'object': name, **extra}) + '\n'


class CompletionRecordTests(unittest.TestCase):
    def parse(self, operation, text):
        events = []
        with tempfile.TemporaryFile() as log:
            log.write(text.encode()); log.flush()
            RcloneStats(events.append, operation).drain(log.fileno())
        return events

    def test_delete_reports_only_info_deleted_records(self):
        text = (record('Deleted') + record('Deleted', 'b' * 64, level='error')
                + record('Skipped delete as --dry-run is set (size 1)', 'c' * 64, level='notice', skipped='delete')
                + record('Copied (new)', 'd' * 64))
        self.assertEqual(self.parse('delete', text), [NAME])
        self.assertEqual(self.parse('copy', text), ['d' * 64])

    def test_drain_reads_past_one_read_and_drops_a_cut_off_fragment(self):
        padding = json.dumps({'stats': {'bytes': 1}, 'msg': 'x' * 1000}) + '\n'
        text = padding * 1600 + record('Deleted') + record('Deleted', 'b' * 64)[:40]
        self.assertGreater(len(text), 1024 * 1024)
        self.assertEqual(self.parse('delete', text), [NAME])


class WritingChild(Child):
    """A client that writes `records` to its stderr when started and `on_stop` when stopped."""

    def __init__(self, rc=None, records='', on_stop=''):
        super().__init__(rc)
        self.records, self.on_stop, self.stderr = records, on_stop, None

    def started(self, stderr):
        self.stderr = stderr
        stderr.write(self.records); stderr.flush()
        return self

    def wait(self, timeout=None):
        if self.returncode is None and self.on_stop:
            self.stderr.write(self.on_stop); self.stderr.flush()
        return super().wait(timeout)


class TerminalDrainTests(WorkerHoldFixture):
    def run_children(self, children, operation, **kwargs):
        pending = iter(children)
        return self.run_with(lambda *args, **popen: next(pending).started(popen['stderr']), operation, **kwargs)

    def test_a_record_written_just_before_exit_still_reaches_the_callback(self):
        self.verdicts = [OK]
        for operation, message in (('delete', 'Deleted'), ('copy', 'Copied (new)')):
            with self.subTest(operation):
                events = []
                self.assertEqual(self.run_children([WritingChild(0, record(message))], operation,
                                                   on_completed=events.append), '')
                self.assertEqual(events, [NAME])

    def test_an_unlisted_record_read_after_a_clean_exit_still_fails(self):
        self.verdicts = [OK]
        def tripwire(name):
            raise UnlistedDeletion('unlisted')
        with self.assertRaises(UnlistedDeletion):
            self.run_children([WritingChild(0, record('Deleted'))], 'delete', on_completed=tripwire)

    def test_records_written_while_stopping_arrive_before_the_restart(self):
        self.verdicts = [OK, LOST, OK]
        events = []
        first = WritingChild(on_stop=record('Deleted'))
        self.assertEqual(self.run_children([first, WritingChild(0)], 'delete',
                                           on_completed=events.append), '')
        self.assertEqual((events, self.popen.call_count), ([NAME], 2))

    def test_an_unlisted_record_written_while_stopping_is_never_held_or_restarted(self):
        self.verdicts = [OK, LOST, OK]
        def tripwire(name):
            raise UnlistedDeletion('unlisted')
        with self.assertRaises(UnlistedDeletion):
            self.run_children([WritingChild(on_stop=record('Deleted')), WritingChild(0)], 'delete',
                              on_completed=tripwire)
        self.popen.assert_called_once()
        self.assertEqual(self.reports, [])

    def test_a_restart_runs_the_mapped_argv_and_keeps_failure_meaning(self):
        self.verdicts = [OK, LOST, OK]
        restart = lambda args: [*args[:2], 'remote:objects', '--max-delete', '1']
        self.assertEqual(self.run_children([WritingChild(), WritingChild(0)], 'delete', restart_args=restart), '')
        first, second = (call.args[0] for call in self.popen.call_args_list)
        self.assertNotIn('--max-delete', first)
        self.assertEqual(second[-3:], ['remote:objects', '--max-delete', '1'])
        self.verdicts = [OK, LOST, OK]
        with self.assertRaisesRegex(RuntimeError, r'^rclone delete failed \(exit 7\)$'):
            self.run_children([WritingChild(), WritingChild(7)], 'delete', restart_args=restart)


if __name__ == '__main__':
    unittest.main()
