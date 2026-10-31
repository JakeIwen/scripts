"""qBittorrent's bounded API, using the library's existing cookie transport."""
import json
import os
from urllib.parse import urlencode

from pi.apps.video_library.video_qbittorrent import (
    QbittorrentError, UrllibTransport, validate_torrent_id,
)
from .config import FORCED_TAG, HOLD_TAG, TORRENT_API, TORRENT_TIMEOUT


STOPPED_STATES = frozenset({'pausedDL', 'pausedUP', 'stoppedDL', 'stoppedUP', 'error', 'missingFiles'})
API_ACTIONS = {4: {'hold': 'pause', 'release': 'resume'},
               5: {'hold': 'stop', 'release': 'start'}}


class TorrentApi:
    def __init__(self, transport=None, base_url=TORRENT_API, credentials=None):
        self.transport = transport or UrllibTransport()
        self.base_url = base_url.rstrip('/')
        self.credentials = credentials

    def request(self, endpoint, values=None, login=True):
        body = None if values is None else urlencode(values).encode()
        response = self.transport.request('GET' if body is None else 'POST',
            self.base_url + '/api/v2/' + endpoint,
            headers={'Referer': self.base_url + '/', 'Origin': self.base_url,
                     'Content-Type': 'application/x-www-form-urlencoded'},
            body=body, timeout=TORRENT_TIMEOUT)
        if response.status in (401, 403) and login:
            credentials = self.credentials or {
                'username': os.environ.get('VAN_VIDEO_QBITTORRENT_USERNAME', ''),
                'password': os.environ.get('VAN_VIDEO_QBITTORRENT_PASSWORD', ''),
            }
            if not all(credentials.values()):
                raise QbittorrentError('qBittorrent login is unavailable')
            if self.request('auth/login', credentials, login=False).strip() != b'Ok.':
                raise QbittorrentError('qBittorrent login was rejected')
            return self.request(endpoint, values, login=False)
        if response.status != 200:
            raise QbittorrentError('qBittorrent control request failed')
        return response.body

    def torrents(self):
        rows = json.loads(self.request('torrents/info'))
        if not isinstance(rows, list):
            raise QbittorrentError('invalid torrent list')
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('state'), str):
                raise QbittorrentError('invalid torrent state')
            validate_torrent_id(row.get('hash'))
            if not isinstance(row.get('tags'), str):
                raise QbittorrentError('invalid torrent tags')
        return rows

    def change(self, action, hashes):
        if not hashes:
            return
        version = self.request('app/version').decode().strip().lstrip('v')
        try:
            endpoint = API_ACTIONS[int(version.split('.')[0])][action]
        except (ValueError, KeyError):
            raise QbittorrentError('unsupported qBittorrent API version') from None
        self.request('torrents/' + endpoint, {'hashes': '|'.join(hashes)})

    def tags(self, action, hashes, tag=HOLD_TAG):
        if hashes:
            self.request('torrents/' + {'add': 'addTags', 'remove': 'removeTags'}[action],
                         {'hashes': '|'.join(hashes), 'tags': tag})

    def force_start(self, hashes):
        if hashes:
            self.request('torrents/setForceStart', {'hashes': '|'.join(hashes), 'value': 'true'})


def has_tag(row, tag):
    return tag in {value.strip() for value in row['tags'].split(',')}


def tagged(row):
    return has_tag(row, HOLD_TAG)


def reconcile_api(api, permitted):
    rows = api.torrents()
    if permitted:
        selected = [row['hash'] for row in rows if tagged(row) and row['state'] in STOPPED_STATES
                    and row['state'] not in ('error', 'missingFiles')]
        forced = [row['hash'] for row in rows if row['hash'] in selected and has_tag(row, FORCED_TAG)]
        api.change('release', selected)
        api.force_start(forced)
        refreshed = api.torrents() if selected else rows
        confirmed = [row['hash'] for row in refreshed if tagged(row) and row['state'] not in STOPPED_STATES]
        if set(selected) - set(confirmed):
            raise QbittorrentError('torrent resume is not confirmed')
        api.tags('remove', [row['hash'] for row in refreshed if has_tag(row, FORCED_TAG)
                            and (row['hash'] in confirmed or not tagged(row))], FORCED_TAG)
        api.tags('remove', confirmed)
        return refreshed
    selected = [row['hash'] for row in rows if row['state'] not in STOPPED_STATES]
    # Ownership precedes stopping so a crash does not strand an unowned hold.
    api.tags('add', [row['hash'] for row in rows if row['hash'] in selected
                     and row.get('force_start') is True], FORCED_TAG)
    api.tags('add', selected)
    api.change('hold', selected)
    refreshed = api.torrents() if selected else rows
    if any(row['state'] not in STOPPED_STATES for row in refreshed):
        raise QbittorrentError('torrent hold is not confirmed')
    return refreshed
