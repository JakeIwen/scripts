"""Cloud object identities and size-based transfer estimates."""
import json


def remote_fingerprint(row):
    if (not isinstance(row, dict) or row.get('IsDir') is not False or
            type(row.get('Size')) is not int or not isinstance(row.get('ModTime'), str) or
            not row['ModTime']):
        return None
    return {'size': row['Size'], 'mtime': row['ModTime'], 'id': row.get('ID', '')}


def parse_inventory(data):
    rows = json.loads(data)
    if not isinstance(rows, list):
        raise RuntimeError('invalid cloud inventory')
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError('invalid cloud inventory entry')
        path = row.get('Path')
        if not isinstance(path, str) or path in result:
            raise RuntimeError('ambiguous cloud inventory')
        result[path] = row
    return result


def upload_progress(expected, inventory, counters):
    """Size-based estimate includes complete objects saved by earlier attempts.

    Transferred bytes can include retries; this is never verification evidence.
    """
    total = sum(item['bytes'] for item in expected.values())
    existing = sum(item['bytes'] for path, item in expected.items()
                   if remote_fingerprint(inventory.get(path)) is not None
                   and inventory[path]['Size'] == item['bytes'])
    sent = counters.get('bytes', 0)
    return {'upload_total_bytes': total, 'upload_estimated_bytes': min(total, existing + sent),
            'upload_bytes_per_second': counters.get('speed'),
            'command_bytes': sent, 'command_idle_seconds': counters.get('idle_seconds', 0)}
