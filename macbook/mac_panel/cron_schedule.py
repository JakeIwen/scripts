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
