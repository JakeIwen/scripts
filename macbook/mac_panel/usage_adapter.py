"""The usage tool through the CLI boundary: its sanitized cached facts, and the manual mode switch."""
from datetime import datetime, timezone

from cli_runner import json_result
from log_redaction import redact
from panel_contracts import ApiError, UsagePayload
from wire_validation import confirm, field, object_fields

TOOL = ('/usr/bin/python3', '-I', '-B', '/Users/jacobr/config/mac/usage-budget/usage_budget.py')
COMMAND = TOOL + ('facts', '--json')
MODE_COMMAND = TOOL + ('mode',)
# The tool's mode names, defined once for the wire check; `auto` clears a manual mode back to automatic.
MODES = ('claude-conserve', 'gpt-conserve', 'balanced-conserve', 'balanced-burn', 'gpt-burn', 'claude-burn', 'auto')
WINDOWS = ('claude_weekly', 'claude_session', 'fable_weekly', 'codex_weekly', 'codex_session')
# Normalised field -> the facts keys that may carry it (older shape first, then the positions shape).
POSITION_SOURCES = {'left': ('left',), 'elapsed_pct': ('elapsed_pct',), 'line_left': ('line_left', 'line'),
                    'ahead': ('ahead', 'position'), 'resets_in_h': ('resets_in_h',)}
ROUTE_KEYS = ('workers', 'builders', 'review', 'parents')
AVAILABILITY = frozenset(('ok', 'constrained', 'out', 'unknown'))


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Missing timezone')
    except ValueError:
        raise ApiError(502, 'Invalid usage timestamp') from None
    return value


def hours_until(when):
    """Signed hours from now to an ISO timestamp (negative when past)."""
    moment = datetime.fromisoformat(timestamp(when).replace('Z', '+00:00'))
    return (moment - datetime.now(timezone.utc)).total_seconds() / 3600


def position_fields(raw, gpt=False):
    """Normalise one position; fields the tool did not send stay absent."""
    if not isinstance(raw, dict):
        return None
    result = {}
    for name, keys in POSITION_SOURCES.items():
        key = next((k for k in keys if raw.get(k) is not None), None)
        if key is not None:
            result[name] = field(raw, key, float)
    if raw.get('reset') is not None and 'resets_in_h' not in result:
        result['resets_in_h'] = max(0.0, hours_until(field(raw, 'reset', str)))
    if gpt and isinstance(raw.get('on_credits'), bool):
        result['on_credits'] = raw['on_credits']
    return result or None


def decode_routing(value, result):
    """Lean, positions, reasons and route: optional, since that tool's contract evolves."""
    positions = value.get('positions') if isinstance(value.get('positions'), dict) else {}
    lean = value.get('lean', value.get('mode'))
    if isinstance(lean, str):
        result['lean'] = lean
    claude = position_fields(value.get('claude_position') or positions.get('claude'))
    result['claude_position'] = claude
    gpt = position_fields(positions.get('gpt'), gpt=True)
    if gpt:
        result['gpt_position'] = gpt
    reasons = value.get('reasons', [])
    if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
        raise ApiError(502, 'Invalid usage reasons')
    result['reasons'] = reasons
    route = value.get('route') if isinstance(value.get('route'), dict) else value
    result['route'] = {key: route[key] for key in ROUTE_KEYS if isinstance(route.get(key), str)}
    lines = value.get('lines')
    if isinstance(lines, list) and all(isinstance(item, str) for item in lines):
        result['lines'] = lines
    for key in ('mode_source', 'expires_at'):
        if isinstance(value.get(key), str):
            result[key] = value[key]


def decode_availability(raw, result):
    """Each family is a state string or an object with `state` (and optionally window_left, reset)."""
    states, detail = {}, {}
    for family in ('claude', 'gpt'):
        item = raw.get(family)
        extra = {}
        if isinstance(item, dict):
            item, extra = item.get('state'), item
        if not isinstance(item, str) or item not in AVAILABILITY:
            raise ApiError(502, 'Invalid usage availability')
        states[family] = item
        if isinstance(extra.get('window_left'), (int, float)) and not isinstance(extra['window_left'], bool):
            detail.setdefault(family, {})['window_left'] = field(extra, 'window_left', float)
        if isinstance(extra.get('reset'), str):
            detail.setdefault(family, {})['reset'] = timestamp(extra['reset'])
    result['availability'] = states
    if detail:
        result['availability_detail'] = detail


def window_fields(window):
    used, left = field(window, 'used', float), field(window, 'left', float)
    reset = field(window, 'resets_at', str, nullable=True)
    return {'used': used, 'left': left, 'resets_at': timestamp(reset) if reset else None}


def fallback_window(name, value):
    """A window the tool no longer sends top-level, rebuilt from its positions or availability."""
    positions = value.get('positions') if isinstance(value.get('positions'), dict) else {}
    source, key = {'claude_weekly': (positions.get('claude'), 'reset'), 'codex_weekly': (positions.get('gpt'), 'reset'),
                   'claude_session': (value['availability'].get('claude'), 'reset'),
                   'codex_session': (value['availability'].get('gpt'), 'reset')}.get(name, (None, None))
    left = source.get('window_left', source.get('left')) if isinstance(source, dict) else None
    if type(left) not in (int, float):
        return None
    return window_fields({'used': 100 - left, 'left': left, 'resets_at': source.get(key)})


def decode_credits(value, result):
    """Credits: the old object, else the credit balance the gpt position reports; absent when neither."""
    credits = field(value, 'credits', dict, nullable=True) if 'credits' in value else None
    gpt = (value.get('positions') or {}).get('gpt') if isinstance(value.get('positions'), dict) else None
    if not isinstance(credits, dict):
        credits = {'left': gpt.get('credits')} if isinstance(gpt, dict) and gpt.get('credits') is not None else None
    if credits is None:
        result['credits'] = None
        return
    result['credits'] = {key: field(credits, key, float) for key in ('left', 'allowance_per_day', 'burn_per_day')
                         if credits.get(key) is not None}
    if credits.get('expires') is not None:
        expires = field(credits, 'expires', str)
        try:
            datetime.strptime(expires, '%Y-%m-%d')
        except ValueError:
            raise ApiError(502, 'Invalid credits expiry') from None
        result['credits']['expires'] = expires
    if credits.get('days_left') is not None:
        result['credits']['days_left'] = field(credits, 'days_left', float)


def decode_usage(value) -> UsagePayload:
    object_fields(value, ('availability',), status=502)
    stamp = 'fetched_at' if 'fetched_at' in value else 'generated_at'
    result = {'fetched_at': timestamp(field(value, stamp, str))}
    if value.get('age_minutes') is not None:
        result['age_minutes'] = field(value, 'age_minutes', float)
    else:
        result['age_minutes'] = max(0.0, -hours_until(result['fetched_at']) * 60)
    if result['age_minutes'] < 0:
        raise ApiError(502, 'Invalid usage age')
    decode_availability(field(value, 'availability', dict), result)
    for name in WINDOWS:
        window = field(value, name, dict, nullable=True) if name in value else None
        result[name] = (window_fields(window) if window is not None
                        else None if name in value else fallback_window(name, value))
    decode_credits(value, result)
    decode_routing(value, result)
    return result


class UsageAdapter:
    def __init__(self, runner):
        self.runner = runner

    def read(self) -> UsagePayload:
        return decode_usage(json_result(self.runner.run(COMMAND, timeout=20)))

    def mutate(self, body) -> UsagePayload:
        """Set (or clear) the manual mode; the tool refuses names it does not know with exit 2, writes nothing."""
        object_fields(body, ('action', 'mode'), ('confirmed',))
        if field(body, 'action', str, status=400) != 'mode':
            raise ApiError(400, 'Unsupported usage action')
        mode = field(body, 'mode', str, status=400)
        if mode not in MODES:
            raise ApiError(400, 'Unknown usage mode')
        confirm(body)
        result = self.runner.run(MODE_COMMAND + (mode,), timeout=20)
        if result.returncode != 0:
            text = redact((result.stderr.strip() or result.stdout.strip()))
            raise ApiError(400, text or f'The usage tool exited {result.returncode}')
        return {**self.read(), 'message': redact(result.stdout.strip())}
