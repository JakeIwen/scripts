#!/usr/bin/env python3
"""Fixed dashboard and shell entry points for backup scheduling policy."""
import json
import os
import subprocess
import sys

import backup_priority as priority
import mac_capture_control as capture
from cloud_backup_control import control


def change(mode, command=subprocess.run):
    selected = priority.set_policy(mode)
    if selected != priority.PriorityMode.NORMAL:
        job = priority.JOBS[selected]
        # Existing disk jobs finish safely. A cloud peer yields at its next
        # guard check; no manual pause record is created for the peer.
        control('resume', directory=job.directory, service=job.service, command=command)
    return {'ok': True}


def main(args=None):
    os.umask(0o077)
    args = sys.argv[1:] if args is None else args
    if args == ['--status']:
        result = {**priority.status(), 'capture': capture.status()}
    elif len(args) == 2 and args[0] == '--set':
        result = change(args[1])
    elif args == ['--request-capture']:
        if priority.policy() == priority.PriorityMode.PI:
            raise ValueError('Select Mac or normal backup priority before requesting a Mac capture.')
        eligible, reason = priority.eligibility(priority.PriorityMode.TIME_MACHINE)
        if not eligible:
            raise ValueError(reason)
        capture.request_stop()
        job = priority.JOBS[priority.PriorityMode.TIME_MACHINE]
        control('resume', directory=job.directory, service=job.service)
        result = {'ok': True}
    elif args == ['--cancel-capture']:
        capture.cancel_stop()
        result = {'ok': True}
    elif len(args) == 2 and args[0] == '--admit':
        if not priority.allows(args[1]):
            print('Waiting for the selected cloud backup to finish.', file=sys.stderr)
            return 1
        return 0
    else:
        raise ValueError('Unsupported backup priority operation.')
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ValueError as exc:
        print(json.dumps({'ok': False, 'message': str(exc)}))
        sys.exit(2)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        print(json.dumps({'ok': False, 'message': 'Backup priority control is unavailable; refresh and retry.'}))
        sys.exit(1)
