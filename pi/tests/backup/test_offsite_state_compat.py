"""Persisted offsite state stays readable across the release boundary, both ways.

A run executes from its frozen snapshot for days while newer code is deployed,
so the newest `status`/`dashboard`/`rates` readers must parse what the older
running writer leaves, and (for a rollback) the older readers must parse what
the newer writer leaves. The older side is offsite v2 exactly as released in
5ed7311, extracted from Git history and run through its own fixture.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from backup_locks import LockClass
import backup_locks
from pi.tests.backup.offsite_remote_fixtures import RemoteFixture, SmbFixture
from pi.tests.backup.offsite_state_writes import readers, writes

REPO = Path(__file__).resolve().parents[3]
V2_RELEASE = '5ed7311'
V2_PATHS = ('pi/__init__.py', 'pi/tests/__init__.py', 'pi/scripts/backup', 'pi/tests/backup')


def legacy_sections(value):
    # New read-only dashboard capabilities are additive, not persisted-state changes.
    return {**value, 'dashboard': {key: item for key, item in value['dashboard'].items()
                                   if key not in ('controls', 'availability')}}


def v2_available():
    try:
        return subprocess.run(['git', '-C', str(REPO), 'cat-file', '-e', V2_RELEASE + '^{commit}'],
                              capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


V2_WRITER = '''
import json, shutil, sys, time
from pathlib import Path
from pi.tests.backup.offsite_remote_fixtures import RemoteFixture
from pi.tests.backup.offsite_state_writes import readers, writes
'''

V2_READER = '''
import json, os, sys
from pathlib import Path
from offsite_config import Config
from offsite_dashboard import dashboard, rates, status
from offsite_repository import records
cfg = Config(borg=Path('/nonexistent'), ignition=Path('/nonexistent'), state=Path(sys.argv[1]), owner=os.getuid())
print(json.dumps({'status': status(cfg), 'dashboard': dashboard(cfg),
                  'rates': {source: rates(cfg, source) for source in cfg.sources()},
                  'repos': records(cfg)}, sort_keys=True))
'''


@unittest.skipUnless(v2_available(), f'needs the offsite v2 release commit {V2_RELEASE} in local Git history')
class StateCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.v2 = Path(cls.temporary.name) / 'v2'
        cls.v2.mkdir()
        archive = subprocess.run(['git', '-C', str(REPO), 'archive', '--format=tar', V2_RELEASE, *V2_PATHS],
                                 capture_output=True, check=True, timeout=60).stdout
        subprocess.run(['tar', '-x', '-C', str(cls.v2)], input=archive, check=True, timeout=60)
        # v2 runs these same write steps through its own fixture and code.
        shutil.copy2(Path(__file__).with_name('offsite_state_writes.py'), cls.v2 / 'pi/tests/backup')
        # Repair only the old doubles' discarded stdin, which races the parent's
        # pipe write. Every historical production writer/reader stays untouched.
        repairs = (
            ('offsite_remote_fixtures.py', "    print(json.dumps({'allowed':",
             "    json.load(sys.stdin)\n    print(json.dumps({'allowed':"),
            ('offsite_fake_command.py', 'def partition(state, args):\n',
             'def partition(state, args):\n    sys.stdin.read()\n'),
        )
        for filename, before, after in repairs:
            path = cls.v2 / 'pi/tests/backup' / filename
            text = path.read_text()
            if text.count(before) != 1:
                raise AssertionError(f'Expected exactly one historical stdin repair site in {filename}')
            path.write_text(text.replace(before, after, 1))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def v2_environment(self):
        environment = {key: value for key, value in os.environ.items() if not key.startswith('VANPI_BACKUP')}
        environment['PYTHONPATH'] = os.pathsep.join((str(self.v2), str(self.v2 / 'pi/scripts/backup')))
        return environment

    def v2_python(self, code, *args):
        result = subprocess.run([sys.executable, '-B', '-c', code, *args], cwd=self.v2, env=self.v2_environment(),
                                capture_output=True, text=True, timeout=600)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def fixture(self, kind=RemoteFixture):
        f = kind()
        self.addCleanup(f.close)
        result = f.prepare_replayed()
        self.assertEqual(result.returncode, 0, result.stderr)
        f.sources()
        return f

    def test_v2_command_doubles_wait_for_input(self):
        self.v2_python(V2_WRITER + '''
from pi.tests.backup.offsite_state_writes import assert_stdin_consumed
f = RemoteFixture()
try:
    for name in ('metered', 'sfdisk'):
        assert_stdin_consumed(f, name)
finally:
    f.close()
''')

    def test_newest_readers_parse_state_written_by_v2(self):
        output = Path(self.temporary.name) / 'v2-state'
        self.v2_python(V2_WRITER + '''
f = RemoteFixture()
try:
    assert f.prepare().returncode == 0
    f.sources()
    results, failed = writes(f, int(time.time()) - 1000)
    assert all(result.returncode == 0 for result in results), [r.stderr for r in results]
    assert failed.returncode == 1
    out = Path(sys.argv[1])
    shutil.copytree(f.cfg.state, out / 'state')
    (out / 'readers.json').write_text(json.dumps(readers(f)))
finally:
    f.close()
''', str(output))
        expected = json.loads((output / 'readers.json').read_text())
        self.assertEqual(set(expected['dashboard']['history']['offsite-3tb']), {'borg', 'exfat512', 'movingparts'})
        f = self.fixture()
        for path in (output / 'state').iterdir():
            target = f.cfg.state / path.name
            if target.is_dir():
                shutil.rmtree(target)
            (shutil.copytree if path.is_dir() else shutil.copy2)(path, target)
        actual = readers(f)
        self.assertFalse(actual['dashboard']['controls']['supported'])
        self.assertEqual(legacy_sections(actual), expected)

    def test_v2_readers_parse_state_written_by_the_newest_writer(self):
        f = self.fixture()
        results, failed = writes(f, int(time.time()) - 1000)
        # Finish on a run whose status carries the additive cleanup_notes field.
        f.reload()
        f.state['mounts'].pop(str(f.cfg.mount_root / 'bigboi'))
        f.save()
        fd = backup_locks.open_lock(backup_locks.lock_path(LockClass.DISK, 'bigboi'))
        self.addCleanup(os.close, fd)
        backup_locks.try_flock(fd, False)
        results.append(f.run('backup', '--label', f.label, '--only', 'borg'))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(failed.returncode, 1)
        newest = readers(f)
        self.assertIn('cleanup_notes', newest['status'])
        old = json.loads(self.v2_python(V2_READER, str(f.cfg.state)))
        self.assertEqual({key: old[key] for key in ('status', 'dashboard', 'rates')}, legacy_sections(newest))
        self.assertEqual(set(old['repos']['repos']), {f.label})
        self.assertEqual(len(old['rates']['borg']['samples']), 2)

    def test_v2_readers_parse_state_left_by_a_shared_folder_run(self):
        # The newest readers' side of this direction is the test above: v2 state has no transport_kind.
        f = self.fixture(SmbFixture)
        for args in (('--remote',), ()):
            result = f.run('backup', *args, '--label', f.label, '--only', 'borg')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            newest = readers(f)
            self.assertEqual(newest['status']['transport_kind'], 'smb' if args else None)
            old = json.loads(self.v2_python(V2_READER, str(f.cfg.state)))
            self.assertEqual({key: old[key] for key in ('status', 'dashboard', 'rates')}, legacy_sections(newest))


if __name__ == '__main__':
    unittest.main()
