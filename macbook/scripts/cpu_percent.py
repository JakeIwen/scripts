#!/usr/bin/env python3
"""Print the whole-machine CPU busy percentage (0-100, all cores) as one line, e.g. `37%`.

Run by BetterTouchTool's Mac Panel menu-bar item every few seconds, so it reads
the kernel's per-CPU tick counters (Mach host_processor_info) and diffs them
against the totals the previous run saved: no `top`, no `ps`, no sleeping
(unless there is no recent sample). Stdlib only, Python 3.9+.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
import time
from pathlib import Path

PLACEHOLDER = "–%"
PROCESSOR_CPU_LOAD_INFO = 2
CPU_STATE_IDLE = 2
CPU_STATES = 4  # user, system, idle, nice
STATE_FILE = Path.home() / ".local/state/cpu-percent/last-sample.json"
# A sample older than this no longer describes "the last few seconds".
MAX_SAMPLE_AGE = 30.0
FALLBACK_GAP = 0.25


def read_ticks():
    """Return (idle, total) ticks summed over every CPU since boot."""
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    libc.mach_host_self.restype = ctypes.c_uint
    libc.host_processor_info.argtypes = [
        ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint)]
    libc.vm_deallocate.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_size_t]
    cpus, info, count = ctypes.c_uint(), ctypes.c_void_p(), ctypes.c_uint()
    status = libc.host_processor_info(libc.mach_host_self(), PROCESSOR_CPU_LOAD_INFO,
                                      ctypes.byref(cpus), ctypes.byref(info), ctypes.byref(count))
    if status != 0 or not info.value:
        raise OSError("host_processor_info failed (%d)" % status)
    try:
        values = (ctypes.c_int * count.value).from_address(info.value)
        # Counters are unsigned 32-bit ticks that may wrap; mask to keep them non-negative.
        ticks = [v & 0xFFFFFFFF for v in values]
    finally:
        task = ctypes.c_uint.in_dll(libc, "mach_task_self_")
        libc.vm_deallocate(task.value, info, count.value * ctypes.sizeof(ctypes.c_int))
    idle = sum(ticks[CPU_STATE_IDLE::CPU_STATES])
    return idle, sum(ticks)


def busy_percent(previous, current):
    """Busy % between two (idle, total) samples; None when no time passed or counters went back."""
    idle = current[0] - previous[0]
    total = current[1] - previous[1]
    if total <= 0 or idle < 0:
        return None
    return max(0.0, min(100.0, 100.0 * (1.0 - idle / total)))


def format_percent(value):
    return PLACEHOLDER if value is None else "%d%%" % int(round(max(0.0, min(100.0, value))))


def load_previous(path, now):
    """The saved (idle, total) sample, or None if missing, corrupt or stale."""
    try:
        data = json.loads(Path(path).read_text())
        idle, total, taken = int(data["idle"]), int(data["total"]), float(data["time"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not 0 <= now - taken <= MAX_SAMPLE_AGE:
        return None
    return idle, total


def save_sample(path, sample, now):
    """Atomic write (temp file + rename) so a concurrent run never reads half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".sample-")
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump({"idle": sample[0], "total": sample[1], "time": now}, handle)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def measure(path=STATE_FILE, reader=read_ticks, clock=time.time, sleeper=time.sleep):
    """Percent since the previous run; falls back to a short two-sample window."""
    now = clock()
    current = reader()
    previous = load_previous(path, now)
    if previous is None:
        sleeper(FALLBACK_GAP)
        previous, now, current = current, clock(), reader()
    try:
        save_sample(path, current, now)
    except OSError:
        pass  # Without state every run just pays the short fallback window.
    return busy_percent(previous, current)


def main():
    try:
        print(format_percent(measure()))
    except Exception:
        print(PLACEHOLDER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
