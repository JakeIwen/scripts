"""Dashboard adapter; the IPT watcher CLI owns credentials and durable state."""
import re
import sys

from .cli_jobs import CliBusy, CliJobController
from .van_dashboard_common import run_command


class IptWatchBusy(CliBusy):
    pass


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 250 or value.startswith('-') or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid show name.')
    return value


def _torrent_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,32}', value):
        raise ValueError('Invalid torrent id.')
    return value


class IptWatchController(CliJobController):
    def __init__(self, cli=None, command=run_command, timeout=20, job_timeout=180):
        super().__init__(cli or (sys.executable, '-P', '-m', 'pi.apps.iptorrents_watch'),
                         'IPT watchlist', command, timeout, job_timeout)

    def _ensure_idle(self):
        if self._job and self._job['finished_at'] is None:
            raise IptWatchBusy('Watchlist check is already running.')
        if self._run('status').get('checking'):
            raise IptWatchBusy('Watchlist is checking in another caller.')

    def check(self, show=None):
        arguments = ['check']
        if show is not None:
            arguments.extend(('--show', _name(show)))
        with self._lock:
            self._ensure_idle()
            return self._start_job('check', arguments)

    def wantlist(self, action, name, season=None, episode=None):
        if action not in ('add', 'remove', 'pause', 'resume'):
            raise ValueError('Invalid wantlist action.')
        arguments = ['wantlist', action, _name(name)]
        for flag, value in (('--season', season), ('--episode', episode)):
            if value is not None:
                if action != 'add' or type(value) is not int or value < 0:
                    raise ValueError('Season and episode must be nonnegative integers for add.')
                arguments.extend((flag, str(value)))
        with self._lock:
            self._ensure_idle()
            return self._run(*arguments)

    def candidate(self, action, torrent_id):
        if action not in ('download', 'dismiss'):
            raise ValueError('Invalid candidate action.')
        torrent_id = _torrent_id(torrent_id)
        with self._lock:
            self._ensure_idle()
            return self._run(action, torrent_id)
