"""The private state directory: ownership checks, atomic JSON, flock probes."""
import fcntl
import json
import os
import stat
import tempfile
import time
from contextlib import contextmanager

from . import config


class StateDirError(RuntimeError):
    pass


class StateDir:
    def __init__(self, path):
        self.path = path
        self.snapshot = os.path.join(path, "snapshot.json")
        self.heartbeat = os.path.join(path, "heartbeat")
        self.watch_lock = os.path.join(path, "watch.lock")
        self.watch_info = os.path.join(path, "watch.json")
        self.spawn_lock = os.path.join(path, "spawn.lock")
        self.spawn_info = os.path.join(path, "spawn.json")
        self.failures = os.path.join(path, "failures.json")
        self.socket = os.path.join(path, "ctl.sock")
        self.log = os.path.join(path, "watch.log")

    @classmethod
    def open(cls, create=True):
        path = os.path.abspath(os.path.expanduser(
            os.environ.get(config.STATE_DIR_ENV) or config.DEFAULT_STATE_DIR))
        if create:
            os.makedirs(path, mode=0o700, exist_ok=True)
        try:
            info = os.lstat(path)
        except OSError as error:
            raise StateDirError("state dir unavailable: %s" % error)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise StateDirError("state dir must be a real directory owned by you with mode 0700")
        return cls(path)

    def check_socket_path(self):
        if len(os.fsencode(self.socket)) >= 100:
            raise StateDirError("state dir path too long for a unix socket")

    def write_json(self, path, data):
        descriptor, temporary = tempfile.mkstemp(prefix=".tmp-", dir=self.path)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(data, output, allow_nan=False)
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def read_json(self, path):
        try:
            with open(path, encoding="utf-8") as source:
                data = json.load(source)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def touch_heartbeat(self):
        with open(self.heartbeat, "a"):
            pass
        os.utime(self.heartbeat, None)

    def heartbeat_age(self):
        try:
            return time.time() - os.stat(self.heartbeat).st_mtime
        except OSError:
            return float("inf")

    def watcher_alive(self):
        """A shared probe: it never blocks other probes, and a starting watcher retries its
        exclusive lock briefly, so a probe cannot make it give up."""
        descriptor = os.open(self.watch_lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True
        finally:
            os.close(descriptor)

    @contextmanager
    def try_lock(self, path):
        """Yield True when the exclusive non-blocking lock was taken (released on exit)."""
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            yield True
        finally:
            os.close(descriptor)

    def record_failure(self, message, now):
        from .records import backoff_seconds
        count = int((self.read_json(self.failures) or {}).get("count", 0)) + 1
        self.write_json(self.failures, {"count": count, "last_error": message,
                                        "next_start_at": now + backoff_seconds(count)})

    def clear_failures(self):
        try:
            os.unlink(self.failures)
        except FileNotFoundError:
            pass
