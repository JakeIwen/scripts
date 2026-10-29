"""Validated preference overrides; defaults stay in the shared Settings record."""
import math
from . import config
from .records import Settings
from .storage import read_json, write_json
from .busy import operation


def validate(data):
    defaults = Settings().to_dict()
    if not isinstance(data, dict) or set(data) - set(defaults):
        raise ValueError('unknown setting')
    for name, value in data.items():
        default = defaults[name]
        if isinstance(default, bool) and type(value) is not bool:
            raise ValueError(f'{name} must be a boolean')
        if isinstance(default, str) and not isinstance(value, str):
            raise ValueError(f'{name} must be text')
        if isinstance(default, float) and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError(f'{name} must be a finite number')
    return Settings.from_dict(data)


def load():
    try:
        return validate(read_json(config.SETTINGS_PATH))
    except FileNotFoundError:
        return Settings()


def save(settings):
    value = validate(settings.to_dict())
    defaults = Settings().to_dict()
    write_json(config.SETTINGS_PATH, {k: v for k, v in value.to_dict().items() if v != defaults[k]})
    return value


def update(assignments):
    with operation():
        data = load().to_dict()
        for assignment in assignments:
            key, separator, value = assignment.partition('=')
            if not separator or key not in data:
                raise ValueError('settings require known-key=value')
            if isinstance(data[key], bool):
                if value.lower() not in ('true', 'false'):
                    raise ValueError(f'{key} must be true or false')
                data[key] = value.lower() == 'true'
            elif isinstance(data[key], (int, float)):
                data[key] = float(value)
            else:
                data[key] = value
        return save(validate(data))
