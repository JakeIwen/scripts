from datetime import datetime
import os
import time
import unittest
from unittest.mock import patch

from cron_schedule import next_runs
from crontab_adapter import display_job
from test_crontab_adapter import status_payload


def epoch(value):
    return datetime.fromisoformat(value).timestamp()


class ScheduleTests(unittest.TestCase):
    def test_lists_ranges_steps_and_names(self):
        start, end = epoch('2026-11-01'), epoch('2026-11-08')
        self.assertEqual(next_runs('0,30 8-10/2 * nov mon-fri', start, end),
                         [epoch(f'2026-11-{day:02}T{hour:02}:{minute:02}')
                          for day in range(2, 7) for hour in (8, 10) for minute in (0, 30)])
        self.assertEqual(next_runs('0 0 * * 0', start, end), [start])
        self.assertEqual(next_runs('0 0 * * 7', start, end), [start])
        self.assertEqual(next_runs(['*/15', '*', '*', '*', '*'], start + 1, start + 3600),
                         [start + 900, start + 1800, start + 2700])

    def test_macros_boundaries_and_caps(self):
        start, end = epoch('2026-01-01'), epoch('2027-01-01')
        self.assertEqual(next_runs('@yearly', start, end), [start])
        self.assertEqual(next_runs('@annually', start, end), [start])
        self.assertEqual(len(next_runs('@monthly', start, end)), 12)
        self.assertEqual(len(next_runs('@daily', start, start + 86400 * 2)), 2)
        self.assertEqual(next_runs('@midnight', start, end, limit=1), [start])
        self.assertEqual(next_runs('@reboot', start, end), [])
        self.assertEqual(len(next_runs('* * * * *', start, end)), 500)
        self.assertEqual(len(next_runs('@hourly', start, end, 3)), 3)

    def test_day_fields_are_alternatives_unless_wildcard(self):
        start, end = epoch('2026-11-01'), epoch('2026-11-08')
        self.assertEqual(next_runs('0 0 1 * mon', start, end), [start, epoch('2026-11-02')])
        self.assertEqual(next_runs('0 0 * * mon', start, end), [epoch('2026-11-02')])
        self.assertEqual(next_runs('0 0 */2 * mon', start, end), [])

    def test_invalid_and_impossible_expressions_have_no_events(self):
        start, end = epoch('2026-01-01'), epoch('2027-01-01')
        for expression in ('nope', '@unknown', '*/0 * * * *', '60 * * * *',
                           '0 0 30 feb *', '0 0 * * mon-nope', '0 0 * 13 *'):
            self.assertEqual(next_runs(expression, start, end), [])

    @unittest.skipUnless(hasattr(time, 'tzset'), 'Local timezone switching requires tzset')
    def test_dst_gap_uses_real_minutes(self):
        try:
            with patch.dict(os.environ, {'TZ': 'America/New_York'}):
                time.tzset()
                self.assertEqual(next_runs('30 2 * * *', epoch('2026-03-08'), epoch('2026-03-09')), [])
                result = next_runs('30 1 * * *', epoch('2026-11-01'), epoch('2026-11-02'))
                self.assertEqual(len(result), 2)
                self.assertEqual(result[1] - result[0], 3600)
        finally:
            time.tzset()

    def test_adapter_projects_next_week_but_not_paused_jobs(self):
        job = status_payload()['jobs'][0]
        start = epoch('2026-11-02T00:00:00')
        projected = display_job(job, start)['next_runs']
        self.assertEqual(len(projected), 7)
        self.assertEqual(projected[0], start + 3 * 3600)
        job['paused'] = True
        self.assertEqual(display_job(job, start)['next_runs'], [])
