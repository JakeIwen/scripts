"""Host-local permission edits never cross the synchronization boundary."""
from dataclasses import replace
from pathlib import Path

from .records import Approval, ApprovalKind, Host, HostPolicy, MAX_OVERRIDE_SECONDS, Mode, ModeValue
from .registry import consumer, validate_policy
from .rules import approval_active
from .store import RecordStore


def policy_path(host):
    name = '.config/vanpi/metered-policy.json' if Host(host) == Host.PI else '.config/jacobr/metered.json'
    return Path.home() / name


class PolicyStore(RecordStore):
    def __init__(self, host, path=None):
        self.host = Host(host)
        super().__init__(path or policy_path(self.host), HostPolicy, HostPolicy)

    def load(self):
        return validate_policy(super().load(), self.host)

    def _change(self, revision, transform):
        def update(current):
            result = transform(current)
            return replace(validate_policy(result, self.host), revision=current.revision + 1)
        return self.change(revision, update)

    def set_mode(self, mode, until, now, revision, set_by='cli'):
        mode = Mode(ModeValue(mode), until, set_by, now)
        if mode.value == ModeValue.AUTO and until is not None:
            raise ValueError('auto mode does not take a duration')
        if until is not None and until > now + MAX_OVERRIDE_SECONDS:
            raise ValueError('mode override cannot exceed 24 hours')
        return self._change(revision, lambda current: replace(current, mode=mode))

    def approve(self, consumer_id, kind, until, state, now, revision, set_by='cli'):
        consumer(consumer_id, self.host)
        kind = ApprovalKind(kind)
        if kind == ApprovalKind.EPISODE and (not state.metered or not state.episode):
            raise ValueError('there is no active metered episode')
        if until is not None and until > now + MAX_OVERRIDE_SECONDS:
            raise ValueError('approval cannot exceed 24 hours')
        approval = Approval(kind, until, state.episode if kind == ApprovalKind.EPISODE else None,
                            set_by, now)
        return self._change(revision, lambda current: replace(
            current, approvals={**current.approvals, consumer_id: approval}))

    def revoke(self, consumer_id, revision):
        consumer(consumer_id, self.host)
        return self._change(revision, lambda current: replace(
            current, approvals={key: value for key, value in current.approvals.items()
                                if key != consumer_id}))

    def set_exempt(self, consumer_id, enabled, revision):
        consumer(consumer_id, self.host)
        if type(enabled) is not bool:
            raise ValueError('exemption requires a boolean')
        def update(current):
            values = set(current.exempt)
            (values.add if enabled else values.discard)(consumer_id)
            return replace(current, exempt=sorted(values))
        return self._change(revision, update)

    def expire(self, state, now):
        with self.locked():
            current = self.load()
            approvals = {key: value for key, value in current.approvals.items()
                         if approval_active(value, state, now)}
            mode = current.mode
            if mode.until is not None and now >= mode.until:
                mode = Mode()
            if mode == current.mode and approvals == current.approvals:
                return current
            updated = replace(current, revision=current.revision + 1,
                              mode=mode, approvals=approvals)
            self.save_locked(updated)
            return updated
