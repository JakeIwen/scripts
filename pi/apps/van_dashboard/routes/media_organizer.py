"""Media organizer HTTP boundary; all media operations stay in its CLI."""
from flask import Blueprint, request

from ..http import runtime_proxy
from .cli_boundary import body, endpoint
from pi.apps.media_organizer.records import JOB_APPLY, JOB_PROPOSE, JOB_UNDO, Settings

bp = Blueprint('media_organizer', __name__, url_prefix='/api/media-organizer')
control = runtime_proxy('media_organizer')
SETTING_KEYS = tuple(Settings.__dataclass_fields__)



@bp.get('/status')
@endpoint
def status():
    return control.status()


@bp.post('/propose')
@endpoint
def propose():
    payload = body(optional=('classifier',))
    return control.start(JOB_PROPOSE, classifier=payload.get('classifier')), 202


@bp.post('/decide')
@endpoint
def decide():
    payload = body(('plan_id', 'decisions'))
    return control.decide(payload['plan_id'], payload['decisions'])


@bp.post('/apply')
@endpoint
def apply():
    payload = body(('plan_id',), ('auto',))
    return control.start(JOB_APPLY, payload['plan_id'], auto=payload.get('auto', False)), 202


@bp.post('/undo')
@endpoint
def undo():
    payload = body(('run_id',))
    return control.start(JOB_UNDO, payload['run_id']), 202


@bp.get('/plans/<record_id>')
@endpoint
def plan(record_id):
    return control.lookup('plan', record_id)


@bp.get('/runs/<record_id>')
@endpoint
def run(record_id):
    return control.lookup('run', record_id)


@bp.route('/settings', methods=['GET', 'POST'])
@endpoint
def settings():
    return control.settings(body(optional=SETTING_KEYS) if request.method == 'POST' else None)


@bp.post('/alias')
@endpoint
def alias():
    body()
    return control.alias()
