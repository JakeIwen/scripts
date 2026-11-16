"""Advisory orphan-cleanup counts: episodes, throttled writes and status text."""
import unittest

import icloud_status as status
from icloud_progress import RETENTION_PROGRESS_WRITE_SECONDS, RetentionProgress

GEN = 'tm-20261109T003819Z-4ec13bed'


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class RetentionProgressTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.saves, self.state = Clock(), [], {}

    def recorder(self, state=None, keep=2):
        return RetentionProgress(self.state if state is None else state, GEN, keep, lambda: self.saves.append(1),
                                 log=lambda *a, **k: None, clock=self.clock, monotonic=self.clock)

    def test_deletions_are_written_at_most_once_per_interval_but_steps_at_once(self):
        progress = self.recorder()
        progress.listing()
        progress.listed(10, 4, 4000)
        steps = len(self.saves)
        for _ in range(3):
            self.clock.now += 1
            progress.deleted(1000)
        self.assertEqual(len(self.saves), steps)
        self.clock.now += RETENTION_PROGRESS_WRITE_SECONDS
        progress.deleted(1000)
        self.assertEqual(len(self.saves), steps + 1)
        self.assertEqual((self.state['retention_progress']['deleted_objects'],
                          self.state['retention_progress']['remaining_bytes']), (4, 0))
        progress.local(); progress.finished()
        self.assertEqual(len(self.saves), steps + 3)

    def test_listing_counter_and_duration_are_recorded(self):
        progress = self.recorder()
        progress.listing()
        self.clock.now += 31
        progress.tick({'listed': 9800})
        self.assertEqual(self.state['retention_progress']['listed_objects'], 9800)
        self.clock.now += 50
        progress.listed(15155, 1943, 100 * 1024**3)
        self.assertEqual(self.state['retention_progress']['listing_seconds'], 81.0)

    def test_episode_identity_is_generation_and_keep_count(self):
        self.recorder().listing()
        first = dict(self.state['retention_progress'])
        self.clock.now += 5
        self.recorder()
        self.assertEqual(self.state['retention_progress']['episode_started_at'], first['episode_started_at'])
        self.recorder(keep=3)
        self.assertEqual(self.state['retention_progress']['episode_started_at'], 1005.0)


class RetentionMessageTests(unittest.TestCase):
    def test_messages_and_fallbacks(self):
        record = {'generation': GEN, 'step': 'deleting', 'planned_objects': 1943, 'deleted_objects': 412,
                  'remaining_bytes': 63 * 1024**3}
        self.assertEqual(status.retention_message(record, GEN),
                         'Deleting unused cloud files: 412 / 1,943 (63.0 GiB left).')
        self.assertEqual(status.retention_message({'generation': GEN, 'step': 'listing', 'listed_objects': 9800}, GEN),
                         'Listing cloud files before cleanup: 9,800 so far.')
        self.assertEqual(status.retention_message({'generation': GEN, 'step': 'listing'}, GEN),
                         'Listing cloud files before cleanup.')
        default = status.reason({'phase': 'retention'})
        for bad in ({**record, 'generation': 'tm-20261026T083737Z-66cda1e2'}, {**record, 'deleted_objects': -1},
                    {**record, 'planned_objects': 'many'}, 'deleting', None):
            self.assertEqual(status.reason({'phase': 'retention', 'last_generation': GEN,
                                            'retention_progress': bad}), default, bad)
        self.assertEqual(status.reason({'phase': 'retention', 'retention_progress': record}), default)
