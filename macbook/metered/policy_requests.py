"""Host-local policy request boundary for the Mac CLI."""
from dataclasses import replace

from shared.metered.records import ApprovalKind, PolicyChange, PolicyOperation


def apply_policy(controller, change):
    change = PolicyChange.from_dict(change.as_dict())
    with controller.state.locked():
        previous = controller.state.load()
        state = controller._evaluate(controller.collector(), previous)
        store, now = controller.policy, controller.clock()
        until = now + change.seconds if change.seconds is not None else None
        actions = {
            PolicyOperation.MODE: lambda: store.set_mode(change.value, until, now, change.revision),
            PolicyOperation.APPROVE: lambda: store.approve(
                change.consumer, ApprovalKind(change.value), until, state.effective, now, change.revision),
            PolicyOperation.REVOKE: lambda: store.revoke(change.consumer, change.revision),
            PolicyOperation.EXEMPT: lambda: store.set_exempt(change.consumer, change.enabled, change.revision),
        }
        result = actions[change.op]()
        controller.state.save_locked(replace(state, valid_until=None))
        return result.as_dict()
