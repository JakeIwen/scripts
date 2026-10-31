"""Metering controls use the same validated owner operations as the CLI."""
from flask import Blueprint

from pi.apps.metered.policy_requests import apply_policy
from shared.metered.records import PolicyChange, SSIDChange
from shared.metered.store import RevisionConflict
from ..cli_jobs import CliBusy
from ..http import runtime_proxy
from .cli_boundary import body, endpoint


bp = Blueprint('metered', __name__)
metered = runtime_proxy('metered')


@bp.get('/api/metered')
@endpoint
def status():
    return {'ok': True, **metered.status()}


@bp.post('/api/metered/ssids')
@endpoint
def ssids():
    request = SSIDChange.from_dict(body(('op', 'ssid', 'revision'), label='Metering'))
    try:
        config = metered.config.edit_ssid(request.op.value, request.ssid, request.revision)
    except RevisionConflict as error:
        raise CliBusy(str(error)) from error
    return {'ok': True, 'config': config.as_dict()}


@bp.post('/api/metered/policy')
@endpoint
def policy():
    request = PolicyChange.from_dict(body(('op', 'revision'),
        ('consumer', 'value', 'seconds', 'enabled'), label='Metering'))
    try:
        policy = apply_policy(metered, request, 'dashboard')
    except RevisionConflict as error:
        raise CliBusy(str(error)) from error
    return {'ok': True, 'policy': policy.as_dict()}
