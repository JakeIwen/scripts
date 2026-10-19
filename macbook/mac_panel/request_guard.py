"""Host, CSRF and body guards shared by all routes, including health."""
import hmac
import json

from panel_contracts import ApiError
from panel_paths import PORT

HOSTS = frozenset((f'127.0.0.1:{PORT}', f'localhost:{PORT}'))
BODY_LIMIT = 65536
SECURITY_HEADERS = {
    'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'Cache-Control': 'no-store',
}


def single_header(headers, name):
    values = headers.get_all(name, [])
    return values[0] if len(values) == 1 else None


def guard_request(method, path, headers, token):
    host = single_header(headers, 'Host')
    if host not in HOSTS:
        raise ApiError(403, 'Host is not allowed')
    if path.startswith('/api/'):
        supplied = single_header(headers, 'X-Panel-Token') or ''
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise ApiError(403, 'Panel token is missing or invalid; refresh the page')
    if method != 'POST':
        return 0
    if single_header(headers, 'Origin') != f'http://{host}':
        raise ApiError(403, 'Origin is missing or invalid')
    content_type = single_header(headers, 'Content-Type') or ''
    if content_type.split(';')[0].strip().lower() != 'application/json':
        raise ApiError(415, 'Content-Type must be application/json')
    size = single_header(headers, 'Content-Length')
    if headers.get('Transfer-Encoding') or size is None or not size.isdecimal():
        raise ApiError(400, 'A valid Content-Length is required')
    if int(size) > BODY_LIMIT:
        raise ApiError(413, 'Request body exceeds 64 KiB')
    return int(size)


def reject_nonfinite(_value):
    raise ValueError('Non-finite JSON number')


def read_body(stream, size):
    try:
        raw = stream.read(size)
        if len(raw) != size:
            raise ValueError('Incomplete body')
        value = json.loads(raw, parse_constant=reject_nonfinite)
        if not isinstance(value, dict):
            raise ValueError('Object required')
        return value
    except (ValueError, UnicodeError):
        raise ApiError(400, 'Body must be a valid JSON object') from None
