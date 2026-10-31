"""Private filesystem and real-client transport fixtures; never contact live media."""
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs
from pi.apps.media_organizer import config
from pi.apps.media_organizer.qbittorrent_client import Client
from pi.apps.video_library.video_qbittorrent import HttpResponse


class TorrentTransport:
    def __init__(self):
        self.torrents = []
        self.moves = []
        self.fail = set()
        self.crash = set()
        self.unavailable = False
        self.auth_required = False
        self.logged_in = False

    def request(self, method, url, *, headers, body, timeout):
        if self.unavailable:
            raise RuntimeError('offline fixture')
        if url.endswith('auth/login'):
            self.logged_in = True
            return HttpResponse(200, b'Ok.')
        if self.auth_required and not self.logged_in:
            return HttpResponse(403, b'Forbidden')
        if url.endswith('torrents/info'):
            listed = [{k: v for k, v in t.items() if k != 'files'} for t in self.torrents]
            return HttpResponse(200, json.dumps(deepcopy(listed)).encode())
        if '/torrents/files?hash=' in url:
            wanted = url.rsplit('=', 1)[1]
            torrent = next(t for t in self.torrents if t['hash'] == wanted)
            return HttpResponse(200, json.dumps([{'name': n} for n in torrent.get('files', [])]).encode())
        if not url.endswith('torrents/setLocation') or method != 'POST':
            raise AssertionError('unexpected endpoint')
        form = parse_qs(body.decode())
        torrent = next(t for t in self.torrents if t['hash'] == form['hashes'][0])
        if torrent['hash'] in self.fail:
            return HttpResponse(500, b'failed')
        target = Path(form['location'][0])
        source = Path(torrent['save_path'])
        relative = Path(torrent['content_path']).relative_to(source)
        # Like libtorrent: move the torrent's own files one by one, then drop
        # directories left empty; anything else stays behind.
        for name in torrent.get('files', []):
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            os.rename(source / name, target / name)
        root = source / relative.parts[0]
        for directory in sorted((p for p in [root, *root.rglob('*')] if p.is_dir()),
                                key=lambda p: len(p.parts), reverse=True):
            if not any(directory.iterdir()):
                directory.rmdir()
        torrent.update(save_path=str(target), content_path=str(target / relative))
        self.moves.append((torrent['hash'], str(target)))
        if torrent['hash'] in self.crash:
            raise KeyboardInterrupt('simulated process death after move')
        return HttpResponse(200, b'')


class Fixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.media, self.mirror_mount = self.root / 'media', self.root / 'mirror'
        self.mirror = self.mirror_mount / 'mp_backup'
        self.new = self.media / 'torrent' / 'New'
        self.new.mkdir(parents=True)
        self.mirror.mkdir(parents=True)
        self.state = self.root / 'state'
        values = {
            'MEDIA_ROOT': self.media, 'MIRROR_ROOT': self.mirror,
            'MIRROR_MOUNT': self.mirror_mount, 'STATE_DIR': self.state,
            'PLANS_DIR': self.state / 'plans', 'RUNS_DIR': self.state / 'runs',
            'SETTINGS_PATH': self.state / 'settings.json', 'LOCK_PATH': self.root / 'lock',
            'LOG_PATH': self.root / 'organizer.log', 'ALIAS_MEDIA_SCRIPT': self.root / 'alias',
            'MIRROR_LOCK_PATH': self.root / 'mirror.lock',
        }
        patcher = patch.multiple(config, **values)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(Path, 'is_mount', lambda path: path in (self.media, self.mirror_mount))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.transport = TorrentTransport()
        self.client = Client(self.transport)
        for module in ('scan', 'apply', 'undo', 'status'):
            patcher = patch('pi.apps.media_organizer.' + module + '.Client', return_value=self.client)
            patcher.start()
            self.addCleanup(patcher.stop)
        config.ALIAS_MEDIA_SCRIPT.write_text('#!/bin/sh\nexit 0\n')
        config.ALIAS_MEDIA_SCRIPT.chmod(0o700)

    def payload(self, name, tracked=True, state='forcedUP', progress=1, mirror=False):
        path = self.new / name
        path.mkdir()
        (path / 'video.mkv').write_bytes(b'video data')
        if mirror:
            copy = self.mirror / 'torrent' / 'New' / name
            copy.mkdir(parents=True)
            (copy / 'video.mkv').write_bytes(b'backup video data')
        if tracked:
            torrent = dict(hash=f'{len(self.transport.torrents) + 1:040x}', name=name,
                           save_path=str(self.new), content_path=str(path), state=state,
                           progress=progress, amount_left=0 if progress == 1 else 42,
                           files=[f'{name}/video.mkv'])
            self.transport.torrents.append(torrent)
            return torrent
        return None

    def show(self, name='The Example', category='TV'):
        path = self.media / 'torrent' / category / name
        path.mkdir(parents=True, exist_ok=True)
        return path
