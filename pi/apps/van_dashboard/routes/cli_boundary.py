"""Bounded JSON input and error mapping for dashboard CLI controls."""
import json
from functools import wraps

from flask import jsonify, request

from ..cli_jobs import CliBusy
from ..http import api_error

MAX_REQUEST_BYTES = 1024 * 1024


def endpoint(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            result = function(*args, **kwargs)
            payload, status = result if isinstance(result, tuple) else (result, 200)
            response = jsonify(payload)
            response.headers['Cache-Control'] = 'no-store'
            return response, status
        except CliBusy as exc:
            return api_error(str(exc), 409)
        except ValueError as exc:
            return api_error(str(exc), 400)
        except RuntimeError as exc:
            return api_error(str(exc), 502)
    return guarded


def body(required=(), optional=(), label='Organizer'):
    if request.args or not request.is_json:
        raise ValueError(f'{label} controls require JSON input without query parameters.')
    if (request.content_length or 0) > MAX_REQUEST_BYTES:
        raise ValueError(f'{label} request is too large.')
    raw = request.stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError(f'{label} request is too large.')
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f'Invalid {label.lower()} JSON.') from exc
    if not isinstance(payload, dict) or not set(required) <= set(payload) or set(payload) - set(required) - set(optional):
        raise ValueError(f'Invalid {label.lower()} request fields.')
    return payload
