"""Per-minute I/O history for each labelled disk, kept for 30 days.

The collector averages its 5-second disk rates into one record a minute per
filesystem label, in ``disk-io/<label>.bin`` beside the event database. A
label shared by several disks (every SD card carries ``bootfs``) is skipped,
since its samples could not be told apart. The dashboard reads the files only
through the ``disk-io`` CLI command.

Each record is little-endian: the epoch second the minute ended, the mean read
and write bytes per second over it (uint32 each) and the mean busy time in
hundredths of a percent (uint16).
"""

import math
import os
import re
import struct
import tempfile

DIRECTORY_NAME = "disk-io"
SUFFIX = ".bin"
RECORD = struct.Struct("<IIIH")
SAMPLE_SECONDS = 60
RETENTION_SECONDS = 30 * 86400
MAX_HOURS = RETENTION_SECONDS / 3600
# Rewriting a month of records every minute would be wasteful; trim hourly
# and let readers filter the rest.
PRUNE_SLACK_SECONDS = 3600
# A graph wider than this many points adds nothing a phone can show.
MAX_BUCKETS = 360
BUSY_SCALE = 100
UINT32_MAX = 2**32 - 1
UINT16_MAX = 2**16 - 1
# The dashboard's DiskManager.LABEL_RE: labels double as file names here.
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def history_directory(database):
    return os.path.join(os.path.dirname(os.path.abspath(database)), DIRECTORY_NAME)


def history_path(directory, label):
    if not LABEL_RE.fullmatch(label):
        raise ValueError("invalid disk label")
    return os.path.join(directory, label + SUFFIX)


def _finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and value >= 0


def unique_label_devices(sample):
    """Map each label carried by exactly one disk to that disk's I/O entry."""
    disk_io = sample.get("disk_io") if isinstance(sample, dict) else None
    devices = disk_io.get("devices") if isinstance(disk_io, dict) else None
    owners = {}
    for device in devices if isinstance(devices, list) else ():
        if not isinstance(device, dict):
            continue
        for label in set(device.get("labels") or ()):
            if isinstance(label, str) and LABEL_RE.fullmatch(label):
                owners.setdefault(label, []).append(device)
    return {label: found[0] for label, found in owners.items() if len(found) == 1}


def _unpack(data):
    usable = len(data) - len(data) % RECORD.size
    return list(RECORD.iter_unpack(data[:usable]))


def _prune(path, now):
    with open(path, "rb") as handle:
        data = handle.read()
    kept = [row for row in _unpack(data) if now - RETENTION_SECONDS <= row[0]]
    fd, name = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(b"".join(RECORD.pack(*row) for row in kept))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def append(directory, label, at, read, write, busy_percent):
    path = history_path(directory, label)
    os.makedirs(directory, mode=0o750, exist_ok=True)
    record = RECORD.pack(
        int(at),
        min(UINT32_MAX, max(0, round(read))),
        min(UINT32_MAX, max(0, round(write))),
        min(UINT16_MAX, max(0, round(busy_percent * BUSY_SCALE))),
    )
    with open(path, "ab") as handle:
        size = handle.tell()
        if size % RECORD.size:
            # A torn earlier write would misalign every later record.
            handle.truncate(size - size % RECORD.size)
            handle.seek(0, os.SEEK_END)
        handle.write(record)
    with open(path, "rb") as handle:
        first = handle.read(RECORD.size)
    if len(first) == RECORD.size and RECORD.unpack(first)[0] < at - RETENTION_SECONDS - PRUNE_SLACK_SECONDS:
        _prune(path, at)


class DiskHistoryRecorder:
    """Average each disk's 5-second rates into one appended record a minute."""

    def __init__(self, directory, interval=SAMPLE_SECONDS):
        self.directory = directory
        self.interval = interval
        self.period_start = None
        self.last_at = None
        self.totals = {}
        self.reported_failure = False

    def observe(self, sample):
        """Add one sample, first closing the minute it falls outside; return records written."""
        at = sample.get("timestamp")
        written = self.flush() if _finite(at) and self.ready(at) else 0
        self.add(sample)
        return written

    def add(self, sample):
        at = sample.get("timestamp")
        if not _finite(at):
            return
        if self.period_start is None:
            self.period_start = at
        self.last_at = at
        for label, device in unique_label_devices(sample).items():
            values = (
                device.get("read_bytes_per_second"),
                device.get("write_bytes_per_second"),
                device.get("busy_percent"),
            )
            # The first sample after a disk appears has no previous counters.
            if not all(_finite(value) for value in values):
                continue
            entry = self.totals.setdefault(label, [0.0, 0.0, 0.0, 0])
            for index, value in enumerate(values):
                entry[index] += value
            entry[3] += 1

    def ready(self, now):
        # A backwards clock step (NTP after a cold boot) also closes the minute.
        return self.period_start is not None and not 0 <= now - self.period_start < self.interval

    def flush(self):
        """Append one record per disk seen this minute; return how many were written."""
        totals, at = self.totals, self.last_at
        self.totals, self.period_start, self.last_at = {}, None, None
        written = 0
        for label, (read, write, busy, count) in sorted(totals.items()):
            try:
                append(self.directory, label, at, read / count, write / count, busy / count)
                written += 1
            except OSError as error:
                # The graph is optional; a lost minute must never stop the monitor.
                if not self.reported_failure:
                    print(f"system-event-monitor: disk history write failed: {error}", flush=True)
                    self.reported_failure = True
        return written


def read_records(directory, label, now):
    """Records from the last 30 days, oldest first; a disk never seen has none."""
    try:
        with open(history_path(directory, label), "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        return []
    cutoff = now - RETENTION_SECONDS
    return sorted(row for row in _unpack(data) if cutoff <= row[0] <= now + 86400)


def bucket_seconds(span_seconds):
    return max(1, math.ceil(span_seconds / SAMPLE_SECONDS / MAX_BUCKETS)) * SAMPLE_SECONDS


def history(directory, label, hours, now):
    """Bucketed history for one disk over the last ``hours``, for the dashboard graph."""
    if not 0 < hours <= MAX_HOURS:
        raise ValueError(f"hours must be above 0 and at most {MAX_HOURS:g}")
    span = hours * 3600
    since = now - span
    size = bucket_seconds(span)
    buckets = {}
    for at, read, write, busy in read_records(directory, label, now):
        if not since < at <= now:
            continue
        # Buckets align to the epoch so a refresh never shifts their edges.
        start = int((at - 1) // size * size)
        bucket = buckets.setdefault(start, [0, 0, 0, 0, 0, 0, 0])
        bucket[0] += 1
        bucket[1] += read * SAMPLE_SECONDS
        bucket[2] += write * SAMPLE_SECONDS
        bucket[3] = max(bucket[3], read)
        bucket[4] = max(bucket[4], write)
        bucket[5] += busy
        bucket[6] = max(bucket[6], busy)
    return {
        "ok": True,
        "label": label,
        "since": int(since),
        "until": int(now),
        "sample_seconds": SAMPLE_SECONDS,
        "bucket_seconds": size,
        "buckets": [
            {
                "start": start,
                "minutes": minutes,
                "read_bytes": read_bytes,
                "write_bytes": write_bytes,
                "peak_read_bytes_per_second": peak_read,
                "peak_write_bytes_per_second": peak_write,
                "busy_percent": round(busy_total / minutes / BUSY_SCALE, 2),
                "peak_busy_percent": round(peak_busy / BUSY_SCALE, 2),
            }
            for start, (
                minutes, read_bytes, write_bytes, peak_read, peak_write, busy_total, peak_busy
            ) in sorted(buckets.items())
        ],
    }


def latest(directory, now):
    """The most recent minute recorded for every disk with a history file."""
    try:
        names = sorted(os.listdir(directory))
    except FileNotFoundError:
        names = []
    disks = {}
    for name in names:
        label = name[: -len(SUFFIX)]
        if not name.endswith(SUFFIX) or not LABEL_RE.fullmatch(label):
            continue
        try:
            with open(os.path.join(directory, name), "rb") as handle:
                size = handle.seek(0, os.SEEK_END)
                handle.seek(max(0, size - size % RECORD.size - RECORD.size))
                tail = _unpack(handle.read(RECORD.size))
        except OSError:
            continue
        if not tail or not now - RETENTION_SECONDS <= tail[0][0] <= now + 86400:
            continue
        at, read, write, busy = tail[0]
        disks[label] = {
            "at": at,
            "read_bytes_per_second": read,
            "write_bytes_per_second": write,
            "busy_percent": round(busy / BUSY_SCALE, 2),
        }
    return {"ok": True, "sample_seconds": SAMPLE_SECONDS, "disks": disks}
