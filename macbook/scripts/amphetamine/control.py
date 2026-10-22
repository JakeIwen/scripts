"""Revision-checked native settings changes with session preservation and rollback."""
from contextlib import contextmanager
import fcntl

from .model import (CONTROLLED_KEYS, DELAY, DISPLAY_SLEEP, ENABLED, Conflict,
                    ControlError, revision, status_record, validate_change)


@contextmanager
def exclusive(app):
    app.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (app.state_dir / 'control.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Conflict('Another Amphetamine update is in progress.') from exc
        yield


def read_status(app):
    values = app.read_preferences()
    try:
        return status_record(values, app.session())
    except ControlError as exc:
        return status_record(values, error=str(exc))


def restore(app, original, previous):
    # Quitting first prevents the just-launched process overwriting the rollback.
    app.quit()
    app.write_preferences({key: original.get(key) for key in CONTROLLED_KEYS})
    app.resume(previous)


def configure(app, enabled, minutes, expected_revision):
    validate_change(enabled, minutes, expected_revision)
    with exclusive(app):
        original = app.read_preferences()
        if revision(original) != expected_revision:
            raise Conflict('Amphetamine settings changed; refresh before saving.')
        previous = app.session()
        previous.ensure_restartable()
        changes = {ENABLED: enabled, DELAY: minutes * 60}
        if enabled:
            changes[DISPLAY_SLEEP] = True
        app.backup(original)
        app.quit()
        # A preferences window can save during quit. Never overwrite that edit.
        try:
            fresh = app.read_preferences()
        except ControlError:
            app.resume(previous)
            raise
        if revision(fresh) != expected_revision:
            app.resume(previous)
            raise Conflict('Amphetamine saved newer settings while closing; refresh before saving.')
        try:
            app.write_preferences(changes)
            app.resume(previous, start=enabled)
            current = app.read_preferences()
            if any(current.get(key) != value for key, value in changes.items()):
                raise ControlError('Amphetamine did not retain the requested settings.')
            session = app.session()
        except ControlError as exc:
            try:
                restore(app, original, previous)
            except ControlError as rollback_error:
                raise ControlError('{} Rollback also failed: {}. Saved backup: {}'.format(
                    exc, rollback_error, app.state_dir)) from exc
            raise ControlError('{} Original settings and session were restored.'.format(exc)) from exc
        result = status_record(current, session)
        result['message'] = ('Automatic lock {}. Keep-awake session {}.'.format(
            'set to {} minutes'.format(minutes) if enabled else 'off',
            'active' if session.active else 'inactive'))
        return result
