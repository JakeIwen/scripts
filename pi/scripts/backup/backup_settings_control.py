#!/usr/bin/env python3
"""Bounded JSON writes; stale revisions use the standard EX_TEMPFAIL exit status."""
import datetime as dt
import json
import os
import sys

import backup_settings as settings


def main(args=None):
    os.umask(0o077)
    args = sys.argv[1:] if args is None else args
    if args == ['--shell']:
        print(settings.shell_overrides())
        return 0
    if args == ['--window']:
        values = settings.effective()
        start, end, hour = values['disk.start'], values['disk.end'], dt.datetime.now().hour
        return 0 if (start <= hour < end if start < end else hour >= start or hour < end) else 3
    if args == ['--status']:
        result = settings.snapshot()
    elif args == ['--local']:
        result = settings.local_projection(settings.effective())
    elif args == ['--save']:
        raw = sys.stdin.read(16385)
        if len(raw) > 16384:
            raise ValueError('Settings request is too large.')
        result = settings.save(json.loads(raw))
    else:
        raise ValueError('Unsupported backup settings operation.')
    print(json.dumps({'ok': True, 'settings': result}, allow_nan=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except settings.SettingsConflict as exc:
        print(json.dumps({'ok': False, 'message': str(exc)}))
        sys.exit(os.EX_TEMPFAIL)
    except ValueError as exc:
        print(json.dumps({'ok': False, 'message': str(exc)}))
        sys.exit(2)
    except (OSError, KeyError, TypeError):
        print(json.dumps({'ok': False, 'message': 'Backup settings are unavailable.'}))
        sys.exit(1)
