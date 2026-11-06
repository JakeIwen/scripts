"""Describe common five-field cron schedules without guessing unfamiliar ones."""
import re

DAYS = ('Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday')
MACROS = {'@reboot': 'At cron startup (usually boot)', '@yearly': 'Yearly on January 1 at 00:00',
          '@annually': 'Yearly on January 1 at 00:00', '@monthly': 'Monthly on day 1 at 00:00',
          '@weekly': 'Weekly on Sunday at 00:00', '@daily': 'Daily at 00:00',
          '@midnight': 'Daily at 00:00', '@hourly': 'Hourly at :00'}


def day_name(value):
    if value.isdecimal() and 0 <= int(value) <= 7:
        return DAYS[int(value) % 7]
    return next((day for day in DAYS if value.lower() == day[:3].lower()), None)


def day_words(expression, weekly=False):
    pieces = []
    for part in expression.split(','):
        ends = part.split('-')
        if len(ends) not in (1, 2):
            return None
        if weekly:
            names = [day_name(end) for end in ends]
        else:
            names = [end if end.isdecimal() and 1 <= int(end) <= 31 else None for end in ends]
        if not all(names):
            return None
        pieces.append('–'.join(names))
    return ', '.join(pieces)


def describe_schedule(expression):
    if expression.startswith('@'):
        return MACROS.get(expression.lower(), expression)
    parts = expression.split()
    if len(parts) != 5:
        return expression
    minute, hour, day, month, weekday = parts
    if parts == ['*'] * 5:
        return 'Every minute'
    step = re.fullmatch(r'\*/([1-9]\d?)', minute)
    if step and 1 <= int(step[1]) <= 59 and parts[1:] == ['*'] * 4:
        return f'Every {int(step[1])} minutes'
    if not minute.isdecimal() or not 0 <= int(minute) <= 59:
        return expression
    if parts[1:] == ['*'] * 4:
        return f'Hourly at :{int(minute):02}'
    if not hour.isdecimal() or not 0 <= int(hour) <= 23 or month != '*':
        return expression
    clock = f'{int(hour):02}:{int(minute):02}'
    if day == weekday == '*':
        return f'Daily at {clock}'
    if day == '*' and (words := day_words(weekday, weekly=True)):
        return f'Weekly on {words} at {clock}'
    if weekday == '*' and (words := day_words(day)):
        return f'Monthly on day {words} at {clock}'
    return expression


MONTH_NAMES = ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec')
MACRO_FIELDS = {'@yearly': '0 0 1 1 *', '@annually': '0 0 1 1 *',
                '@monthly': '0 0 1 * *', '@weekly': '0 0 * * 0',
                '@daily': '0 0 * * *', '@midnight': '0 0 * * *', '@hourly': '0 * * * *'}


def field_values(expression, low, high, names=()):
    def number(word):
        if word.lower() in names:
            return names.index(word.lower()) + low
        return int(word)
    values = set()
    for part in expression.split(','):
        pieces = part.split('/')
        if len(pieces) > 2:
            raise ValueError('Invalid cron step')
        base = pieces[0]
        step = int(pieces[1]) if len(pieces) == 2 else 1
        if step < 1:
            raise ValueError('Invalid cron step')
        if base == '*':
            first, last = low, high
        elif '-' in base:
            first, last = map(number, base.split('-'))
        else:
            first = number(base)
            last = high if len(pieces) == 2 else first
        if not low <= first <= last <= high:
            raise ValueError('Invalid cron range')
        values.update(range(first, last + 1, step))
    return values


def next_runs(fields, start, end, limit=500):
    """Epochs in [start, end), in local time; invalid expressions have no projection.

    Advancing real minutes avoids inventing a time during the spring DST gap.
    Cron's restricted day-of-month and day-of-week fields are alternatives (OR).
    """
    import math
    import time
    parts = fields.split() if isinstance(fields, str) else list(fields)
    if len(parts) == 1:
        parts = MACRO_FIELDS.get(parts[0].lower(), '').split()
    if len(parts) != 5 or limit <= 0:
        return []
    try:
        minute = field_values(parts[0], 0, 59)
        hour = field_values(parts[1], 0, 23)
        day = field_values(parts[2], 1, 31)
        month = field_values(parts[3], 1, 12, MONTH_NAMES)
        weekday = {v % 7 for v in field_values(parts[4], 0, 7,
                                               tuple(d[:3].lower() for d in DAYS))}
    except (ValueError, TypeError):
        return []
    result = []
    moment = math.ceil(start / 60) * 60
    while moment < end and len(result) < min(limit, 500):
        local = time.localtime(moment)
        dom, dow = local.tm_mday in day, (local.tm_wday + 1) % 7 in weekday
        # A leading wildcard (including */N) retains cron's wildcard day semantics.
        day_match = dom and dow if parts[2].startswith('*') or parts[4].startswith('*') else dom or dow
        if local.tm_min in minute and local.tm_hour in hour and local.tm_mon in month and day_match:
            result.append(float(moment))
        moment += 60
    return result
