"""Validated host-policy requests shared by the CLI and HTTP boundary."""
from shared.metered.records import ApprovalKind, PolicyOperation


def apply_policy(controller, request, set_by='cli'):
    store = controller.policy
    now = controller.clock()
    until = None if request.seconds is None else now + request.seconds

    def approve():
        state = controller.evaluate()
        return store.approve(request.consumer, ApprovalKind(request.value), until,
                             state.effective, now, request.revision, set_by)

    actions = {
        PolicyOperation.MODE: lambda: store.set_mode(request.value, until, now, request.revision, set_by),
        PolicyOperation.APPROVE: approve,
        PolicyOperation.REVOKE: lambda: store.revoke(request.consumer, request.revision),
        PolicyOperation.EXEMPT: lambda: store.set_exempt(request.consumer, request.enabled, request.revision),
    }
    return actions[request.op]()
