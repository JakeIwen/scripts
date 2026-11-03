"""Opt-in Linux descriptor/parent-death acceptance; temporary files, no real mounts.

Requires isolated pinned OFFSITE_RESTIC and OFFSITE_REST_SERVER binaries on Linux.
Mountpoint replacement is simulated by directory rename; no mount/unmount occurs.
"""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from unittest.mock import patch

from offsite_config import REPO_PATH
from synology.offsite.mounts import Drive
from synology.offsite.runtime import Server

RESTIC = os.environ.get('OFFSITE_RESTIC')
BINARY = os.environ.get('OFFSITE_REST_SERVER')
SECRET = 'fixture-only-' + 'a' * 48
PARENT = '''
import json,os,sys,time
from pathlib import Path
from unittest.mock import patch
from synology.offsite.mounts import Drive
from synology.offsite.runtime import Server
root=Path(sys.argv[1]); drive=Drive(root/'usb',tuple(json.loads(sys.argv[2])),{'repo_id':sys.argv[3]})
fd=os.open(root/'watch.lock',os.O_RDWR)
import fcntl
fcntl.flock(fd,fcntl.LOCK_EX)
cfg={'address':'127.0.0.1','port':int(sys.argv[4]),'transport_password':sys.argv[5]}
with patch('synology.offsite.runtime.mounts.discover',return_value=drive):
    server=Server(root,cfg,drive,Path(sys.argv[6]),fd)
(root/'ready').write_text(str(server.child.pid))
while True: time.sleep(1)
'''


@unittest.skipUnless(sys.platform == 'linux' and RESTIC and BINARY,
                     'opt-in Linux /proc descriptor and parent-death smoke with isolated pinned binaries')
class LinuxReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.usb = self.root / 'usb'
        self.usb.mkdir()
        self.password = self.root / 'fixture-password'
        self.password.write_text('fixture encryption password ' * 3)
        self.password.chmod(0o600)
        self.repo_id = self.initialize(self.usb / REPO_PATH)
        device = self.usb.stat().st_dev
        self.mount_id = ('fixture', '{}:{}'.format(os.major(device), os.minor(device)), '/dev/fixture')
        self.drive = Drive(self.usb, self.mount_id, {'repo_id': self.repo_id})
        self.lock = self.root / 'watch.lock'
        self.lock.touch(mode=0o600)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.cfg = {'address': '127.0.0.1', 'port': self.port, 'transport_password': SECRET}

    def initialize(self, path):
        for command in [('init',), ('cat', 'config')]:
            result = subprocess.run([RESTIC, '--repo', str(path), '--password-file', str(self.password),
                                     '--no-cache', *command], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['id']

    def test_replaced_mount_path_cannot_redirect_old_requests(self):
        fd = os.open(self.lock, os.O_RDWR)
        self.addCleanup(os.close, fd)
        with patch('synology.offsite.runtime.mounts.discover', return_value=self.drive):
            server = Server(self.root, self.cfg, self.drive, Path(BINARY), fd)
        self.addCleanup(server.close)
        detached = self.root / 'original-usb'
        self.usb.rename(detached)
        self.usb.mkdir()
        other_id = self.initialize(self.usb / REPO_PATH)
        self.assertNotEqual(other_id, self.repo_id)
        payload = b'disposable append-only object'
        digest = hashlib.sha256(payload).hexdigest()
        request = urllib.request.Request('http://127.0.0.1:{}/data/{}'.format(self.port, digest), data=payload)
        request.add_header('Authorization', 'Basic ' + base64.b64encode((self.repo_id + ':' + SECRET).encode()).decode())
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=3) as response:
            self.assertEqual(response.status, 200)
        self.assertTrue(list((detached / REPO_PATH / 'data').rglob(digest)))
        self.assertFalse(list((self.usb / REPO_PATH / 'data').rglob(digest)))

    def test_parent_death_kills_server_and_releases_inherited_singleton(self):
        # Transport fixture secret is synthetic, never an account credential.
        parent = subprocess.Popen([sys.executable, '-B', '-c', PARENT, str(self.root), json.dumps(self.mount_id),
                                   self.repo_id, str(self.port), SECRET, BINARY],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not (self.root / 'ready').exists() and parent.poll() is None:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.02)
            self.assertIsNone(parent.poll(), parent.communicate() if parent.poll() is not None else '')
            parent.kill()
            parent.communicate(timeout=5)
            fd = os.open(self.lock, os.O_RDWR)
            try:
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(0.02)
            finally:
                os.close(fd)
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
