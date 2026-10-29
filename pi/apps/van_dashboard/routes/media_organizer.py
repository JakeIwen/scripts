"""Media organizer HTTP boundary; all media operations stay in its CLI."""
import json
from functools import wraps

from flask import Blueprint, jsonify, request

from ..http import api_error, runtime_proxy
from ..van_dashboard_media_organizer import MediaOrganizerBusy
from pi.apps.media_organizer.records import JOB_APPLY, JOB_PROPOSE, JOB_UNDO, Settings

bp = Blueprint('media_organizer', __name__, url_prefix='/api/media-organizer')
control = runtime_proxy('media_organizer')
MAX_REQUEST_BYTES = 1024 * 1024
SETTING_KEYS = tuple(Settings.__dataclass_fields__)


def endpoint(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            result = function(*args, **kwargs)
            payload, status = result if isinstance(result, tuple) else (result, 200)
            response = jsonify(payload)
            response.headers['Cache-Control'] = 'no-store'
            return response, status
        except MediaOrganizerBusy as exc:
            return api_error(str(exc), 409)
        except ValueError as exc:
            return api_error(str(exc), 400)
        except RuntimeError as exc:
            return api_error(str(exc), 502)
    return guarded


def body(required=(), optional=()):
    if request.args or not request.is_json:
        raise ValueError('Organizer controls require JSON input without query parameters.')
    if (request.content_length or 0) > MAX_REQUEST_BYTES:
        raise ValueError('Organizer request is too large.')
    raw = request.stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError('Organizer request is too large.')
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError('Invalid organizer JSON.') from exc
    if not isinstance(payload, dict) or not set(required) <= set(payload) or set(payload) - set(required) - set(optional):
        raise ValueError('Invalid organizer request fields.')
    return payload


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
