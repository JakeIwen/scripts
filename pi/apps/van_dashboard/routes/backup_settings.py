"""Backup preferences, with revision checking at the privileged writer."""
import json
from flask import Blueprint, jsonify, request

from ..http import api_error, runtime_proxy
from ..backup_settings_control import BackupSettingsConflict

bp = Blueprint('backup_settings', __name__)
control = runtime_proxy('backup_settings_control')
MAX_REQUEST_BYTES = 16 * 1024


@bp.route('/api/backups/settings', methods=['GET', 'POST'])
def backup_settings():
    if request.args or (request.content_length or 0) > MAX_REQUEST_BYTES:
        return api_error('Invalid backup settings input.', 400)
    payload = None
    if request.method == 'POST':
        if not request.is_json:
            return api_error('Settings require JSON input.', 400)
        raw = request.stream.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            return api_error('Invalid backup settings input.', 400)
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            return api_error('Invalid backup settings JSON.', 400)
    if request.method == 'POST' and (not isinstance(payload, dict) or set(payload) != {'revision', 'changes'}):
        return api_error('Settings require a revision and changes.', 400)
    try:
        settings = control.request('save' if request.method == 'POST' else 'status', payload)
        response = jsonify({'ok': True, 'settings': settings})
        response.headers['Cache-Control'] = 'no-store'
        return response
    except BackupSettingsConflict as exc:
        return api_error(str(exc), 409)
    except ValueError as exc:
        return api_error(str(exc), 400)
    except RuntimeError as exc:
        return api_error(str(exc), 503)
