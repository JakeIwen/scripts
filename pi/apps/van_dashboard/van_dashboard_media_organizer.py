"""Dashboard adapter for the organizer CLI; the CLI owns all media state."""

import datetime
import json
import re
import subprocess
import sys
import tempfile
import threading

from pi.apps.media_organizer.config import CATEGORIES, CATEGORY_MOVIES
from pi.apps.media_organizer.records import CLASSIFIERS, JOB_APPLY, JOB_PROPOSE, JOB_UNDO, Settings
from .van_dashboard_common import run_command


class MediaOrganizerBusy(RuntimeError):
    pass


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', value):
        raise ValueError('Invalid organizer record id.')
    return value


class MediaOrganizerController:
    def __init__(self, cli=None, command=run_command, timeout=20, job_timeout=900):
        self.cli = tuple(cli or (sys.executable, '-P', '-m', 'pi.apps.media_organizer'))
        self.command = command
        self.timeout = timeout
        self.job_timeout = job_timeout
        self._lock = threading.RLock()
        self._job = None

    def _run(self, *arguments, timeout=None):
        try:
            result = self.command([*self.cli, *arguments], timeout=timeout or self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('Media organizer command timed out; check status before retrying.') from exc
        except OSError as exc:
            raise RuntimeError('Could not start media organizer.') from exc
        try:
            payload = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            raise RuntimeError('Media organizer returned invalid JSON.') from exc
        if not isinstance(payload, dict):
            raise RuntimeError('Media organizer returned an invalid record.')
        if result.returncode or 'error' in payload and payload['error']:
            raise RuntimeError(str(payload.get('error') or 'Media organizer command failed.'))
        return payload

    def status(self):
        status = self._run('status')
        with self._lock:
            status['job'] = dict(self._job) if self._job else None
        return status

    def lookup(self, kind, record_id):
        if kind not in ('plan', 'run'):
            raise ValueError('Unknown organizer record kind.')
        return self._run(kind, _identifier(record_id))

    def _ensure_idle(self):
        if self._job and self._job['finished_at'] is None:
            raise MediaOrganizerBusy('Media organizer job is already running.')
        if self._run('status').get('busy'):
            raise MediaOrganizerBusy('Media organizer is busy in another caller.')

    def start(self, stage, record_id=None, auto=False, classifier=None):
        if stage not in (JOB_PROPOSE, JOB_APPLY, JOB_UNDO):
            raise ValueError('Unknown organizer job stage.')
        arguments = [stage]
        if stage != JOB_PROPOSE:
            arguments.append(_identifier(record_id))
        if not isinstance(auto, bool):
            raise ValueError('auto must be true or false.')
        if stage == JOB_APPLY and auto:
            arguments.append('--auto')
        if classifier is not None:
            if stage != JOB_PROPOSE or classifier not in CLASSIFIERS:
                raise ValueError('Invalid classifier.')
            arguments.extend(('--classifier', classifier))
        with self._lock:
            self._ensure_idle()
            self._job = dict(stage=stage, started_at=_now(), finished_at=None,
                             ok=None, error=None, result_id=None)
            job = dict(self._job)
            threading.Thread(target=self._work, args=(arguments,), daemon=True).start()
            return job

    def _work(self, arguments):
        try:
            result = self._run(*arguments, timeout=self.job_timeout)
            outcome = dict(ok=True, error=None, result_id=result.get('id'))
        except Exception as exc:
            outcome = dict(ok=False, error=str(exc), result_id=None)
        with self._lock:
            self._job.update(outcome, finished_at=_now())

    def decide(self, plan_id, decisions):
        plan_id = _identifier(plan_id)
        if not isinstance(decisions, dict) or not decisions:
            raise ValueError('decisions must be a nonempty object.')
        for name, decision in decisions.items():
            if not isinstance(name, str) or not name or not isinstance(decision, dict):
                raise ValueError('Invalid organizer decision.')
            if set(decision) != {'category', 'show', 'accepted'}:
                raise ValueError('Decision requires category, show and accepted.')
            if decision['category'] not in CATEGORIES or type(decision['accepted']) is not bool:
                raise ValueError('Invalid decision category or accepted flag.')
            show = decision['show']
            if decision['category'] == CATEGORY_MOVIES:
                if show is not None:
                    raise ValueError('Movies must not have a show folder.')
            elif not isinstance(show, str) or not show.strip():
                raise ValueError('TV and Documentaries need a show folder.')
        with self._lock:
            self._ensure_idle()
            # The CLI reads the private file before returning; no decision payload in argv.
            with tempfile.NamedTemporaryFile(mode='w+', suffix='.json') as stream:
                json.dump(decisions, stream)
                stream.flush()
                return self._run('decide', plan_id, '--decisions-file', stream.name)

    def settings(self, changes=None):
        if changes is None:
            return self._run('settings')
        if not isinstance(changes, dict) or not changes:
            raise ValueError('Settings require a nonempty object.')
        defaults = Settings().to_dict()
        for key, value in changes.items():
            if key not in defaults:
                raise ValueError('Unknown organizer setting.')
            expected = type(defaults[key])
            if expected is float:
                if type(value) not in (int, float) or not 0 <= value <= 1:
                    raise ValueError('Confidence must be within 0..1.')
            elif type(value) is not expected:
                raise ValueError('Invalid organizer setting type.')
        Settings.from_dict(changes)
        arguments = ['settings']
        for key, value in changes.items():
            arguments.extend(('--set', f'{key}={json.dumps(value) if not isinstance(value, str) else value}'))
        with self._lock:
            self._ensure_idle()
            return self._run(*arguments)

    def alias(self):
        with self._lock:
            self._ensure_idle()
            return self._run('alias')
