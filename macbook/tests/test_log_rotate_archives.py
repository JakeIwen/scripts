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

    def adopt(self, path):
        revision = self.inventory()['revision']
        code, value, _ = self.run_cli('adopt', '--revision', revision, path, '--json')
        self.assertEqual(code, 0, value)
        return value

    def test_shift_and_directory_sync_writers_are_archived_without_losing_generations(self):
        for compress in (True, False):
            with self.subTest(compress=compress):
                path = self.make_log(compress)
                suffix = '.gz' if compress else ''
                generations = [self.read(path)]
                self.rotate(path)
                real_publish, real_sync = archives.publish, copytruncate._sync_directory
                for number in range(2, 4):
                    data, late = payload('generation{}'.format(number)), bytearray()
                    Path(path).write_bytes(data)
                    def append(data):
                        with open(path, 'ab') as output:
                            output.write(data)
                        late.extend(data)
                    def publish(source, destination):
                        real_publish(source, destination)
                        append(b'during publication\n')
                    def sync(directory):
                        real_sync(directory)
                        append(b'during directory sync\n')
                    with mock.patch.object(archives, 'publish', side_effect=publish), \
                            mock.patch.object(copytruncate, '_sync_directory', side_effect=sync):
                        self.rotate(path)
                    generations.append(data + late)
                    self.assertEqual(os.path.getsize(path), 0)
                for index, expected in enumerate(reversed(generations), 1):
                    archive = path + '.{}'.format(index) + suffix
                    with (gzip.open(archive, 'rb') if compress else open(archive, 'rb')) as source:
                        self.assertEqual(source.read(), expected)
                    archives.require_owned(archive, state.read_json(self.home, state.ARCHIVES_FILE))
                # The next subtest uses a clean archive namespace, not an untracked prior format.
                for index in range(1, 4):
                    os.unlink(path + '.{}'.format(index) + suffix)

    def test_final_pre_shift_check_refuses_new_hard_link_without_touching_archives(self):
        path = self.make_log()
        self.rotate(path)
        original_archive = self.read(path + '.1.gz')
        original = payload('second')
        Path(path).write_bytes(original)
        real_write = copytruncate._write_archive
        def link_after_copy(*args):
            copied = real_write(*args)
            os.link(path, self.path('alias'))
            return copied
        with mock.patch.object(copytruncate, '_write_archive', side_effect=link_after_copy):
            code, value, _ = self.run_cli('rotate', '--only', path, '--json')
        self.assertEqual(code, 1)
        self.assertIn(eligibility.HARD_LINK_NOTE, value['errors'][0]['error'])
        self.assertEqual(os.stat(path).st_nlink, 2)
        self.assertEqual(self.read(path), original)
        self.assertEqual(self.read(path + '.1.gz'), original_archive)
        self.assertFalse(Path(path + '.2.gz').exists())

    def test_failed_prepublication_record_does_not_create_a_foreign_archive(self):
        path = self.make_log()
        self.rotate(path)
        Path(path).write_bytes(payload('second'))
        before = self.read(path + '.1.gz')
        real_write = state.write_json
        def fail_archive_record(home, name, value):
            if name == state.ARCHIVES_FILE:
                raise OSError(28, 'fixture record failure')
            return real_write(home, name, value)
        with mock.patch.object(state, 'write_json', side_effect=fail_archive_record):
            code, _, _ = self.run_cli('rotate', '--only', path, '--json')
        self.assertEqual(code, 1)
        self.assertEqual(self.read(path + '.1.gz'), before)
        self.assertFalse(Path(path + '.2.gz').exists())
        self.assertEqual(self.entry(path)['state'], 'due')
        self.rotate(path)
        self.assertEqual(self.read(path + '.2.gz'), before)

    def test_interrupted_shift_keeps_destination_recorded_and_prunes_old_source(self):
        path = self.make_log()
        self.rotate(path)
        original = self.read(path + '.1.gz')
        Path(path).write_bytes(payload('second'))
        real_write, real_publish = state.write_json, archives.publish
        published = []
        def publish(source, destination):
            # Read the durable record at the actual filesystem publication boundary.
            recorded = state.read_json(self.home, state.ARCHIVES_FILE)
            self.assertEqual(recorded[destination], {**archives.identity(source), 'path': destination})
            real_publish(source, destination)
            published.append(destination)
        def fail_after_unlink(home, name, value):
            if name == state.ARCHIVES_FILE and published:
                raise OSError(28, 'fixture source-record removal failure')
            return real_write(home, name, value)
        with mock.patch.object(archives, 'publish', side_effect=publish), \
                mock.patch.object(state, 'write_json', side_effect=fail_after_unlink):
            code, _, _ = self.run_cli('rotate', '--only', path, '--json')
        self.assertEqual(code, 1)
        recorded = state.read_json(self.home, state.ARCHIVES_FILE)
        self.assertEqual(set(recorded), {path + '.1.gz', path + '.2.gz'})
        self.assertFalse(Path(path + '.1.gz').exists())
        self.assertEqual(self.read(path + '.2.gz'), original)
        self.rotate(path)
        self.assertEqual(self.read(path + '.3.gz'), original)
        self.assertEqual(set(state.read_json(self.home, state.ARCHIVES_FILE)),
                         {path + '.1.gz', path + '.3.gz'})

    def test_failed_postpublication_size_record_can_be_adopted(self):
        path = self.make_log()
        original = self.read(path)
        real_write, real_sync = state.write_json, copytruncate._sync_directory
        writes = []
        def fail_final_record(home, name, value):
            if name == state.ARCHIVES_FILE:
                writes.append(1)
                if len(writes) == 2:
                    raise OSError(28, 'fixture record failure')
            return real_write(home, name, value)
        def append_during_sync(directory):
            real_sync(directory)
            with open(path, 'ab') as output:
                output.write(b'late\n')
        with mock.patch.object(state, 'write_json', side_effect=fail_final_record), \
                mock.patch.object(copytruncate, '_sync_directory', side_effect=append_during_sync):
            code, _, _ = self.run_cli('rotate', '--only', path, '--json')
        self.assertEqual(code, 1)
        self.assertEqual(self.read(path), original + b'late\n')
        self.assertEqual(self.entry(path)['note'], archives.UNRECORDED_NOTE)
        self.assertNotEqual(self.adopt(path)['log']['state'], 'refused')
        self.rotate(path)
        with gzip.open(path + '.2.gz', 'rb') as source:
            self.assertEqual(source.read(), original + b'late\n')

    def test_missing_and_corrupt_records_require_explicit_revision_checked_adoption(self):
        path = self.make_log()
        self.rotate(path)
        archive = path + '.1.gz'
        for mode in ('missing', 'corrupt'):
            with self.subTest(mode=mode):
                ledger_path = state.state_dir(self.home) / state.ARCHIVES_FILE
                if mode == 'missing':
                    ledger_path.unlink()
                else:
                    ledger_path.write_text('{broken')
                before = self.read(archive)
                snapshot = self.inventory()
                entry = self.entry(path, snapshot)
                self.assertEqual((entry['state'], entry['note']), ('refused', archives.UNRECORDED_NOTE))
                self.assertTrue(any(problem.get('path') == path for problem in snapshot['problems']))
                code, _, _ = self.run_cli('adopt', '--revision', 'stale', path, '--json')
                self.assertEqual(code, 3)
                self.assertEqual(state.read_json(self.home, state.ARCHIVES_FILE), {})
                self.adopt(path)
                self.assertEqual(self.read(archive), before)
                archives.require_owned(archive, state.read_json(self.home, state.ARCHIVES_FILE))
        Path(path).write_bytes(payload('second'))
        self.rotate(path)
        self.assertTrue(Path(path + '.2.gz').exists())

    def test_adoption_refuses_symlinks_hard_links_and_excluded_logs(self):
        path = self.make_log()
        target = self.write('unrelated', b'not an archive')
        archive = path + '.1'
        for create in (lambda: os.symlink(target, archive), lambda: os.link(target, archive)):
            create()
            code, _, _ = self.run_cli('adopt', '--revision', self.inventory()['revision'], path, '--json')
            self.assertEqual(code, 1)
            self.assertEqual(state.read_json(self.home, state.ARCHIVES_FILE), {})
            os.unlink(archive)
        forbidden = self.write('.claude/secret.log')
        code, _, _ = self.run_cli('adopt', '--revision', self.inventory()['revision'], forbidden, '--json')
        self.assertEqual(code, 1)
        self.assertEqual(self.read(target), b'not an archive')

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

    def test_run_start_prunes_absent_records_even_when_no_log_rotates(self):
        path = self.make_log()
        self.rotate(path)
        archive = path + '.1.gz'
        os.unlink(archive)
        self.run_cli('rotate', '--json')
        self.assertEqual(state.read_json(self.home, state.ARCHIVES_FILE), {})
