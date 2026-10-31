"""Minimal relocation client using the library's bounded cookie transport."""
import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode
from pi.apps.video_library.video_qbittorrent import (
    UrllibTransport, QbittorrentError, validate_torrent_id,
)
from . import config
from .paths import destination, new_directory, confined


class Client:
    def __init__(self, transport=None, credentials=None):
        self.transport = transport or UrllibTransport()
        self.credentials = credentials
        self.base_url = config.QBITTORRENT_URL.rstrip('/')

    def _send(self, endpoint, form=None):
        return self.transport.request(
            'GET' if form is None else 'POST', self.base_url + '/api/v2/' + endpoint,
            headers={'Referer': self.base_url + '/',
                     'Content-Type': 'application/x-www-form-urlencoded'},
            body=None if form is None else urlencode(form).encode(),
            timeout=config.QBITTORRENT_TIMEOUT,
        )

    def _request(self, endpoint, form=None):
        response = self._send(endpoint, form)
        if response.status in (401, 403):
            credentials = self.credentials or (
                os.environ.get('VAN_VIDEO_QBITTORRENT_USERNAME', ''),
                os.environ.get('VAN_VIDEO_QBITTORRENT_PASSWORD', ''),
            )
            if not all(credentials):
                raise QbittorrentError('qBittorrent requires login credentials')
            login = self._send('auth/login', dict(zip(('username', 'password'), credentials)))
            if login.status != 200 or login.body.strip() != b'Ok.':
                raise QbittorrentError('qBittorrent login failed')
            response = self._send(endpoint, form)
        if response.status != 200:
            raise QbittorrentError(f'qBittorrent request failed (HTTP {response.status})')
        return response.body

    def list_torrents(self):
        data = json.loads(self._request('torrents/info'))
        if not isinstance(data, list):
            raise QbittorrentError('invalid qBittorrent torrent list')
        for row in data:
            if not isinstance(row, dict):
                raise QbittorrentError('invalid qBittorrent torrent record')
            for field in ('hash', 'name', 'save_path', 'content_path', 'state'):
                if not isinstance(row.get(field), str):
                    raise QbittorrentError('incomplete qBittorrent torrent record')
            validate_torrent_id(row['hash'])
            if (type(row.get('progress')) not in (int, float)
                    or type(row.get('amount_left')) not in (int, float)):
                raise QbittorrentError('invalid qBittorrent progress')
        return data

    def torrent_files(self, torrent_hash):
        """Paths of the torrent's files, relative to its save path."""
        data = json.loads(self._request('torrents/files?hash=' + validate_torrent_id(torrent_hash)))
        if not isinstance(data, list) or not all(
                isinstance(row, dict) and isinstance(row.get('name'), str) for row in data):
            raise QbittorrentError('invalid qBittorrent file list')
        return [row['name'] for row in data]

    def available(self):
        try:
            self.list_torrents()
            return True
        except (QbittorrentError, ValueError, OSError):
            return False

    def set_location(self, torrent_hash, path):
        target = destination(Path(path))
        self._relocate(torrent_hash, target)

    def restore_location(self, torrent_hash, path):
        # Rollback alone may return a payload to New; ordinary writes cannot.
        if Path(path) != new_directory():
            raise ValueError('rollback destination must be torrent/New')
        self._relocate(torrent_hash, confined(Path(path), config.MEDIA_ROOT))

    def _relocate(self, torrent_hash, path):
        self._request('torrents/setLocation',
                      {'hashes': validate_torrent_id(torrent_hash), 'location': str(path)})

    def wait_for_location(self, torrent_hash, path, timeout=None):
        deadline = time.monotonic() + (config.MOVE_WAIT_SECONDS if timeout is None else timeout)
        while True:
            row = next((t for t in self.list_torrents() if t['hash'] == torrent_hash), None)
            if row is None:
                raise QbittorrentError('torrent disappeared while moving')
            if Path(row['save_path']) == Path(path) and row['state'] != 'moving':
                if row['state'] in ('error', 'missingFiles'):
                    raise QbittorrentError('torrent reported a relocation error')
                return row
            if time.monotonic() >= deadline:
                raise TimeoutError('qBittorrent relocation is unconfirmed; inspect before retrying')
            time.sleep(0.2)


QbittorrentClient = Client
