#!/usr/bin/env python3
"""Rotate either bounded RAM syslog stream (three old files plus active).

Called by rsyslog omfile after closing its output, not by a concurrent timer.
The fixed stream argument chooses a RAM path; newer rsyslog versions may append
that same filename. This helper never accesses flash or SD history.
"""

import os
from pathlib import Path
import stat
import sys

RAM_SPOOL = Path('/run/vanpi-network/spool')
STREAMS = {'legacy': 'dendelion.log', 'json': 'network.jsonl'}


def rotate(path):
    path = Path(path)
    candidates = [path] + [path.with_name(path.name + f".{i}") for i in range(1, 4)]
    # Validate every exact destination before changing anything; never follow
    # links or recursively remove an unexpected directory.
    for candidate in candidates:
        try:
            mode = candidate.lstat().st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(mode):
            raise ValueError(f"rotation target is not a regular file: {candidate}")
    if not path.exists():
        return
    candidates[-1].unlink(missing_ok=True)
    for index in range(2, -1, -1):
        if candidates[index].exists():
            os.replace(candidates[index], candidates[index + 1])


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3) or sys.argv[1] not in STREAMS:
        raise SystemExit("usage: rotate_network_log.py legacy|json")
    target = RAM_SPOOL / STREAMS[sys.argv[1]]
    if len(sys.argv) == 3 and sys.argv[2] != str(target):
        raise SystemExit("refusing unexpected syslog filename")
    rotate(target)
