"""Isolated priority state and deterministic clocks for backup regressions."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import backup_priority as priority
from time_machine_store import atomic_json

GEN = 'tm-20260930T010203Z-12345678'
FIXTURE_NOW = 2_000_000_000.0


class PriorityFixture(unittest.TestCase):
    def setUp(self):
        # Whole seconds keep filesystem mtime boundary assertions exact as well.
        self.now = FIXTURE_NOW
        self.clock = lambda: self.now
        clock = mock.patch.object(time, 'time', self.clock)
        clock.start(); self.addCleanup(clock.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.directory = self.root / 'priority'
        self.jobs = {}
        for mode, job in priority.JOBS.items():
            directory = self.root / mode.value
            directory.mkdir(mode=0o700)
            cfg = self.root / (mode.value + '.json')
            cfg.write_text('{"interval_days":7}')
            self.jobs[mode] = priority.CloudJob(job.label, job.service, directory, cfg)
            atomic_json(directory / 'state.json', {'pending': GEN, 'progress': {'upload_total_bytes': 100}})
        for name, value in (('DIRECTORY', self.directory), ('JOBS', self.jobs),
                            ('BORG_STAMP', self.root / 'borg_ok')):
            patch = mock.patch.object(priority, name, value)
            patch.start(); self.addCleanup(patch.stop)
