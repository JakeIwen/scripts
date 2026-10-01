"""Verified removable storage and bounded tmpfs fallback; never mounts a disk.

The receiver always writes a bounded RAM ring. Raw files are mirrored by physical
generation (first-line hash and original offsets), so replay from either medium
has the same identity. Only a verified flash filesystem may receive durable data.
"""

import dataclasses
import contextlib
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import stat
import sys
import time

from .collector import Collector, FileImporter, wait_until
from .report import event_dict
from .store import DEFAULT_DATABASE, Store, connect_readonly

DEFAULT_CONFIG = "/etc/vanpi-network-storage.json"
RAW_BUDGET = 512 * 1024
RAW_LIMITS = {"network.jsonl": 35 * 1024 * 1024, "dendelion.log": 160 * 1024 * 1024}


class StorageUnavailable(OSError):
    """A storage identity/availability check failed; no SD fallback is allowed."""


@dataclasses.dataclass
class StorageConfig:
    mountpoint: Path
    uuid: str
    flash_root: Path
    runtime_root: Path
    fstype: str = "exfat"
    ram_max_bytes: int = 16 * 1024 * 1024
    flash_max_bytes: int = 256 * 1024 * 1024
    flash_max_events: int = 150000
    retention_days: float = 30

    def __post_init__(self):
        for name in ("mountpoint", "flash_root", "runtime_root"):
            value = Path(getattr(self, name))
            if not value.is_absolute() or ".." in value.parts:
                raise ValueError("storage paths must be absolute and normalized")
            setattr(self, name, value)
        if (
            self.flash_root.parent != self.mountpoint
            or self.runtime_root == self.mountpoint
        ):
            raise ValueError(
                "flash data must be an immediate child of its verified mount"
            )
        if (
            not self.uuid
            or "/" in self.uuid
            or self.ram_max_bytes < 4 * 1024 * 1024
            or self.ram_max_bytes > 32 * 1024 * 1024
        ):
            raise ValueError("invalid storage identity or RAM bound")

    @property
    def spool_dir(self):
        return self.runtime_root / "spool"

    @property
    def flash_database(self):
        return self.flash_root / "events.sqlite3"

    @property
    def ram_database(self):
        return self.runtime_root / "events.sqlite3"

    @property
    def flash_logs(self):
        return self.flash_root / "openwrt"

    @property
    def status_path(self):
        return self.runtime_root / "storage-status.json"


def load_config(path=None):
    path = path or os.environ.get("VANPI_NETWORK_STORAGE_CONFIG", DEFAULT_CONFIG)
    with open(path, encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("unsupported storage configuration")
    fields = {field.name for field in dataclasses.fields(StorageConfig)}
    return StorageConfig(**{key: value[key] for key in fields if key in value})


def _unescape_mount(value):
    for old, new in ((r"\040", " "), (r"\011", "\t"), (r"\012", "\n"), (r"\134", "\\")):
        value = value.replace(old, new)
    return value


class MountGuard:
    def __init__(self, config):
        self.config = config

    def _mount_rows(self):
        rows = []
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            before, after = line.split(" - ", 1)
            fields, fs = before.split(), after.split()
            major, minor = map(int, fields[2].split(":"))
            rows.append(
                dict(
                    id=fields[0],
                    mountpoint=_unescape_mount(fields[4]),
                    major=major,
                    minor=minor,
                    fstype=fs[0],
                    source=_unescape_mount(fs[1]),
                    options=set(fields[5].split(",")) | set(fs[2].split(",")),
                )
            )
        return rows

    def _uuid_device(self):
        return os.stat(Path("/dev/disk/by-uuid") / self.config.uuid)

    def _path_stat(self, path):
        return os.stat(path)

    def flash(self):
        config = self.config
        try:
            if (
                config.mountpoint.resolve() != config.mountpoint
                or config.flash_root.is_symlink()
            ):
                raise StorageUnavailable("Flash path is a symlink")
            rows = self._mount_rows()
            mounts = [
                row for row in rows if row["mountpoint"] == str(config.mountpoint)
            ]
            if len(mounts) != 1:
                raise StorageUnavailable("Expected flash mount is absent or ambiguous")
            row = mounts[0]
            device = self._uuid_device()
            actual = self._path_stat(config.mountpoint)
            if (
                row["fstype"] != config.fstype
                or "ro" in row["options"]
                or "rw" not in row["options"]
                or not stat.S_ISBLK(device.st_mode)
                or (row["major"], row["minor"])
                != (os.major(device.st_rdev), os.minor(device.st_rdev))
                or actual.st_dev != device.st_rdev
            ):
                raise StorageUnavailable(
                    "Flash filesystem identity or writable state differs"
                )
            # A bind/submount under this managed directory is not our data path.
            if any(
                Path(r["mountpoint"]).is_relative_to(config.flash_root) for r in rows
            ):
                raise StorageUnavailable("Unexpected nested flash mount")
            for path in (config.flash_root, config.flash_logs, config.flash_database):
                if path.exists() and (
                    path.is_symlink() or path.stat().st_dev != actual.st_dev
                ):
                    raise StorageUnavailable(
                        "Flash data path escaped the verified filesystem"
                    )
            return (row["id"], row["major"], row["minor"], actual.st_dev)
        except (OSError, ValueError) as exc:
            if isinstance(exc, StorageUnavailable):
                raise
            raise StorageUnavailable("Flash identity cannot be verified") from exc

    def ram(self):
        root = self.config.runtime_root
        try:
            if root.resolve() != root:
                raise StorageUnavailable("RAM path is a symlink")
            rows = [
                r
                for r in self._mount_rows()
                if root.is_relative_to(Path(r["mountpoint"]))
            ]
            row = max(rows, key=lambda r: len(Path(r["mountpoint"]).parts))
            if (
                row["fstype"] != "tmpfs"
                or "rw" not in row["options"]
                or "ro" in row["options"]
            ):
                raise StorageUnavailable("Runtime storage must be writable tmpfs")
            for path in (
                root,
                self.config.spool_dir,
                self.config.ram_database,
                self.config.status_path,
            ):
                if path.is_symlink():
                    raise StorageUnavailable("Unexpected link in RAM storage")
            return (row["id"], row["major"], row["minor"])
        except (OSError, ValueError) as exc:
            if isinstance(exc, StorageUnavailable):
                raise
            raise StorageUnavailable("RAM filesystem cannot be verified") from exc


def resolve_database(database=None, config_path=None):
    """Read-only selector. Explicit DBs remain useful for fixtures and recovery."""
    if database not in (None, "auto"):
        return str(database)
    selected = config_path or os.environ.get(
        "VANPI_NETWORK_STORAGE_CONFIG", DEFAULT_CONFIG
    )
    config = load_config(selected)
    guard = MountGuard(config)
    guard.ram()
    if config.status_path.exists():
        if config.status_path.stat().st_size > 16384:
            raise StorageUnavailable("Invalid recorder storage status")
        status = json.loads(config.status_path.read_text())
        mode = status.get("mode")
        expected = config.flash_database if mode == "flash" else config.ram_database
        if mode not in ("flash", "ram") or status.get("active_database") != str(
            expected
        ):
            raise StorageUnavailable("Invalid recorder storage selection")
        if mode == "flash":
            guard.flash()
        return str(expected)
    try:
        guard.flash()
    except StorageUnavailable:
        return str(config.ram_database)
    return str(config.flash_database)


class StorageManager:
    def __init__(self, config, guard=None):
        self.config = config
        self.guard = guard or MountGuard(config)
        self.store = None
        self.mode = None
        self.token = None
        self.checkpoints = {}
        self.drain_last_id = 0
        self.raw_pruned = 0
        self.flash_retry_after = 0
        self.last_raw_prune = 0
        self.last_error = None
        self.replaying = False

    def storage_error(self, exc, operation):
        """Expose bounded diagnostics, never exception text/paths or raw data."""
        if isinstance(exc, PermissionError):
            code, detail = (
                "permission-denied",
                "Permission denied; check receiver file and storage access.",
            )
        elif isinstance(exc, sqlite3.Error):
            code, detail = (
                "database-error",
                "SQLite operation failed; check database access, capacity and integrity.",
            )
        elif isinstance(exc, StorageUnavailable):
            code, detail = (
                "storage-check-failed",
                "Storage identity or raw-generation consistency check failed.",
            )
        else:
            code, detail = (
                "io-error",
                "Storage I/O failed; check filesystem availability and local service status.",
            )
        # Operations are caller-owned labels, never external command arguments.
        operation = (
            operation
            if operation in ("open", "mirror", "replay", "collect")
            else "storage"
        )
        previous = self.last_error
        if previous and (previous["code"], previous["operation"]) == (code, operation):
            return
        self.last_error = dict(
            code=code, operation=operation, detail=detail, since=time.time()
        )
        print(
            f"network recorder storage: {operation}: {code}; using bounded RAM",
            file=sys.stderr,
            flush=True,
        )

    def storage_recovered(self):
        if self.last_error:
            print(
                "network recorder storage: flash access and replay recovered",
                file=sys.stderr,
                flush=True,
            )
        self.last_error = None

    def _flash_guard(self, token):
        if self.guard.flash() != token:
            raise StorageUnavailable("Flash mount changed; writer must reopen")

    def _ensure_flash(self, token):
        """Create through pinned directory FDs, never through a vacant /mnt path."""
        self._flash_guard(token)
        mountfd = os.open(
            self.config.mountpoint, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            self._flash_guard(token)
            try:
                os.mkdir(self.config.flash_root.name, 0o750, dir_fd=mountfd)
            except FileExistsError:
                pass
            rootfd = os.open(
                self.config.flash_root.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=mountfd,
            )
            try:
                self._flash_guard(token)
                try:
                    os.mkdir("openwrt", 0o750, dir_fd=rootfd)
                except FileExistsError:
                    pass
                dbfd = os.open(
                    "events.sqlite3",
                    os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                    0o640,
                    dir_fd=rootfd,
                )
                try:
                    if not stat.S_ISREG(os.fstat(dbfd).st_mode):
                        raise StorageUnavailable("Unexpected flash database type")
                    os.fsync(dbfd)
                finally:
                    os.close(dbfd)
                os.fsync(rootfd)
            finally:
                os.close(rootfd)
        finally:
            os.close(mountfd)
        self._flash_guard(token)

    def _flash_store(self, token):
        self._ensure_flash(token)
        return Store(
            self.config.flash_database,
            max_bytes=self.config.flash_max_bytes,
            max_events=self.config.flash_max_events,
            retention_days=self.config.retention_days,
            create_parent=False,
            existing_only=True,
            portable_permissions=True,
            write_guard=lambda: self._flash_guard(token),
        )

    def _cache_checkpoints(self):
        if self.store is not None:
            # Metadata only, bounded by source-generation retention (not raw events).
            self.checkpoints = {
                r["key"]: json.loads(r["value"])
                for r in self.store.db.execute(
                    "SELECT key,value FROM checkpoints WHERE key LIKE 'file-%' OR key LIKE 'receiver-%' OR key IN ('structured-cutover','system-monitor-id','ubnt-snapshot','recorder-heartbeat') LIMIT 4096"
                )
            }

    def _ram(self):
        self.guard.ram()
        if self.store:
            try:
                self.store.close()
            except sqlite3.Error:
                pass
        self.config.runtime_root.mkdir(mode=0o750, parents=True, exist_ok=True)
        self.config.spool_dir.mkdir(mode=0o750, exist_ok=True)
        self.store = Store(
            self.config.ram_database,
            max_bytes=self.config.ram_max_bytes,
            max_events=12000,
            retention_days=1,
            write_guard=self.guard.ram,
        )
        acknowledged = self.store.checkpoint("flash-drained-through", 0)
        pending = self.store.db.execute(
            "SELECT 1 FROM events WHERE id>? LIMIT 1", (acknowledged,)
        ).fetchone()
        if not pending:
            for key, value in self.checkpoints.items():
                self.store.set_checkpoint(key, value)
            self.store.db.commit()
        self.mode, self.token, self.drain_last_id = "ram", None, acknowledged
        self.record_coverage()
        return self.store

    def open(self):
        self.guard.ram()
        self.config.runtime_root.mkdir(mode=0o750, parents=True, exist_ok=True)
        self.config.spool_dir.mkdir(mode=0o750, exist_ok=True)
        # A restart must first replay a surviving volatile store, even if flash
        # has already reappeared. It is never replaced with an empty database.
        if self.config.ram_database.exists():
            ram = connect_readonly(self.config.ram_database)
            try:
                row = ram.execute(
                    "SELECT value FROM checkpoints WHERE key='flash-drained-through'"
                ).fetchone()
                acknowledged = json.loads(row[0]) if row else 0
                if ram.execute(
                    "SELECT 1 FROM events WHERE id>? LIMIT 1", (acknowledged,)
                ).fetchone():
                    return self._ram()
            finally:
                ram.close()
        try:
            token = self.guard.flash()
            self.store = self._flash_store(token)
            self.mode, self.token = "flash", token
            self._cache_checkpoints()
            self.record_coverage()
            return self.store
        except (OSError, sqlite3.Error) as exc:
            self.storage_error(exc, "open")
            self.flash_retry_after = time.monotonic() + 5
            return self._ram()

    @staticmethod
    def _first_line(fd):
        return os.pread(fd, 16385, 0).split(b"\n", 1)[0] + b"\n"

    def mirror_logs(self, token=None):
        token = self.token if token is None else token
        self._flash_guard(token)
        self._ensure_flash(token)
        rawfd = os.open(
            self.config.flash_logs, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        budget = RAW_BUDGET
        priority = []
        try:
            for base in ("network.jsonl", "dendelion.log"):
                sources = sorted(
                    self.config.spool_dir.glob(base + "*"),
                    key=lambda p: (p.name != base, p.name),
                )
                for source in sources:
                    if budget <= 0:
                        break
                    self._flash_guard(token)
                    srcfd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
                    try:
                        srcstat = os.fstat(srcfd)
                        if not stat.S_ISREG(srcstat.st_mode):
                            raise StorageUnavailable("Unexpected RAM log type")
                        first = os.pread(srcfd, 16385, 0)
                        if b"\n" not in first:
                            continue
                        first = first.split(b"\n", 1)[0] + b"\n"
                        digest = hashlib.sha256(first).hexdigest()
                        canonical = None
                        try:
                            checkfd = os.open(
                                base, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=rawfd
                            )
                        except FileNotFoundError:
                            pass
                        else:
                            try:
                                header = os.pread(checkfd, 16385, 0)
                                if b"\n" in header:
                                    canonical = hashlib.sha256(
                                        header.split(b"\n", 1)[0] + b"\n"
                                    ).hexdigest()
                                elif first.startswith(header):
                                    # A crash/short write during the first line
                                    # leaves a prefix we can finish in place.
                                    canonical = digest
                                else:
                                    raise StorageUnavailable(
                                        "Incomplete raw generation prefix differs"
                                    )
                            finally:
                                os.close(checkfd)
                        if source.name == base and canonical and canonical != digest:
                            archived = base + "." + canonical
                            try:
                                os.stat(archived, dir_fd=rawfd, follow_symlinks=False)
                            except FileNotFoundError:
                                os.rename(
                                    base, archived, src_dir_fd=rawfd, dst_dir_fd=rawfd
                                )
                            else:
                                raise StorageUnavailable(
                                    "Duplicate raw generation needs review"
                                )
                            canonical = None
                        name = (
                            base
                            if source.name == base or canonical == digest
                            else base + "." + digest
                        )
                        dstfd = os.open(
                            name,
                            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                            0o640,
                            dir_fd=rawfd,
                        )
                        try:
                            dststat = os.fstat(dstfd)
                            if (
                                not stat.S_ISREG(dststat.st_mode)
                                or dststat.st_size > srcstat.st_size
                            ):
                                raise StorageUnavailable("Raw generation size differs")
                            # Verify the existing tail before extending a durable
                            # generation; never append onto a different prefix.
                            tail = min(dststat.st_size, 4096)
                            if tail and os.pread(
                                dstfd, tail, dststat.st_size - tail
                            ) != os.pread(srcfd, tail, dststat.st_size - tail):
                                raise StorageUnavailable(
                                    "Raw generation prefix differs"
                                )
                            data = os.pread(
                                srcfd,
                                min(budget, srcstat.st_size - dststat.st_size),
                                dststat.st_size,
                            )
                            if data:
                                self._flash_guard(token)
                                os.lseek(dstfd, 0, os.SEEK_END)
                                view = memoryview(data)
                                while view:
                                    written = os.write(dstfd, view)
                                    if not written:
                                        raise StorageUnavailable(
                                            "Raw append made no progress"
                                        )
                                    view = view[written:]
                                os.fsync(dstfd)
                                budget -= len(data)
                            if source.name == base:
                                priority.append(str(self.config.flash_logs / name))
                        finally:
                            os.close(dstfd)
                    finally:
                        os.close(srcfd)
            os.fsync(rawfd)
        finally:
            os.close(rawfd)
        if time.monotonic() - self.last_raw_prune > 60:
            self._prune_raw(token)
            self.last_raw_prune = time.monotonic()
        return priority

    def _prune_raw(self, token):
        self._flash_guard(token)
        rawfd = os.open(
            self.config.flash_logs, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            for base, maximum in RAW_LIMITS.items():
                entries = []
                for name in os.listdir(rawfd):
                    if not name.startswith(base):
                        continue
                    info = os.stat(name, dir_fd=rawfd, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode):
                        raise StorageUnavailable("Unexpected raw retention target")
                    entries.append((info.st_mtime, name, info.st_size))
                total = sum(item[2] for item in entries)
                for stamp, name, size in sorted(entries):
                    if name == base:
                        continue
                    if total <= maximum and stamp >= time.time() - 30 * 86400:
                        continue
                    self._flash_guard(token)
                    os.unlink(name, dir_fd=rawfd)
                    total -= size
                    self.raw_pruned += 1
        finally:
            os.close(rawfd)

    def _drain(self, token):
        target = self._flash_store(token)
        try:
            self.mirror_logs(token)
            rows = self.store.db.execute(
                "SELECT * FROM events WHERE id>? ORDER BY id LIMIT 500",
                (self.drain_last_id,),
            ).fetchall()
            target.ingest([event_dict(row) for row in rows])
            if rows:
                self.drain_last_id = rows[-1]["id"]
            if self.store.db.execute(
                "SELECT 1 FROM events WHERE id>? LIMIT 1", (self.drain_last_id,)
            ).fetchone():
                target.close()
                return self.store
            for row in self.store.db.execute(
                "SELECT i.id,e.fingerprint FROM incidents i JOIN events e ON e.id=i.first_id"
            ):
                target.alias_incident(row["id"], row["fingerprint"])
            # Do not copy RAM source cursors: evicted RAM observations may still
            # be recoverable from raw mirrors, Pi history or the antenna's ring.
            heartbeat = self.store.checkpoint("recorder-heartbeat")
            if heartbeat:
                target.set_checkpoint("recorder-heartbeat", heartbeat)
            target.db.commit()
            target.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._flash_guard(token)
            self.store.set_checkpoint("flash-drained-through", self.drain_last_id)
            self.store.db.commit()
            self.store.close()
            self.store = target
            self.mode, self.token = "flash", token
            self.storage_recovered()
            self._cache_checkpoints()
            self.record_coverage()
            # Keep the bounded RAM database inode. Unlinking a SQLite database
            # beneath an in-flight read-only report would break its WAL pairing.
            # The durable flash commit precedes this RAM acknowledgement;
            # restart/reconnection can safely retry any unacknowledged rows.
            return self.store
        except BaseException:
            if target is not self.store:
                target.close()
            raise

    def tick(self):
        self.replaying = False
        if self.store is None:
            return self.open()
        operation = "mirror" if self.mode == "flash" else "replay"
        try:
            token = self.guard.flash()
            if self.mode == "flash":
                if token != self.token:
                    self.store.close()
                    self.store = self._flash_store(token)
                    self.token = token
                self.mirror_logs(token)
                self._cache_checkpoints()
                self.storage_recovered()
            elif time.monotonic() >= self.flash_retry_after:
                self._drain(token)
                self.replaying = self.mode == "ram"
            self.record_coverage()
        except (OSError, sqlite3.Error) as exc:
            self.replaying = False
            self.storage_error(exc, operation)
            self.flash_retry_after = time.monotonic() + 5
            if self.mode != "ram":
                self._ram()
            else:
                self.record_coverage()
        return self.store

    def record_coverage(self):
        self.guard.ram()
        loss = (self.store.checkpoint("retention", {}) or {}).get("evicted_events", 0)
        detail = (
            "Verified flash storage; database and router logs are off the boot SD. "
            "Current-process source checks are separate from network health."
            if self.mode == "flash"
            else "Flash unavailable or replay pending: recording in bounded RAM. Volatile evidence is lost on reboot; "
            "older flash history is unavailable until reconnection. No boot-SD data fallback."
        )
        if self.mode == "ram" and loss:
            detail += f" RAM retention evicted {loss} observations; raw replay may recover some."
        if self.mode == "ram" and self.last_error:
            detail += f" {self.last_error['detail']} Operation: {self.last_error['operation']}; error: {self.last_error['code']}."
        if self.replaying:
            detail += " Replaying saved observations before resuming source import to protect pending RAM evidence."
        self.store.coverage(
            "storage",
            "vanpi",
            "current" if self.mode == "flash" else "unknown",
            detail,
            time.time(),
        )
        status = dict(
            schema_version=1,
            mode=self.mode,
            active_database=str(self.store.path),
            checked_at=time.time(),
            volatile=self.mode == "ram",
            raw_generations_pruned=self.raw_pruned,
            last_error=self.last_error,
            replaying=self.replaying,
        )
        temporary = self.config.status_path.with_name("storage-status.json.new")
        if temporary.is_symlink():
            raise StorageUnavailable("Unexpected storage status link")
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640
        )
        with os.fdopen(fd, "w") as stream:
            json.dump(status, stream)
        os.replace(temporary, self.config.status_path)

    def close(self):
        if self.store:
            self.store.close()
            self.store = None


@contextlib.contextmanager
def storage_lock(config):
    guard = MountGuard(config)
    guard.ram()
    config.runtime_root.mkdir(mode=0o750, parents=True, exist_ok=True)
    descriptor = os.open(
        config.runtime_root / "collector.lock",
        os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW,
        0o640,
    )
    with os.fdopen(descriptor, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def write_storage_command(config, command, files=()):
    """Auto-path imports share the daemon's identity/bounds and writer lock."""
    with storage_lock(config):
        manager = StorageManager(config)
        try:
            store = manager.open()
            if command == "import":
                importer = FileImporter(store)
                for path in files:
                    while True:
                        result = importer.import_file(path)
                        if result["complete"] or not result.get("progress"):
                            break
            manager.record_coverage()
        finally:
            manager.close()


def run_storage(config, once=False, sync_only=False):
    with storage_lock(config):
        guard = MountGuard(config)
        manager = StorageManager(config, guard)
        stop = False

        def stopping(_signum, _frame):
            nonlocal stop
            stop = True

        signal.signal(signal.SIGTERM, stopping)
        signal.signal(signal.SIGINT, stopping)
        try:
            manager.open()
            if sync_only:
                guard.flash()
                for _ in range(1000):
                    manager.flash_retry_after = 0
                    manager.tick()
                    if manager.mode == "flash":
                        # Drain the bounded RAM raw ring completely without probes.
                        for _ in range(32):
                            manager.mirror_logs()
                        collector = Collector(
                            manager.store, log_dir=str(config.flash_logs)
                        )
                        for _ in range(256):
                            collector.logs()  # local raw evidence only; no probes/SSH
                            coverage = manager.store.db.execute(
                                "SELECT status FROM coverage WHERE source='historical-import'"
                            ).fetchone()
                            if coverage and coverage[0] == "current":
                                return
                        raise StorageUnavailable("Durable raw replay has not caught up")
                raise StorageUnavailable("Volatile recovery did not complete")
            collector = Collector(manager.store)
            last_slow = last_prune = 0
            while not stop:
                started = time.monotonic()
                store = manager.tick()
                collector.store, collector.files = store, FileImporter(store)
                collector.log_dir = str(
                    config.flash_logs if manager.mode == "flash" else config.spool_dir
                )
                if manager.replaying:
                    # Drain saved RAM observations before admitting a raw-file
                    # backlog that could evict them. The receiver keeps its RAM
                    # ring and mirror_logs continues copying it to flash. Once
                    # replay completes, normal import catches up from those
                    # durable files; a failed replay resumes RAM collection.
                    if once:
                        return
                    wait_until(started + 5, lambda: stop)
                    continue
                slow = started - last_slow >= 60
                try:
                    collector.once(slow)
                    if slow:
                        last_slow = started
                    if started - last_prune >= 3600:
                        store.prune()
                        last_prune = started
                    manager._cache_checkpoints()
                    manager.record_coverage()
                except (OSError, sqlite3.Error) as exc:
                    manager.storage_error(exc, "collect")
                    manager._ram()
                if once:
                    return
                wait_until(started + 5, lambda: stop)
        finally:
            manager.close()
