"""Read-only guard inspection and explicitly accepted known-good checkpoints."""
from contextlib import contextmanager
import threading

from cli_runner import json_result
from panel_contracts import ApiError, GuardPayload
from panel_paths import GUARD
from wire_validation import confirm, field, object_fields

ACTIONS = frozenset(('inspect', 'audit', 'checkpoint', 'notify-test'))
AUDIT_STATES = frozenset(('healthy', 'drift', 'unavailable', 'none', 'unreadable'))


def decode_findings(value):
    for finding in field(value, 'findings', list):
        object_fields(finding, ('kind', 'uuid', 'detail'), status=502)
        for key in ('kind', 'uuid', 'detail'):
            field(finding, key, str)
    return value


def decode_audit(value):
    object_fields(value, ('status',), status=502)
    if field(value, 'status', str) not in AUDIT_STATES:
        raise ApiError(502, 'Invalid guard audit status')
    if value['status'] not in ('none', 'unreadable'):
        field(value, 'checkpoint_id', str, nullable=True)
        field(value, 'checked_at', float)
        field(value, 'btt_version', str, nullable=True)
        decode_findings(value)
    return value


class GuardAdapter:
    def __init__(self, runner):
        self.runner, self.lock = runner, threading.Lock()

    @contextmanager
    def busy(self):
        if not self.lock.acquire(blocking=False):
            raise ApiError(409, 'The BTT guard section is busy; try again shortly')
        try:
            yield
        finally:
            self.lock.release()

    def call(self, *args, allowed=(0, 2)):
        return json_result(self.runner.run((*GUARD, *args), timeout=120), allowed)

    def checkpoints(self):
        value = self.call('checkpoint', '--list', allowed=(0,))
        if not isinstance(value, list):
            raise ApiError(502, 'Invalid checkpoint list')
        for checkpoint in value:
            object_fields(checkpoint, ('checkpoint_id', 'active'), status=502)
            field(checkpoint, 'checkpoint_id', str)
            field(checkpoint, 'active', bool)
        if sum(c['active'] for c in value) > 1:
            raise ApiError(502, 'Multiple active checkpoints reported')
        return value

    def read(self) -> GuardPayload:
        with self.busy():
            checkpoints = self.checkpoints()
            audit = decode_audit(self.call('last-audit'))
            service = self.call('service', 'status')
            object_fields(service, ('installed', 'supervised', 'job', 'heartbeat', 'audit_status'), status=502)
            field(service, 'installed', bool)
            field(service, 'supervised', bool)
            field(service, 'job', dict)
            field(service, 'heartbeat', dict)
            field(service, 'audit_status', str, nullable=True)
            return {'checkpoints': checkpoints, 'audit': audit, 'service': service}

    def action(self, body):
        object_fields(body, ('action',), ('confirmed', 'replaces'))
        action = field(body, 'action', str, status=400)
        if action not in ACTIONS:
            raise ApiError(400, 'Unsupported guard action; repair is Terminal-only')
        with self.busy():
            if action == 'checkpoint':
                return self.accept_checkpoint(body)
            if action == 'notify-test':
                confirm(body)
                result = self.call('notify-test', '--send')
                object_fields(result, ('sent',), status=502)
                field(result, 'sent', bool)
                return result
            result = self.call(action)
            if action == 'audit':
                return decode_audit(result)
            object_fields(result, ('btt_version', 'records', 'findings'), status=502)
            field(result, 'btt_version', str)
            field(result, 'records', int)
            return decode_findings(result)

    def accept_checkpoint(self, body):
        confirm(body)
        if 'replaces' not in body:
            raise ApiError(400, 'The confirmed checkpoint must name what it replaces')
        expected = field(body, 'replaces', str, nullable=True, status=400)
        active = next((c['checkpoint_id'] for c in self.checkpoints() if c['active']), None)
        if expected != active:
            raise ApiError(409, 'The active checkpoint changed; refresh and confirm again')
        args = ('checkpoint', '--accept-current') + (('--replace',) if active else ())
        result = self.call(*args, allowed=(0,))
        object_fields(result, ('checkpoint_id',), status=502)
        field(result, 'checkpoint_id', str)
        return result
