"""Shared case base for the offsite lock suites: a prepared fixture, a flock probe and run helpers."""
import json
import time
import unittest

import backup_locks
from backup_locks import LockBusy
from pi.tests.backup.offsite_fixtures import OffsiteFixture


class LockFixtureCase(unittest.TestCase):
    fixture = OffsiteFixture

    def setUp(self):
        self.f = self.fixture()
        self.addCleanup(self.f.close)
        result = self.f.prepare_replayed()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.f.sources()

    def update(self, **values):
        self.f.reload()
        self.f.state.update(values)
        self.f.save()

    def unmount(self, label):
        self.f.reload()
        self.f.state['mounts'].pop(str(self.f.cfg.mount_root / label))
        self.f.save()

    def hold(self, lock_class, name, exclusive=True):
        fd = backup_locks.open_lock(backup_locks.lock_path(lock_class, name))
        backup_locks.try_flock(fd, exclusive)
        return fd

    def backup(self, *sources, **kwargs):
        args = ['backup', '--label', self.f.label]
        for source in sources:
            args.extend(('--only', source))
        return self.f.run(*args, **kwargs)

    def start(self, *sources):
        args = ['backup', '--label', self.f.label]
        for source in sources:
            args.extend(('--only', source))
        child = self.f.start(*args)
        self.addCleanup(self.stop, child)
        return child

    def stop(self, child):
        if child.poll() is None:
            child.kill()
            child.communicate()

    def wait_for(self, condition, child, message, timeout=60):
        deadline = time.monotonic() + timeout
        while not condition():
            if child.poll() is not None:
                self.fail(message + ': ' + ''.join(child.communicate()))
            self.assertLess(time.monotonic(), deadline, message)
            time.sleep(0.05)

    def free(self, lock_class, name):
        """A non-blocking exclusive probe through a descriptor on the existing lock file."""
        try:
            with backup_locks.held(lock_class, name):
                return True
        except LockBusy:
            return False

    def stall_release(self, label):
        """Make the mount helper's release of label block until the returned proceed file exists."""
        stalled, proceed = self.f.root / 'release.stalled', self.f.root / 'release.proceed'
        self.update(release_stall={'label': label, 'stalled': str(stalled), 'proceed': str(proceed)})
        self.addCleanup(proceed.touch)  # a failing test never leaves the stub waiting
        return stalled, proceed

    def releases(self):
        return [row for row in self.f.executions() if row['name'] == 'mounts' and row['args'][0] == 'release']

    def restic_backups(self):
        return [row[row.index('--tag') + 1] for row in self.f.calls() if row[0] == 'restic' and 'backup' in row
                and '--stdin' not in row]

    def status(self):
        return json.loads(self.f.run('status').stdout)
