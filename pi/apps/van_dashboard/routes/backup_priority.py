"""Backup scheduling priority and one-time Mac capture controls."""
from flask import Blueprint, jsonify, request

from ..http import _exact_form, api_error, runtime_proxy

bp = Blueprint('backup_priority', __name__)
control = runtime_proxy('backup_priority_control')


@bp.route('/api/backups/priority', methods=['GET', 'POST'])
def backup_priority():
    if request.args or (request.method == 'POST' and not _exact_form(('mode',))):
        return api_error('Invalid backup priority input.', 400)
    try:
        if request.method == 'POST':
            control.select(request.form['mode'])
        response = jsonify({'ok': True, 'priority': control.status()})
        response.headers['Cache-Control'] = 'no-store'
        return response
    except ValueError as exc:
        return api_error(str(exc), 400)
    except RuntimeError as exc:
        return api_error(str(exc), 503)


@bp.route('/api/backups/priority/capture', methods=['POST'])
@bp.route('/api/backups/priority/capture/cancel', methods=['POST'])
def mac_capture_priority():
    if request.args or not _exact_form(()) or request.content_length:
        return api_error('Mac capture priority does not accept input.', 400)
    try:
        control.capture(cancel=request.path.endswith('/cancel'))
        return jsonify({'ok': True})
    except ValueError as exc:
        return api_error(str(exc), 400)
    except RuntimeError as exc:
        return api_error(str(exc), 503)
