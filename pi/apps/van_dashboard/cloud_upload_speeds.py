"""Read a cloud backup job's 30-day upload speed samples for the graph sheet.

The root-private log is reached only through each job's fixed status helper
flag; the dashboard never opens the state directory itself.
"""
import json
import subprocess

from .van_dashboard_common import SUDO, run_command

TOOLS = {
    'pi': '/home/pi/scripts/backup/icloud_status.py',
    'time-machine': '/home/pi/scripts/backup/time_machine_icloud_status.py',
}
UINT32_MAX = 2**32 - 1


def upload_speeds(kind, command=run_command):
    """Return [[epoch, bytes_per_second], ...] oldest first; raise on bad output."""
    if kind not in TOOLS:
        raise ValueError('invalid cloud backup')
    try:
        result = command([SUDO, '-n', '/usr/bin/python3', TOOLS[kind], '--upload-speeds'], timeout=8)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('upload speed helper failed') from exc
    if result.returncode:
        raise RuntimeError('upload speed helper failed')
    try:
        samples = json.loads(result.stdout)['samples']
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError('invalid upload speed history') from exc
    if not isinstance(samples, list) or not all(
            isinstance(row, list) and len(row) == 2
            and all(type(value) is int and 0 <= value <= UINT32_MAX for value in row)
            for row in samples):
        raise RuntimeError('invalid upload speed history')
    return samples
