#!/usr/bin/python3
"""Inventory the Mac's job logs and rotate them in place so none grows without bound.

Finds logs from LaunchAgent plists, the tracked crontab, scan roots and extras
(see log_rotate/sources.py); rotates any over its limit by copy-truncate, keeping
`<log>.N.gz` archives beside it. Log contents are never printed.

Defaults are declared in shared/settings/log-rotate.json (saved in
~/.config/jacobr/log-rotate.json); per-file policies, extras and ignores are in
~/.config/jacobr/log-rotate-files.json; state is in ~/.local/state/log-rotate/.
Needs PYTHONPATH=shared/python (tool_settings, tool_automation).

  log_rotate.py inventory [--json]
  log_rotate.py rotate [--only PATH] [--force] [--json]
  log_rotate.py policy --revision REV PATH [--max-mb N] [--keep N]
                       [--compress on|off] [--enabled on|off] [--clear]
  log_rotate.py add|ignore|unignore --revision REV PATH
  log_rotate.py status [--json]

Exit status: 0 ok, 1 error (also a rotate that had per-log errors), 2 usage,
3 stale revision, 75 busy (another run or edit holds the lock).
"""
import sys

from log_rotate.cli import main

if __name__ == "__main__":
    sys.exit(main())
