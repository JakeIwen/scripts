"""One supervised verifier follows completed immutable-object uploads.

The verifier inherits the existing backup lock. It never writes worker state or
publishes a recovery point; the parent still performs the final inventory check.
"""
from collections import deque
from contextlib import suppress
from enum import Enum
import multiprocessing
import os
from queue import Empty, Full
import signal
import time
from typing import TypedDict

import icloud_backup as cloud
from icloud_inventory import parse_inventory
from icloud_progress import TransferActivity
from time_machine_downloads import download_batch, VerificationMeter
from time_machine_staging import JOB_LOCK, require_lock
from time_machine_verification import ObjectVerifier


class ResultKind(str, Enum):
    COMPLETE = 'complete'
    DEFERRED = 'deferred'
    AUTHENTICATION = 'authentication'
    ERROR = 'error'
    OS_ERROR = 'os-error'


class VerifierResult(TypedDict):
    kind: str
    message: str
    errno: int | None


class Follower:
    def __init__(self, cfg, root, expected, rows, parent_pid, completed, updates, done):
        self.cfg = {**cfg, 'bandwidth_limit': cfg.get('verification_bandwidth_limit', cfg['bandwidth_limit'])}
        self.remote = cfg['remote'] + '/objects'
        self.verifier = ObjectVerifier(expected, root / 'verified-objects.json')
        self.verifier.observe(rows)
        self.rows = rows
        self.ready = {k for k in expected if self.verifier.available(k, rows)}
        self.parent_pid, self.completed, self.updates, self.done = parent_pid, completed, updates, done
        self.last_report = 0
        self.last_inventory = 0
        self.meter = VerificationMeter()
        self.downloaded = 0
        self.batch_start = 0

    def check(self):
        if os.getppid() != self.parent_pid:
            raise cloud.Deferred('verification supervisor stopped; saved checkpoints retained')
        cloud.check_work_allowed(self.cfg)

    def report(self, counters=None, waiting=False, force=False):
        self.check()
        now = time.monotonic()
        counters = counters or {}
        if not force and now - self.last_report < self.cfg['guard_interval_seconds']:
            return
        telemetry = self.meter.sample(self.downloaded + counters.get('bytes', 0) if counters else None)
        checked = self.verifier.counters()
        amount = max(0, counters.get('bytes', 0) - (checked['verified_bytes'] - self.batch_start))
        values = {**checked, **telemetry, 'current_file_bytes': amount,
                  'verification_ready_files': len(self.ready),
                  'verification_idle_seconds': counters.get('idle_seconds', 0),
                  'verification_waiting': waiting}
        with suppress(Full):
            self.updates.put_nowait(values)
        self.last_report = now

    def network(self, operation, path, *args, **kwargs):
        self.check()
        return cloud.run([cloud.RCLONE, operation, path, *args], self.cfg, network=True,
                         progress=lambda _: self.activity(TransferActivity.INVENTORY),
                         on_activity=self.activity, **kwargs)

    def activity(self, value):
        self.meter.activity = value
        self.report(waiting=value == TransferActivity.WAITING, force=True)

    def inventory(self):
        self.rows = parse_inventory(self.network('lsjson', self.remote, '--recursive', '--files-only'))
        self.verifier.observe(self.rows)
        self.last_inventory = time.monotonic()

    def drain(self):
        while True:
            try:
                digest = self.completed.get_nowait()
            except Empty:
                return
            if digest in self.verifier.expected:
                self.ready.add(digest)

    def run(self):
        self.report(force=True)
        while True:
            self.check()
            self.drain()
            pending = self.ready - self.verifier.verified.keys()
            available = [k for k in pending if self.verifier.available(k, self.rows)]
            if available:
                # Follow the sorted upload list rather than spending the
                # catch-up window on hundreds of tiny metadata requests.
                names = sorted(available)[:128]
                self.batch_start = self.verifier.counters()['verified_bytes']
                download_batch(self.cfg, self.remote, names, self.verifier, self.rows,
                               lambda counters: self.report(counters, force=True), self.activity)
                self.downloaded = max(self.meter.amount,
                    self.downloaded + sum(self.verifier.expected[k]['bytes'] for k in names))
                self.meter.sample(self.downloaded)
                self.report(force=True)
            elif self.done.is_set():
                # The parent now checks the complete inventory, including any
                # completion messages omitted by an older/different rclone.
                self.report(force=True)
                return
            elif pending and time.monotonic() - self.last_inventory >= 15:
                self.inventory()
            else:
                self.meter.activity = TransferActivity.WAITING if not pending else TransferActivity.INVENTORY
                self.report(waiting=not pending)
                time.sleep(0.25)


def verification_worker(cfg, root, expected, rows, parent_pid, completed, updates, done,
                        result_reader, result_writer, lock_fd, lock_path):
    result_reader.close()
    result: VerifierResult = {'kind': ResultKind.COMPLETE, 'message': '', 'errno': None}
    try:
        require_lock(lock_fd, lock_path)
        with suppress(PermissionError):
            os.nice(4)
        signal.signal(signal.SIGTERM, cloud.abort)
        signal.signal(signal.SIGINT, cloud.abort)
        Follower(cfg, root, expected, rows, parent_pid, completed, updates, done).run()
    except cloud.AuthenticationRequired as exc:
        result.update(kind=ResultKind.AUTHENTICATION, message=str(exc))
    except cloud.Deferred as exc:
        result.update(kind=ResultKind.DEFERRED, message=str(exc))
    except OSError as exc:
        result.update(kind=ResultKind.OS_ERROR, message=type(exc).__name__, errno=exc.errno)
    except Exception as exc:
        result.update(kind=ResultKind.ERROR, message=str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__)
    finally:
        cloud.stop_child()
        with suppress(BrokenPipeError, OSError):
            result_writer.send(result)
        result_writer.close()


class VerificationPipeline:
    def __init__(self, cfg, root, expected, rows, *, lock_fd=9, lock_path=JOB_LOCK):
        if cloud.CHILD is not None:
            raise RuntimeError('start verification before launching the upload client')
        require_lock(lock_fd, lock_path)
        self.cfg, self.expected = cfg, expected
        context = multiprocessing.get_context('fork')
        self.completed = context.Queue(maxsize=16)
        self.updates = context.Queue(maxsize=2)
        self.done = context.Event()
        self.reader, writer = context.Pipe(duplex=False)
        self.pending, self.sent = deque(), set()
        self.latest, self.result = {}, None
        self.process = context.Process(target=verification_worker,
            args=(cfg, root, expected, rows, os.getpid(), self.completed, self.updates, self.done,
                  self.reader, writer, lock_fd, lock_path), daemon=True)
        self.process.start()
        writer.close()

    def completed_object(self, digest):
        if digest in self.expected and digest not in self.sent:
            self.sent.add(digest)
            self.pending.append(digest)
        self.flush()

    def flush(self):
        while self.pending:
            try:
                self.completed.put_nowait(self.pending[0])
            except Full:
                return
            self.pending.popleft()

    def progress(self):
        self.flush()
        while True:
            try:
                self.latest = self.updates.get_nowait()
            except Empty:
                break
        stopped = self.process.exitcode is not None
        if self.result is None and (self.reader.poll() or stopped):
            try:
                self.result = self.reader.recv()
            except EOFError:
                raise RuntimeError('background verification worker stopped unexpectedly') from None
        if self.result is not None:
            kind = self.result['kind']
            message = self.result['message']
            if kind == ResultKind.AUTHENTICATION: raise cloud.AuthenticationRequired(message)
            if kind == ResultKind.DEFERRED: raise cloud.Deferred(message)
            if kind == ResultKind.OS_ERROR: raise OSError(self.result['errno'], 'background verification storage error')
            if kind != ResultKind.COMPLETE: raise RuntimeError(message)
            if not self.done.is_set(): raise RuntimeError('background verification ended before upload finished')
        elif stopped:
            raise RuntimeError('background verification worker stopped unexpectedly')
        return {**self.latest, 'verification_copy_events': len(self.sent)}

    def finish(self, report):
        self.done.set()
        last_report = 0
        while self.result is None:
            cloud.check_work_allowed(self.cfg)
            values = self.progress()
            if self.result is not None or time.monotonic() - last_report >= self.cfg['guard_interval_seconds']:
                report(values)
                last_report = time.monotonic()
            if self.result is None:
                time.sleep(0.25)
        self.process.join(timeout=5)

    def close(self):
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=20)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=5)
        for channel in (self.completed, self.updates):
            channel.cancel_join_thread()
            channel.close()
        self.reader.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def upload_and_follow(cfg, root, expected, rows, files, state):
    total = sum(item['bytes'] for item in expected.values())
    cloud.record_progress(state, 'uploading', parallel_verification=True,
        verified_bytes=0, verified_files=0, verification_total_bytes=total,
        verification_total_files=len(expected), current_file_bytes=0,
        verification_bytes_per_second=None, verification_idle_seconds=0,
        verification_waiting=False, **cloud.upload_progress(expected, rows, {}))
    with VerificationPipeline(cfg, root, expected, rows) as pipeline:
        def report(counters):
            cloud.record_progress(state, 'uploading', **cloud.upload_progress(expected, rows, counters),
                                  **pipeline.progress())
        cloud.run([cloud.RCLONE, 'copy', str(root / 'objects'), cfg['remote'] + '/objects',
                   '--files-from-raw', str(files), '--immutable', '--size-only', '--log-level', 'INFO'],
                  cfg, network=True, progress=report, on_completed=pipeline.completed_object)
        pipeline.finish(lambda values: cloud.record_progress(state, 'verifying',
            upload_estimated_bytes=total, command_idle_seconds=values.get('verification_idle_seconds', 0), **values))
