from pathlib import Path
import tempfile
import unittest

from cron_schedule import describe_schedule
from crontab_adapter import CrontabAdapter, parse_crontab
from cli_runner import CommandResult
from panel_contracts import ApiError, OwnerKind
from panel_test_support import FakeRunner


class CrontabTests(unittest.TestCase):
    def test_plain_words(self):
        cases = {'* * * * *': 'Every minute', '*/5 * * * *': 'Every 5 minutes',
                 '7 * * * *': 'Hourly at :07', '30 3 * * *': 'Daily at 03:30',
                 '0 8 * * 1-5': 'Weekly on Monday–Friday at 08:00',
                 '0 8 * * 0,6': 'Weekly on Sunday, Saturday at 08:00',
                 '0 8 * * mon,fri': 'Weekly on Monday, Friday at 08:00',
                 '0 8 1,3-5 * *': 'Monthly on day 1, 3–5 at 08:00',
                 '@reboot': 'At cron startup (usually boot)', '@hourly': 'Hourly at :00',
                 '*/99 * * * *': '*/99 * * * *', '0 3 1 2 *': '0 3 1 2 *'}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(describe_schedule(raw), expected)

    def test_ownership_drift_and_expanded_log_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            log = home / 'status.log'
            log.write_text('test')
            tracked = f'scripts={home}\n# Tracked cleanup\n0 3 * * * cleanup >> "$scripts/status.log" 2>&1\n'
            live = tracked + '\n# BEGIN managed llm-restore\n# Snapshot\n* * * * * snapshot > ${HOME}/status.log\n# END managed llm-restore\n# Local job\n@hourly extra >> ~/status.log\n'
            value = parse_crontab(live, tracked, home)
            self.assertFalse(value['in_sync'])
            self.assertEqual(value['repo_only'], [])
            self.assertEqual(value['jobs'][0]['owner_kind'], OwnerKind.TRACKED)
            self.assertEqual(value['jobs'][0]['description'], 'Tracked cleanup')
            self.assertEqual(value['jobs'][1]['owner_kind'], OwnerKind.MANAGED)
            self.assertIn('shared/sh/llm_restore.py --cron', value['jobs'][1]['owner'])
            self.assertEqual(value['jobs'][2]['owner_kind'], OwnerKind.LIVE)
            for job in value['jobs']:
                self.assertEqual(job['log_path'], str(log))
                self.assertIsInstance(job['log_modified'], float)
            self.assertTrue(parse_crontab(tracked + '\n  \n', tracked.rstrip() + '  \n', home)['in_sync'])

    def test_reordered_assignments_are_drift_not_in_sync(self):
        value = parse_crontab('NAME=first\nNAME=second\n', 'NAME=second\nNAME=first\n')
        self.assertFalse(value['in_sync'])
        self.assertTrue(value['order_changed'])
        self.assertEqual(value['live_only'], [])
        self.assertEqual(value['repo_only'], [])
        job = parse_crontab('  @hourly job\n', '')['jobs'][0]
        self.assertEqual(job['schedule'], 'Hourly at :00')

    def test_unresolved_or_secret_paths_are_not_inspected(self):
        live = '* * * * * job >> $UNKNOWN/out.log\n* * * * * job > /x/pi/secrets/private\n'
        self.assertEqual([j['log_path'] for j in parse_crontab(live, '')['jobs']], [None, None])

    def test_cli_no_crontab_is_empty_but_other_errors_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            tracked = Path(directory) / 'crontab'
            tracked.write_text('')
            runner = FakeRunner([CommandResult(1, stderr='crontab: no crontab for fixture')])
            self.assertEqual(CrontabAdapter(runner, tracked).read()['jobs'], [])
            runner = FakeRunner([CommandResult(1, stderr='permission denied')])
            with self.assertRaises(ApiError):
                CrontabAdapter(runner, tracked).read()


if __name__ == '__main__':
    unittest.main()
