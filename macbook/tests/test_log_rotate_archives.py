"""Archive recovery and late writers against real files in an isolated HOME."""
import gzip
import os
from pathlib import Path
import types
from unittest import mock

from log_rotate import archives, copytruncate, eligibility, state
from log_rotate.records import LogRotateError
from test_log_rotate import LogRotateCase, payload


class ArchiveRecoveryTests(LogRotateCase):
    def make_log(self, compress=True):
        path = self.write('.local/state/app/job.log', payload('first'))
        self.set_policy(path, '--max-mb', '1', '--keep', '3', '--compress', 'on' if compress else 'off')
        return path

    def rotate(self, path):
        code, value, _ = self.run_cli('rotate', '--only', path, '--json')
        self.assertEqual(code, 0, value)
        self.assertEqual(len(value['rotated']), 1, value)
        return value

    def test_device_number_changes_do_not_invalidate_provenance_but_birthtime_changes_do(self):
        path = self.make_log()
        self.rotate(path)
        archive = path + '.1.gz'
        ledger = state.read_json(self.home, state.ARCHIVES_FILE)
        ledger[archive]['st_dev'] += 1
        state.write_json(self.home, state.ARCHIVES_FILE, ledger)
        self.assertEqual(self.entry(path)['state'], 'ok')
        info = os.lstat(archive)
        changed = types.SimpleNamespace(st_mode=info.st_mode, st_dev=info.st_dev, st_ino=info.st_ino,
                                        st_size=info.st_size, st_birthtime=12345)
        with mock.patch.object(archives.os, 'lstat', return_value=changed):
            with self.assertRaises(LogRotateError):
                archives.require_owned(archive, ledger)
        Path(path).write_bytes(payload('second'))
        self.rotate(path)
