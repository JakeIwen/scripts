"""LaunchAgent configuration and read-only supervision decision."""
import os
from pathlib import Path
import plistlib
import re

from panel_paths import CHILD_PATH, DATA, DIRECTORY, LABEL, LOG, PYTHON, ROOT


def configuration(root=ROOT, python=PYTHON):
    data = root / 'tmp/mac-panel'
    return {
        'Label': LABEL,
        'ProgramArguments': [str(python), str(root / 'macbook/mac_panel/server.py')],
        'WorkingDirectory': str(root),
        'EnvironmentVariables': {'PATH': CHILD_PATH, 'PYTHONPATH': str(root / 'shared/python')},
        'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 10,
        'ExitTimeOut': 20, 'ProcessType': 'Background', 'Umask': 63,
        'StandardOutPath': str(data / 'panel.log'), 'StandardErrorPath': str(data / 'panel.log'),
    }


def assert_plist_identity(path, root=ROOT, uid=None):
    uid = os.getuid() if uid is None else uid
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.stat().st_uid != uid:
        raise RuntimeError('Installed plist belongs to another user or is a symlink')
    value = plistlib.loads(path.read_bytes())
    args = value.get('ProgramArguments', [])
    if (value.get('Label') != LABEL or value.get('WorkingDirectory') != str(root)
            or not isinstance(args, list) or len(args) != 2
            or args[1] != str(root / 'macbook/mac_panel/server.py')
            or value.get('UserName') is not None):
        raise RuntimeError('Installed plist belongs to another checkout or user')


def parse_enabled(text):
    match = re.search(r'^\s*"' + re.escape(LABEL) + r'"\s*=>\s*(\w+)\s*$', text, re.M)
    if match is None:
        if 'disabled services = {' not in text:
            raise RuntimeError('Unrecognized launchctl print-disabled output')
        return True
    if match[1] not in ('true', 'false', 'enabled', 'disabled'):
        raise RuntimeError('Unrecognized launchd enabled state')
    return match[1] in ('false', 'enabled')


def supervision_status(installed, configured, enabled, loaded, job_pid, health, record):
    healthy = bool(installed and configured and enabled and loaded and job_pid
                   and isinstance(health, dict) and health.get('ok') is True
                   and record and job_pid == health.get('pid') == record.get('pid')
                   and health.get('instance') == record.get('instance'))
    return {'installed': installed, 'configuration_ok': configured, 'enabled': enabled,
            'loaded': loaded, 'pid': job_pid, 'healthy': healthy,
            'instance': health.get('instance') if health else None}
