"""Archive recovery and late writers against real files in an isolated HOME."""
import gzip
import os
import subprocess
from pathlib import Path
import types
from unittest import mock

from log_rotate import archives, copytruncate, eligibility, state
from log_rotate.records import MB, LogRotateError
from test_log_rotate import PYTHON, REPOSITORY, LogRotateCase, payload


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
        self.assertEqual(self.read(path + '.2.gz'), original)
        self.assertEqual(set(state.read_json(self.home, state.ARCHIVES_FILE)),
                         {path + '.1.gz', path + '.2.gz'})

    def test_failed_postpublication_size_record_resumes_without_adoption(self):
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
        self.assertEqual(self.entry(path)['state'], 'due')
        self.rotate(path)
        with gzip.open(path + '.1.gz', 'rb') as source:
            self.assertEqual(source.read(), original + b'late\n')
        self.assertFalse(Path(path + '.2.gz').exists())

    def test_missing_records_require_explicit_revision_checked_adoption(self):
        path = self.make_log()
        self.rotate(path)
        archive = path + '.1.gz'
        (state.state_dir(self.home) / state.ARCHIVES_FILE).unlink()
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

    def test_corrupt_record_blocks_all_rotation_and_adoption_without_rewriting_it(self):
        path = self.make_log()
        other = self.write('.local/state/other/job.log', payload('other'))
        self.set_policy(other, '--max-mb', '1')
        self.rotate(path)
        self.rotate(other)
        fresh = self.write('.local/state/fresh/job.log', payload('fresh'))
        self.set_policy(fresh, '--max-mb', '1')
        ledger_path = state.state_dir(self.home) / state.ARCHIVES_FILE
        saved = ledger_path.read_bytes()
        for corrupt in (b'{broken', b'[]', b'null', b'\xff'):
            with self.subTest(corrupt=corrupt):
                ledger_path.write_bytes(corrupt)
                with self.assertRaises(LogRotateError):
                    state.read_json(self.home, state.ARCHIVES_FILE)
                snapshot = self.inventory()
                for log in (path, other):
                    entry = self.entry(log, snapshot)
                    self.assertEqual((entry['state'], entry['note'], entry['reason']),
                                     ('refused', state.ARCHIVES_UNREADABLE_NOTE, 'archive_record_unreadable'))
                for args in (('rotate',), ('rotate', '--only', fresh, '--force'),
                             ('adopt', '--revision', snapshot['revision'], path)):
                    code, value, _ = self.run_cli(*args, '--json')
                    self.assertEqual((code, value['error']), (1, state.ARCHIVES_UNREADABLE_NOTE))
                    self.assertEqual(ledger_path.read_bytes(), corrupt)
                self.assertEqual(self.read(fresh), payload('fresh'))
        ledger_path.write_bytes(saved)  # A repaired record preserves both logs' ownership.
        self.rotate(fresh)
        for log in (path, other, fresh):
            archives.require_owned(log + '.1.gz', state.read_json(self.home, state.ARCHIVES_FILE))

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


    def test_failed_late_member_is_rolled_back_to_the_recorded_archive_size(self):
        path = self.make_log()
        original, late = self.read(path), b'late append\n' * 1000
        real_sync, real_copy = copytruncate._sync_directory, copytruncate._copy_pass
        def sync(directory):
            real_sync(directory)
            with open(path, 'ab') as writer:
                writer.write(late)
        def fail_late(source, output, start, end, compress):
            if start:
                with gzip.GzipFile(fileobj=output, mode='wb', mtime=0) as member:
                    member.write(os.pread(source, 100, start))
                    member.flush()
                    output.flush()
                    raise OSError(28, 'fixture partial-member failure')
            return real_copy(source, output, start, end, compress)
        with mock.patch.object(copytruncate, '_sync_directory', side_effect=sync), \
                mock.patch.object(copytruncate, '_copy_pass', side_effect=fail_late):
            code, value, _ = self.run_cli('rotate', '--only', path, '--json')
        self.assertEqual(code, 1)
        self.assertIn('fixture partial-member failure', value['errors'][0]['error'])
        self.assertEqual(self.read(path), original + late)
        archive = path + '.1.gz'
        archives.require_owned(archive, state.read_json(self.home, state.ARCHIVES_FILE))
        result = subprocess.run(['/usr/bin/gunzip', '-t', archive], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        with gzip.open(archive, 'rb') as source:
            self.assertEqual(source.read(), original)
        self.rotate(path)

    def test_record_write_appends_are_copied_once_more_before_immediate_truncation(self):
        for compress in (True, False):
            with self.subTest(compress=compress):
                path = self.make_log(compress)
                original, writes = self.read(path), []
                real_write = state.write_json
                def append_during_record(home, name, value):
                    real_write(home, name, value)
                    if name == state.ARCHIVES_FILE:
                        data = b'during record %d\n' % (len(writes) + 1)
                        writes.append(data)
                        with open(path, 'ab') as output:
                            output.write(data)
                with mock.patch.object(state, 'write_json', side_effect=append_during_record):
                    self.rotate(path)
                self.assertEqual(len(writes), 3)  # Publication, final record, post-truncate size.
                archive = path + ('.1.gz' if compress else '.1')
                with (gzip.open(archive, 'rb') if compress else open(archive, 'rb')) as source:
                    self.assertEqual(source.read(), original + b''.join(writes[:2]))
                self.assertEqual(self.read(path), writes[2])
                archives.require_owned(archive, state.read_json(self.home, state.ARCHIVES_FILE))
                os.unlink(archive)
                archives.prune(self.home)

    def test_final_record_uses_the_open_archive_even_if_its_path_is_replaced(self):
        path = self.make_log()
        real_sync = copytruncate._sync_directory
        saved = path + '.saved'
        def replace_archive(directory):
            real_sync(directory)
            os.rename(path + '.1.gz', saved)
            Path(path + '.1.gz').write_bytes(b'foreign replacement')
        with mock.patch.object(copytruncate, '_sync_directory', side_effect=replace_archive):
            self.rotate(path)
        record = state.read_json(self.home, state.ARCHIVES_FILE)[path + '.1.gz']
        self.assertEqual(record, {**archives.identity(saved), 'path': path + '.1.gz'})
        self.assertEqual(self.read(path + '.1.gz'), b'foreign replacement')
        self.assertEqual(self.entry(path)['reason'], 'unrecorded_archives')

    def test_interrupted_shift_with_keep_three_retains_the_two_newest_survivors(self):
        path = self.make_log()
        generations = [self.read(path)]
        self.rotate(path)
        for number in range(2, 4):
            generations.append(payload(str(number)))
            Path(path).write_bytes(generations[-1])
            self.rotate(path)
        Path(path).write_bytes(payload('four'))
        real_publish = archives.publish
        def interrupted(source, destination):
            real_publish(source, destination)
            raise OSError(28, 'fixture interrupted shift')
        with mock.patch.object(archives, 'publish', side_effect=interrupted):
            self.assertEqual(self.run_cli('rotate', '--only', path, '--json')[0], 1)
        self.assertFalse(Path(path + '.2.gz').exists())
        self.rotate(path)
        for index, expected in ((1, payload('four')), (2, generations[2]), (3, generations[1])):
            with gzip.open(path + '.%d.gz' % index, 'rb') as source:
                self.assertEqual(source.read(), expected)
            archives.require_owned(path + '.%d.gz' % index, state.read_json(self.home, state.ARCHIVES_FILE))


class InventoryIsolationTests(LogRotateCase):
    def test_unlistable_extra_is_refused_without_blocking_other_logs(self):
        healthy = self.write('.local/state/app/job.log', payload('healthy'))
        self.set_policy(healthy, '--max-mb', '1')
        locked = self.write('locked/job.log', b'locked')
        code, _, _ = self.run_cli('add', '--revision', self.inventory()['revision'], locked, '--json')
        self.assertEqual(code, 0)
        os.chmod(os.path.dirname(locked), 0o300)
        try:
            entry = self.entry(locked)
            self.assertEqual((entry['state'], entry['note'], entry['reason']),
                             ('refused', os.path.dirname(locked), 'unreadable_directory'))
            code, value, _ = self.run_cli('rotate', '--json')
            self.assertEqual(code, 0, value)
            self.assertEqual([item['path'] for item in value['rotated']], [healthy])
            self.assertEqual(os.path.getsize(healthy), 0)
        finally:
            os.chmod(os.path.dirname(locked), 0o700)
        self.assertEqual(self.read(locked), b'locked')

    def test_disabled_logs_keep_their_state_and_note_beside_app_owned_archives(self):
        third = self.write('Library/Logs/SomeApp/app.log', payload('third'))
        disabled = self.write('.local/state/app/disabled.log', payload('disabled'))
        self.set_policy(disabled, '--enabled', 'off')
        for path in (third, disabled):
            Path(path + '.1').write_bytes(b'app owned archive')
        snapshot = self.inventory()
        for path in (third, disabled):
            self.assertEqual(self.entry(path, snapshot)['state'], 'disabled')
            self.assertEqual(self.entry(path, snapshot)['archives'], 1)
        third_entry = self.entry(third, snapshot)
        self.assertEqual((third_entry['note'], third_entry['reason']),
                         ('third-party app log; enable per file', 'third_party'))
        self.assertNotIn('note', self.entry(disabled, snapshot))
        self.assertEqual(snapshot['problems'], [])
        code, value, _ = self.run_cli('rotate', '--json')
        self.assertEqual((code, value['rotated']), (0, []))
        for path in (third, disabled):
            self.assertEqual(self.read(path + '.1'), b'app owned archive')
            self.assertEqual(os.path.getsize(path), len(payload('third')))

    def test_enabled_oversized_log_waiting_for_adoption_escalates_to_error(self):
        path = self.write('.local/state/app/job.log', payload('large', 3 * MB))
        self.set_policy(path, '--max-mb', '1')
        Path(path + '.1').write_bytes(b'unrecorded')
        snapshot = self.inventory()
        self.assertEqual(self.entry(path, snapshot)['reason'], 'unrecorded_archives')
        self.assertEqual([problem['level'] for problem in snapshot['problems']], ['warn', 'error'])
        self.assertIn('rotation is failing', snapshot['problems'][1]['text'])


    def test_unsearchable_scan_folder_does_not_block_inventory_status_or_rotation(self):
        healthy = self.write('.local/state/app/job.log', payload('healthy'))
        self.set_policy(healthy, '--max-mb', '1')
        locked = self.write('Library/Logs/Vendor/job.log', b'locked')
        self.run_cli('add', '--revision', self.inventory()['revision'], locked, '--json')
        directory = os.path.dirname(locked)
        os.chmod(directory, 0o644)
        try:
            entry = self.entry(locked)
            self.assertEqual((entry['state'], entry['note'], entry['reason']),
                             ('refused', directory, 'unreadable_directory'))
            self.assertEqual(self.run_cli('status', '--json')[0], 0)
            code, value, _ = self.run_cli('rotate', '--json')
            self.assertEqual(code, 0, value)
            self.assertEqual([item['path'] for item in value['rotated']], [healthy])
            self.assertEqual(os.path.getsize(healthy), 0)
        finally:
            os.chmod(directory, 0o700)

    def test_unsearchable_recorded_archive_keeps_provenance_and_other_logs_rotate(self):
        locked = self.write('locked/job.log', payload('locked'))
        self.run_cli('add', '--revision', self.inventory()['revision'], locked, '--json')
        self.set_policy(locked, '--max-mb', '1')
        self.assertEqual(self.run_cli('rotate', '--json')[0], 0)
        recorded = state.read_json(self.home, state.ARCHIVES_FILE)[locked + '.1.gz']
        healthy = self.write('.local/state/app/job.log', payload('healthy'))
        self.set_policy(healthy, '--max-mb', '1')
        for mode in (0o600, 0o000):
            with self.subTest(mode=mode):
                Path(healthy).write_bytes(payload('healthy'))
                os.chmod(os.path.dirname(locked), mode)
                try:
                    self.assertEqual(self.entry(locked)['reason'], 'unreadable_directory')
                    self.assertEqual(self.run_cli('status', '--json')[0], 0)
                    code, value, _ = self.run_cli('rotate', '--json')
                    self.assertEqual(code, 0, value)
                    self.assertEqual([item['path'] for item in value['rotated']], [healthy])
                    self.assertEqual(state.read_json(self.home, state.ARCHIVES_FILE)[locked + '.1.gz'], recorded)
                finally:
                    os.chmod(os.path.dirname(locked), 0o700)


class RetryRetentionTests(LogRotateCase):
    make_log = ArchiveRecoveryTests.make_log
    rotate = ArchiveRecoveryTests.rotate
    def full_history(self, compress):
        path = self.make_log(compress)
        for number in range(1, 4):
            Path(path).write_bytes(payload('gen%d' % number))
            self.rotate(path)
        Path(path).write_bytes(payload('gen4'))
        return path

    def assert_retained(self, path, compress, generations=(4, 3, 2)):
        suffix = '.gz' if compress else ''
        inodes = set()
        ledger = state.read_json(self.home, state.ARCHIVES_FILE)
        for position, generation in enumerate(generations, 1):
            archive = path + '.%d%s' % (position, suffix)
            with (gzip.open(archive, 'rb') if compress else open(archive, 'rb')) as source:
                self.assertEqual(source.read(), payload('gen%d' % generation))
            info = os.stat(archive)
            inodes.add((info.st_dev, info.st_ino))
            archives.require_owned(archive, ledger)
            self.assertNotIn('pending_truncate', ledger[archive])
        self.assertEqual(len(inodes), 3)
        self.assertEqual(os.path.getsize(path), 0)
        self.assertEqual(len(list(Path(path).parent.glob(Path(path).name + '.[0-9]*'))), 3)

    def crash_rotation(self, path, stage):
        script = """
import os, sys
from log_rotate import archives, cli
path, stage = sys.argv[1:]
real_publish = archives.publish
def crash(source, destination):
    if stage == 'shift' and source in (path + '.2', path + '.2.gz'):
        os.link(source, destination, follow_symlinks=False)
        os._exit(91)
    real_publish(source, destination)
    if stage == 'published' and destination in (path + '.1', path + '.1.gz'):
        os._exit(91)
archives.publish = crash
cli.main(['rotate', '--only', path, '--json'])
"""
        environment = dict(os.environ, PYTHONPATH=os.pathsep.join(
            str(REPOSITORY / part) for part in ('macbook/scripts', 'shared/python')))
        result = subprocess.run([PYTHON, '-c', script, path, stage], env=environment,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 91, result.stderr)

    def test_retry_after_late_catchup_or_record_failure_keeps_three_generations(self):
        for compress in (True, False):
            for failure in ('catchup', 'record'):
                with self.subTest(compress=compress, failure=failure):
                    path = self.full_history(compress)
                    real_write = state.write_json
                    archive = path + ('.1.gz' if compress else '.1')
                    def fail_record(home, name, value):
                        record = value.get(archive, {})
                        if name == state.ARCHIVES_FILE and 'pending_truncate' in record and Path(archive).exists():
                            raise OSError(28, 'fixture postpublication record failure')
                        return real_write(home, name, value)
                    patcher = (mock.patch.object(copytruncate, '_catch_up', side_effect=OSError(28, 'fixture catchup'))
                               if failure == 'catchup' else mock.patch.object(state, 'write_json', side_effect=fail_record))
                    with patcher:
                        self.assertEqual(self.run_cli('rotate', '--only', path, '--json')[0], 1)
                    self.rotate(path)
                    self.assert_retained(path, compress)
                    for item in Path(path).parent.glob(Path(path).name + '.[0-9]*'):
                        item.unlink()
                    archives.prune(self.home)

    def test_retry_after_crash_at_publication_or_during_shift_keeps_three_generations(self):
        for compress in (True, False):
            for stage in ('published', 'shift'):
                with self.subTest(compress=compress, stage=stage):
                    path = self.full_history(compress)
                    self.crash_rotation(path, stage)
                    self.rotate(path)
                    self.assert_retained(path, compress)
                    for item in Path(path).parent.glob(Path(path).name + '.[0-9]*'):
                        item.unlink()
                    archives.prune(self.home)


    def test_failed_marker_clear_does_not_confuse_regrown_log_with_pending_bytes(self):
        for compress in (True, False):
            with self.subTest(compress=compress):
                path = self.full_history(compress)
                archive = path + ('.1.gz' if compress else '.1')
                real_write = state.write_json
                def fail_clear(home, name, value):
                    if (name == state.ARCHIVES_FILE and archive in value
                            and 'pending_truncate' not in value[archive] and os.path.getsize(path) == 0):
                        raise OSError(28, 'fixture marker clear failure')
                    return real_write(home, name, value)
                with mock.patch.object(state, 'write_json', side_effect=fail_clear):
                    self.assertEqual(self.run_cli('rotate', '--only', path, '--json')[0], 1)
                self.assertEqual(os.path.getsize(path), 0)
                Path(path).write_bytes(payload('gen5'))
                self.rotate(path)
                self.assert_retained(path, compress, (5, 4, 3))
                for item in Path(path).parent.glob(Path(path).name + '.[0-9]*'):
                    item.unlink()
                archives.prune(self.home)


class TemporaryLinkCleanupTests(LogRotateCase):
    make_log = ArchiveRecoveryTests.make_log
    rotate = ArchiveRecoveryTests.rotate
    full_history = RetryRetentionTests.full_history
    assert_retained = RetryRetentionTests.assert_retained

    def test_idle_run_removes_only_temporary_names_linked_to_recorded_archive(self):
        path = self.make_log()
        self.rotate(path)
        archive = path + '.1.gz'
        directory = Path(path).parent
        linked = directory / '.job.log.rotating.interrupted'
        foreign = directory / '.job.log.rotating.foreign'
        symlink = directory / '.job.log.rotating.symlink'
        unrelated = directory / 'saved-link'
        os.link(archive, linked)
        foreign.write_bytes(b'foreign fixture')
        symlink.symlink_to(archive)
        os.link(archive, unrelated)
        self.assertEqual(self.run_cli('rotate', '--json')[0], 0)
        self.assertFalse(linked.exists())
        self.assertTrue(symlink.is_symlink())
        self.assertTrue(unrelated.exists())
        self.assertEqual(foreign.read_bytes(), b'foreign fixture')
        unrelated.unlink()
        self.assertEqual(os.stat(archive).st_nlink, 1)
        self.assertEqual(self.run_cli('adopt', '--revision', self.inventory()['revision'], path, '--json')[0], 0)

    def test_unrecorded_or_replaced_archive_does_not_authorize_temporary_cleanup(self):
        path = self.make_log()
        self.rotate(path)
        archive = path + '.1.gz'
        os.rename(archive, archive + '.saved')
        Path(archive).write_bytes(b'replacement fixture')
        replaced_link = Path(path).parent / '.job.log.rotating.replacement'
        os.link(archive, replaced_link)
        foreign_archive = self.write('elsewhere/manual.log.1', b'unrecorded fixture')
        foreign_link = Path(foreign_archive).parent / '.manual.log.rotating.unrecorded'
        os.link(foreign_archive, foreign_link)
        self.assertEqual(self.run_cli('rotate', '--json')[0], 0)
        self.assertTrue(replaced_link.exists())
        self.assertTrue(foreign_link.exists())
        self.assertEqual(os.stat(archive).st_nlink, 2)
        self.assertEqual(os.stat(foreign_archive).st_nlink, 2)

    def test_sigkill_between_temporary_link_and_unlink_is_cleaned_before_retry_and_adopt(self):
        for compress in (True, False):
            with self.subTest(compress=compress):
                path = self.full_history(compress)
                script = """
import os, sys
from log_rotate import archives, cli
real_publish = archives.publish
def kill(source, destination):
    if os.path.basename(source).startswith('.') and '.rotating.' in os.path.basename(source):
        os.link(source, destination, follow_symlinks=False)
        os.kill(os.getpid(), 9)
    real_publish(source, destination)
archives.publish = kill
cli.main(['rotate', '--only', sys.argv[1], '--json'])
"""
                environment = dict(os.environ, PYTHONPATH=os.pathsep.join(
                    str(REPOSITORY / part) for part in ('macbook/scripts', 'shared/python')))
                result = subprocess.run([PYTHON, '-c', script, path], env=environment,
                                        capture_output=True, timeout=10)
                self.assertEqual(result.returncode, -9, result.stderr)
                directory = Path(path).parent
                self.assertEqual(len(list(directory.glob('.job.log.rotating.*'))), 1)
                self.rotate(path)
                self.assert_retained(path, compress)
                self.assertEqual(list(directory.glob('.job.log.rotating.*')), [])
                self.assertEqual(self.run_cli('adopt', '--revision', self.inventory()['revision'], path, '--json')[0], 0)
                for item in directory.glob(Path(path).name + '.[0-9]*'):
                    item.unlink()
                archives.prune(self.home)
