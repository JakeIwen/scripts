"""Dashboard adapter for the organizer CLI; the CLI owns all media state."""

import json
import re
import sys
import tempfile

from pi.apps.media_organizer.config import CATEGORIES, CATEGORY_MOVIES
from pi.apps.media_organizer.records import CLASSIFIERS, JOB_APPLY, JOB_PROPOSE, JOB_UNDO, Settings
from .van_dashboard_common import run_command
from .cli_jobs import CliBusy, CliJobController


class MediaOrganizerBusy(CliBusy):
    pass


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', value):
        raise ValueError('Invalid organizer record id.')
    return value


class MediaOrganizerController(CliJobController):
    def __init__(self, cli=None, command=run_command, timeout=20, job_timeout=900):
        super().__init__(cli or (sys.executable, '-P', '-m', 'pi.apps.media_organizer'),
                         'Media organizer', command, timeout, job_timeout)

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
            return self._start_job(stage, arguments)

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
