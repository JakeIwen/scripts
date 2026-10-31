"""The packaged consumer registry is the sole id/host/handling vocabulary."""
from functools import lru_cache
import json
from pathlib import Path

from .records import Consumer, Host


@lru_cache(maxsize=1)
def consumers():
    values = json.loads(Path(__file__).with_name('consumers.json').read_text(encoding='utf-8'))
    if not isinstance(values, list) or not values:
        raise ValueError('consumer registry must be a nonempty array')
    rows = [Consumer.from_dict(value) for value in values]
    result = {row.id: row for row in rows}
    if len(result) != len(rows):
        raise ValueError('duplicate consumer id')
    return result


def consumer(consumer_id, host=None):
    row = consumers().get(consumer_id)
    if row is None or (host is not None and row.host != Host(host)):
        raise ValueError('unknown consumer for this host')
    return row


def validate_policy(policy, host):
    for consumer_id in [*policy.approvals, *policy.exempt]:
        consumer(consumer_id, host)
    return policy
