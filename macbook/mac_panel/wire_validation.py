"""Small runtime checks at HTTP and CLI JSON trust boundaries."""
import math
from panel_contracts import ApiError


def object_fields(value, required, optional=(), status=400):
    if not isinstance(value, dict) or not set(required) <= value.keys():
        raise ApiError(status, 'Object is missing required fields')
    if status == 400 and value.keys() - set(required) - set(optional):
        raise ApiError(status, 'Object contains unsupported fields')
    return value


def field(value, key, kind, nullable=False, status=502):
    item = value.get(key)
    valid = item is None and nullable
    if kind is int:
        valid = valid or type(item) is int
    elif kind is float:
        valid = valid or (type(item) in (int, float) and math.isfinite(item))
    else:
        valid = valid or isinstance(item, kind)
    if not valid:
        raise ApiError(status, f'Invalid {key} field')
    return item


def confirm(body):
    if body.get('confirmed') is not True:
        raise ApiError(400, 'Explicit confirmation is required')
