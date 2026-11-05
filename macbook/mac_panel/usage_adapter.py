"""Read only the usage tool's sanitized cached facts through the CLI boundary."""
from datetime import datetime

from cli_runner import json_result
from panel_contracts import ApiError, UsagePayload
from wire_validation import field, object_fields

COMMAND = ('/usr/bin/python3', '-I', '-B',
           '/Users/jacobr/config/mac/usage-budget/usage_budget.py', 'facts', '--json')
WINDOWS = ('claude_weekly', 'claude_session', 'fable_weekly', 'codex_weekly', 'codex_session')
POSITION_FIELDS = ('left', 'elapsed_pct', 'line_left', 'ahead', 'resets_in_h')
AVAILABILITY = frozenset(('ok', 'constrained', 'out', 'unknown'))
LEANS = frozenset(('gpt-heavy', 'balanced', 'conserve-claude', 'conserve-gpt', 'conserve-both'))


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Missing timezone')
    except ValueError:
        raise ApiError(502, 'Invalid usage timestamp') from None
    return value


def decode_usage(value) -> UsagePayload:
    required = ('fetched_at', 'age_minutes', *WINDOWS, 'credits', 'claude_position',
                'lean', 'reasons', 'route', 'availability')
    object_fields(value, required, status=502)
    result = {'fetched_at': timestamp(field(value, 'fetched_at', str)),
              'age_minutes': field(value, 'age_minutes', float)}
    if result['age_minutes'] < 0:
        raise ApiError(502, 'Invalid usage age')
    for name in WINDOWS:
        window = field(value, name, dict, nullable=True)
        result[name] = None
        if window is not None:
            used, left = field(window, 'used', float), field(window, 'left', float)
            reset = field(window, 'resets_at', str, nullable=True)
            result[name] = {'used': used, 'left': left, 'resets_at': timestamp(reset) if reset else None}
    credits = field(value, 'credits', dict)
    result['credits'] = {key: field(credits, key, float, nullable=True)
                         for key in ('left', 'allowance_per_day', 'burn_per_day')}
    expires = field(credits, 'expires', str)
    try:
        datetime.strptime(expires, '%Y-%m-%d')
    except ValueError:
        raise ApiError(502, 'Invalid credits expiry') from None
    result['credits'].update(expires=expires, days_left=field(credits, 'days_left', float))
    position = field(value, 'claude_position', dict, nullable=True)
    result['claude_position'] = ({key: field(position, key, float) for key in POSITION_FIELDS}
                                if position is not None else None)
    result['lean'] = field(value, 'lean', str)
    reasons = field(value, 'reasons', list)
    if result['lean'] not in LEANS or not all(isinstance(item, str) for item in reasons):
        raise ApiError(502, 'Invalid usage lean or reasons')
    result['reasons'] = reasons
    route = field(value, 'route', dict)
    result['route'] = {key: field(route, key, str) for key in ('workers', 'review')}
    availability = field(value, 'availability', dict)
    result['availability'] = {key: field(availability, key, str) for key in ('claude', 'gpt')}
    if not all(state in AVAILABILITY for state in result['availability'].values()):
        raise ApiError(502, 'Invalid usage availability')
    return result


class UsageAdapter:
    def __init__(self, runner):
        self.runner = runner

    def read(self) -> UsagePayload:
        return decode_usage(json_result(self.runner.run(COMMAND, timeout=20)))
