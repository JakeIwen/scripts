"""Backup preferences, with revision checking at the privileged writer."""
from flask import Blueprint, jsonify, request

from ..http import api_error, runtime_proxy

bp = Blueprint('backup_settings', __name__)
control = runtime_proxy('backup_settings_control')


@bp.route('/api/backups/settings', methods=['GET', 'POST'])
def backup_settings():
    if request.args or (request.content_length or 0) > 16384:
        return api_error('Invalid backup settings input.', 400)
    payload = request.get_json(silent=True) if request.method == 'POST' else None
    if request.method == 'POST' and (not isinstance(payload, dict) or set(payload) != {'revision', 'changes'}):
        return api_error('Settings require a revision and changes.', 400)
    try:
        settings = control.request('save' if request.method == 'POST' else 'status', payload)
        response = jsonify({'ok': True, 'settings': settings})
        response.headers['Cache-Control'] = 'no-store'
        return response
    except ValueError as exc:
        return api_error(str(exc), 409 if 'changed elsewhere' in str(exc) else 400)
    except RuntimeError as exc:
        return api_error(str(exc), 503)
