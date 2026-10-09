"""Bounded batches reuse one cloud client while checkpointing each full hash."""
from collections import deque
from pathlib import Path
import re
import tempfile
import time
from typing import TypedDict

import icloud_backup as cloud
from icloud_progress import TransferActivity


class VerificationTelemetry(TypedDict):
    verification_activity: str
    verification_bytes_per_second: float | None
    verification_waiting: bool
    verification_updated_at: float


class VerificationMeter:
    """A rolling effective download rate includes connection and guard waits."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.samples = deque([(clock(), 0)])
        self.amount = 0
        self.activity = TransferActivity.CONNECTING

    def sample(self, amount=None):
        now = self.clock()
        if amount is not None:
            self.activity = (TransferActivity.DOWNLOADING if amount > self.amount
                             else TransferActivity.CONNECTING)
            self.amount = max(self.amount, amount)
        self.samples.append((now, self.amount))
        while len(self.samples) > 2 and self.samples[1][0] <= now - 60:
            self.samples.popleft()
        start, before = self.samples[0]
        rate = (self.amount - before) / max(0.001, now - start) if now > start else None
        return VerificationTelemetry(verification_activity=self.activity,
            verification_bytes_per_second=rate, verification_updated_at=time.time(),
            verification_waiting=self.activity == TransferActivity.WAITING)


class HashReport:
    def __init__(self, stream, names, verifier, rows):
        self.stream, self.names, self.verifier, self.rows = stream, set(names), verifier, rows
        self.checked = set()

    def consume(self):
        while True:
            offset = self.stream.tell()
            line = self.stream.readline(132)
            if not line:
                return
            if not line.endswith(b'\n') and len(line) < 132:
                self.stream.seek(offset)
                return
            match = re.fullmatch(rb'([0-9a-f]{64})  ([0-9a-f]{64})\n', line)
            if match is None:
                raise RuntimeError('invalid downloaded SHA-256 report')
            actual, digest = (value.decode('ascii') for value in match.groups())
            if digest not in self.names:
                raise RuntimeError('unexpected object in downloaded SHA-256 report')
            if digest not in self.checked:
                self.verifier.accept_hash(digest, self.rows[digest], actual)
                self.checked.add(digest)


def download_batch(cfg, remote, names, verifier, rows, progress, activity):
    # --download hashes the entire received stream inside rclone; stdout/report
    # never contains file contents. One client reuses its authenticated backend.
    with tempfile.TemporaryDirectory(prefix='tm-download-check-') as directory:
        base = Path(directory)
        files, hashes = base / 'files', base / 'hashes'
        files.write_text(''.join(name + '\n' for name in names))
        hashes.touch(mode=0o600)
        with hashes.open('rb') as stream:
            report = HashReport(stream, names, verifier, rows)
            def update(counters):
                report.consume()
                progress(counters)
            cloud.run([cloud.RCLONE, 'hashsum', 'SHA256', remote, '--download',
                       '--files-from-raw', str(files), '--output-file', str(hashes),
                       '--transfers', '1'], cfg, network=True, progress=update,
                      on_activity=activity)
            report.consume()
            if report.checked != set(names):
                raise RuntimeError('incomplete downloaded SHA-256 report')
