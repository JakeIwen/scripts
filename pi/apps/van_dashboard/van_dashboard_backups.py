"""Backup evidence and guarded manual backup controllers.

This module supports both repository package imports and the flat sibling
layout used by the deployed van-dashboard service.
"""

__all__ = ["BackupManager", "BackupStatusError"]

if __package__:
    from .van_dashboard_common import (
        BACKUP_ABORT,
        BACKUP_BORG_RUNNER,
        BACKUP_CLONE_CARD_SIZE_PATH,
        BACKUP_CLONE_NOW,
        BACKUP_CLONE_TIMEOUT,
        BACKUP_CONF,
        BACKUP_EXFAT_RUNNER,
        BACKUP_OPENWRT_RUNNER,
        BACKUP_RUN_TIMEOUT,
        BACKUP_STAMP_DIR,
        BACKUP_STATUS_TIMEOUT,
        BACKUP_STOP_TIMEOUT,
        LSBLK,
        SUDO,
        TIME_MACHINE_BUNDLE,
        copy,
        datetime,
        json,
        os,
        plistlib,
        re,
        read_text_file,
        run_command,
        shlex,
        stat,
        subprocess,
        threading,
        time,
    )
else:
    from van_dashboard_common import (
        BACKUP_ABORT,
        BACKUP_BORG_RUNNER,
        BACKUP_CLONE_CARD_SIZE_PATH,
        BACKUP_CLONE_NOW,
        BACKUP_CLONE_TIMEOUT,
        BACKUP_CONF,
        BACKUP_EXFAT_RUNNER,
        BACKUP_OPENWRT_RUNNER,
        BACKUP_RUN_TIMEOUT,
        BACKUP_STAMP_DIR,
        BACKUP_STATUS_TIMEOUT,
        BACKUP_STOP_TIMEOUT,
        LSBLK,
        SUDO,
        TIME_MACHINE_BUNDLE,
        copy,
        datetime,
        json,
        os,
        plistlib,
        re,
        read_text_file,
        run_command,
        shlex,
        stat,
        subprocess,
        threading,
        time,
    )

if __package__:
    from .van_dashboard_block_devices import (
        block_device_descendants,
        flatten_block_devices,
        root_block_device,
    )
else:
    from van_dashboard_block_devices import (
        block_device_descendants,
        flatten_block_devices,
        root_block_device,
    )


class BackupStatusError(RuntimeError):
    pass


class BackupManager:
    """Read backup evidence and serialize safe manual backup requests.

    Configuration is parsed as data rather than sourced, so inspecting status
    cannot execute backup_conf.sh or load its secrets file. Clone targets are
    restricted to the labels already present in CLONE_TARGETS; the dashboard
    never accepts a block-device path and never exposes
    /home/pi/scripts/backup/clone_to_sd.sh --init. Manual Borg requests invoke
    only the fixed pi_backup.sh or exfat_snapshot.sh --force paths, which retain
    each job's disk-policy, ignition, mount, lock, snapshot, and retention
    safeguards. Stop requests invoke one fixed abort wrapper, which independently
    verifies that the shared lock holder is the requested Borg or EXFAT parent.
    """

    CLONE_TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    CLONE_CARD_NOMINAL_GB_OPTIONS = (32, 64, 128, 256)
    PROGRESS_PHASE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
    RSYNC_PROGRESS_RE = re.compile(
        r"^\s*([0-9][0-9,]*)\s+([0-9]{1,3})%"
    )
    RSYNC_FILE_PROGRESS_RE = re.compile(
        r"\(xfr#([0-9]+),\s*to-chk=([0-9]+)/([0-9]+)\)"
    )

    def __init__(
        self,
        config=BACKUP_CONF,
        stamp_dir=BACKUP_STAMP_DIR,
        clone_card_size_path=BACKUP_CLONE_CARD_SIZE_PATH,
        clone_tool=BACKUP_CLONE_NOW,
        borg_tool=BACKUP_BORG_RUNNER,
        exfat_tool=BACKUP_EXFAT_RUNNER,
        abort_tool=BACKUP_ABORT,
        openwrt_tool=BACKUP_OPENWRT_RUNNER,
        time_machine_bundle=TIME_MACHINE_BUNDLE,
        progress_dir=None,
        command=run_command,
        timeout=BACKUP_STATUS_TIMEOUT,
        clone_timeout=BACKUP_CLONE_TIMEOUT,
        backup_timeout=BACKUP_RUN_TIMEOUT,
        stop_timeout=BACKUP_STOP_TIMEOUT,
        wall_clock=time.time,
        process_root="/proc",
    ):
        self.config = config
        self.stamp_dir = stamp_dir
        self.clone_card_size_path = clone_card_size_path
        self.clone_tool = clone_tool
        self.borg_tool = borg_tool
        self.exfat_tool = exfat_tool
        self.abort_tool = abort_tool
        self.openwrt_tool = openwrt_tool
        self.time_machine_bundle = time_machine_bundle
        self.progress_dir = progress_dir or os.path.join(stamp_dir, "progress")
        self.command = command
        self.timeout = timeout
        self.clone_timeout = clone_timeout
        self.backup_timeout = backup_timeout
        self.stop_timeout = stop_timeout
        self.wall_clock = wall_clock
        self.process_root = process_root
        self.lock = threading.Lock()
        self.thread = None
        self.stop_thread = None
        self.operation = {
            "status": "idle",
            "kind": None,
            "target": None,
            "started_at": None,
            "completed_at": None,
            "error": None,
        }
        self.stop_operation = {
            "status": "idle",
            "kind": None,
            "started_at": None,
            "completed_at": None,
            "error": None,
        }

    @classmethod
    def parse_config(cls, text):
        match = re.search(r"^\s*CLONE_TARGETS=\(([^)]*)\)\s*$", text, re.MULTILINE)
        if not match:
            raise BackupStatusError("backup configuration has no CLONE_TARGETS")
        try:
            entries = shlex.split(match.group(1), comments=True, posix=True)
        except ValueError as exc:
            raise BackupStatusError(f"could not parse CLONE_TARGETS: {exc}") from exc
        targets = []
        seen = set()
        for entry in entries:
            label, separator, raw_interval = entry.rpartition(":")
            if (
                not separator
                or not cls.CLONE_TARGET_RE.fullmatch(label)
                or not raw_interval.isdigit()
                or not 1 <= int(raw_interval) <= 3650
                or label in seen
            ):
                raise BackupStatusError("backup configuration has an invalid clone target")
            seen.add(label)
            targets.append({"label": label, "interval_days": int(raw_interval)})
        if not targets:
            raise BackupStatusError("backup configuration has no clone targets")

        def integer(name, default):
            value = re.search(rf"^\s*{re.escape(name)}=(\d+)\s*(?:#.*)?$", text, re.MULTILINE)
            return int(value.group(1)) if value else default

        clone_card_nominal_gb = integer("CLONE_CARD_NOMINAL_GB", 64)
        if clone_card_nominal_gb not in cls.CLONE_CARD_NOMINAL_GB_OPTIONS:
            raise BackupStatusError(
                "backup configuration has an unsupported clone card capacity"
            )

        return {
            "targets": targets,
            "borg_stale_hours": integer("BORG_STALE_HOURS", 48),
            "exfat_snapshot_stale_hours": integer(
                "EXFAT_SNAPSHOT_STALE_HOURS", 48
            ),
            "openwrt_backup_stale_hours": integer(
                "OPENWRT_BACKUP_STALE_HOURS", 72
            ),
            "clone_stale_factor": integer("CLONE_STALE_FACTOR", 2),
            "clone_card_nominal_gb": clone_card_nominal_gb,
        }

    def _configuration(self):
        try:
            configuration = self.parse_config(read_text_file(self.config))
        except OSError as exc:
            raise BackupStatusError(f"could not read backup configuration: {exc}") from exc
        nominal_gb = self._read_clone_card_nominal_gb(
            configuration["clone_card_nominal_gb"]
        )
        configuration["clone_card_nominal_gb"] = nominal_gb
        configuration["root_used_max_gib"] = nominal_gb * 13 // 16
        return configuration

    def _read_clone_card_nominal_gb(self, default):
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(self.clone_card_size_path, flags)
        except FileNotFoundError:
            return default
        except OSError as exc:
            raise BackupStatusError(
                f"could not read clone card capacity: {exc}"
            ) from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 8:
                raise BackupStatusError("clone card capacity setting is invalid")
            raw = os.read(descriptor, 9)
        finally:
            os.close(descriptor)
        try:
            text = raw.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise BackupStatusError("clone card capacity setting is invalid") from exc
        allowed = {str(value): value for value in self.CLONE_CARD_NOMINAL_GB_OPTIONS}
        if text not in allowed:
            raise BackupStatusError("clone card capacity setting is invalid")
        return allowed[text]

    def set_clone_card_nominal_gb(self, value):
        text = str(value).strip()
        allowed = {
            str(option): option for option in self.CLONE_CARD_NOMINAL_GB_OPTIONS
        }
        if text not in allowed:
            raise ValueError("clone card capacity must be 32, 64, 128, or 256GB")
        nominal_gb = allowed[text]
        parent = os.path.dirname(self.clone_card_size_path) or "."
        temporary = (
            f"{self.clone_card_size_path}.tmp.{os.getpid()}."
            f"{threading.get_ident()}.{time.time_ns()}"
        )
        descriptor = None
        try:
            os.makedirs(parent, mode=0o755, exist_ok=True)
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(temporary, flags, 0o644)
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                descriptor = None
                handle.write(f"{nominal_gb}\n")
                handle.flush()
                os.fsync(handle.fileno())
            with self.lock:
                os.replace(temporary, self.clone_card_size_path)
        except OSError as exc:
            raise BackupStatusError(
                f"could not save clone card capacity: {exc}"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        return self.status()

    @staticmethod
    def _flatten_block_devices(devices, parent=None):
        return flatten_block_devices(devices, parent)

    def _block_devices(self):
        args = [
            LSBLK,
            "--json",
            "--bytes",
            "--output",
            "NAME,PATH,PKNAME,LABEL,SIZE,MOUNTPOINTS",
        ]
        try:
            result = self.command(args, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise BackupStatusError(
                f"storage discovery timed out after {self.timeout:g} seconds"
            ) from exc
        except OSError as exc:
            raise BackupStatusError(f"could not inspect storage: {exc}") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout or "lsblk failed").strip()[-500:]
            raise BackupStatusError(detail)
        try:
            payload = json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            raise BackupStatusError("storage discovery returned invalid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("blockdevices"), list):
            raise BackupStatusError("storage discovery returned an invalid schema")
        return self._flatten_block_devices(payload["blockdevices"])

    @staticmethod
    def _mountpoints(row):
        points = row.get("mountpoints")
        if not isinstance(points, list):
            points = [row.get("mountpoint")]
        return [point for point in points if isinstance(point, str) and point]

    @staticmethod
    def _root_row(row):
        return root_block_device(row)

    @staticmethod
    def _descendants(row):
        return block_device_descendants(row)

    @staticmethod
    def _stamp(path):
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise BackupStatusError(f"could not read backup stamp: {exc}") from exc
        return int(stat.st_mtime)

    def _running_backup_processes(self):
        """Identify actual scheduled/manual backup parents from Linux procfs.

        Match complete NUL-delimited argv entries, never command substrings, so
        status probes and unrelated shell commands cannot create false running
        states. Runtime discovery is advisory; an unreadable or absent procfs
        must not make all backup history unavailable.
        """
        expected = {
            os.fsencode(self.borg_tool): "borg",
            os.fsencode(self.exfat_tool): "exfat",
            os.fsencode(self.openwrt_tool): "openwrt",
        }
        running = {kind: set() for kind in expected.values()}
        try:
            entries = os.scandir(self.process_root)
        except OSError:
            return running
        with entries:
            for entry in entries:
                if not entry.name.isdigit():
                    continue
                try:
                    with open(
                        os.path.join(self.process_root, entry.name, "cmdline"), "rb"
                    ) as handle:
                        arguments = set(handle.read(16384).split(b"\0"))
                except OSError:
                    continue
                for script, kind in expected.items():
                    if script in arguments:
                        running[kind].add(int(entry.name))
        return running

    def _progress_state(self, kind, running_pids, now):
        """Read telemetry only when its exact producer process is still live."""
        if not running_pids:
            return None
        path = os.path.join(self.progress_dir, f"{kind}.state")
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read(4097)
        except (FileNotFoundError, PermissionError, OSError):
            return None
        if len(text) > 4096:
            return None
        values = {}
        for line in text.splitlines():
            key, separator, value = line.partition("=")
            if not separator or key in values:
                return None
            values[key] = value
        if values.get("version") != "1":
            return None
        try:
            pid = int(values["pid"])
            started_at = int(values["started_at"])
            updated_at = int(values["updated_at"])
        except (KeyError, TypeError, ValueError):
            return None
        phase = values.get("phase", "")
        detail = values.get("detail", "")
        if (
            pid not in running_pids
            or not self.PROGRESS_PHASE_RE.fullmatch(phase)
            or not detail
            or len(detail) > 160
            or any(ord(character) < 32 for character in detail)
            or started_at <= 0
            or updated_at < started_at
            or started_at > now + 60
        ):
            return None
        return {
            "phase": phase,
            "detail": detail,
            "started_at": started_at,
            "updated_at": updated_at,
            "elapsed_seconds": max(0, now - started_at),
        }

    def _exfat_rsync_progress(self, progress):
        if not progress or progress.get("phase") != "copying":
            return progress
        path = os.path.join(self.progress_dir, "exfat.rsync")
        try:
            with open(path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 131072), os.SEEK_SET)
                text = handle.read(131072).decode("utf-8", errors="replace")
        except (FileNotFoundError, PermissionError, OSError):
            return progress
        for line in reversed(re.split(r"[\r\n]+", text)):
            match = self.RSYNC_PROGRESS_RE.search(line)
            if not match:
                continue
            result = dict(progress)
            result["bytes_processed"] = int(match.group(1).replace(",", ""))
            result["progress_percent"] = min(100, int(match.group(2)))
            files = self.RSYNC_FILE_PROGRESS_RE.search(line)
            if files:
                result["files_transferred"] = int(files.group(1))
                result["files_remaining"] = int(files.group(2))
                result["file_list_total"] = int(files.group(3))
            return result
        return progress

    @staticmethod
    def _plist_timestamp(value):
        if not isinstance(value, datetime.datetime):
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.timezone.utc)
        return int(value.timestamp())

    def _time_machine(self, now):
        bundle = self.time_machine_bundle
        result = {
            "device": os.path.basename(bundle).removesuffix(".sparsebundle") or "Mac",
            "available": os.path.isdir(bundle),
            "last_backup_at": None,
            "snapshots": [],
            "running": False,
            "progress_percent": None,
            "bytes_copied": None,
            "total_bytes": None,
            "updated_at": None,
            "error": None,
        }
        if not result["available"]:
            result["error"] = "Time Machine sparsebundle is not reachable"
            return result

        history_path = os.path.join(bundle, "com.apple.TimeMachine.SnapshotHistory.plist")
        try:
            with open(history_path, "rb") as handle:
                history = plistlib.load(handle)
            raw_snapshots = history.get("Snapshots", []) if isinstance(history, dict) else []
            timestamps = [
                self._plist_timestamp(item.get("com.apple.backupd.SnapshotCompletionDate"))
                for item in raw_snapshots
                if isinstance(item, dict)
            ]
            result["snapshots"] = sorted(
                (stamp for stamp in timestamps if stamp is not None), reverse=True
            )[:24]
            if result["snapshots"]:
                result["last_backup_at"] = result["snapshots"][0]
        except FileNotFoundError:
            result["error"] = "Time Machine snapshot history is missing"
        except (OSError, ValueError, TypeError) as exc:
            result["error"] = f"could not read Time Machine history: {exc}"

        results_path = os.path.join(bundle, "com.apple.TimeMachine.Results.plist")
        try:
            updated_at = int(os.stat(results_path).st_mtime)
            with open(results_path, "rb") as handle:
                current = plistlib.load(handle)
            if isinstance(current, dict):
                progress = current.get("Progress")
                progress = progress if isinstance(progress, dict) else {}
                percent = progress.get("Percent")
                if isinstance(percent, (int, float)) and not isinstance(percent, bool):
                    result["progress_percent"] = round(max(0, min(1, percent)) * 100, 1)
                for source, target in (("bytes", "bytes_copied"), ("totalBytes", "total_bytes")):
                    value = progress.get(source)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        result[target] = value
                # A stale Results.plist can retain Running=true after an interrupted
                # backup. It must have been updated recently to count as live.
                result["running"] = current.get("Running") is True and now - updated_at <= 900
                result["updated_at"] = updated_at
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as exc:
            if result["error"] is None:
                result["error"] = f"could not read Time Machine progress: {exc}"
        return result

    def status(self):
        now = int(self.wall_clock())
        running_processes = self._running_backup_processes()
        configuration = self._configuration()
        rows = self._block_devices()
        labels = {row.get("label"): row for row in rows if row.get("label")}
        clone_factor = configuration["clone_stale_factor"]
        hotswaps = []
        for target in configuration["targets"]:
            label = target["label"]
            row = labels.get(label)
            root = self._root_row(row) if row is not None else None
            mounts = [] if root is None else [
                point
                for device in self._descendants(root)
                for point in self._mountpoints(device)
            ]
            last_clone_at = self._stamp(os.path.join(self.stamp_dir, f"clone_{label}"))
            interval_seconds = target["interval_days"] * 86400
            age_seconds = None if last_clone_at is None else max(0, now - last_clone_at)
            hotswaps.append(
                {
                    **target,
                    "attached": row is not None,
                    "device": root.get("path") if root is not None else None,
                    "size_bytes": root.get("size") if root is not None else None,
                    "mounted": bool(mounts),
                    "mountpoints": mounts,
                    "last_clone_at": last_clone_at,
                    "due": last_clone_at is None or age_seconds >= interval_seconds,
                    "stale": last_clone_at is None
                    or age_seconds > interval_seconds * clone_factor,
                }
            )

        borg_at = self._stamp(os.path.join(self.stamp_dir, "borg_ok"))
        borg_stale_seconds = configuration["borg_stale_hours"] * 3600
        borg = {
            "last_success_at": borg_at,
            "stale_hours": configuration["borg_stale_hours"],
            "stale": borg_at is None or now - borg_at > borg_stale_seconds,
            "running": bool(running_processes["borg"]),
            "progress": self._progress_state(
                "borg", running_processes["borg"], now
            ),
        }
        exfat_snapshot_at = self._stamp(
            os.path.join(self.stamp_dir, "exfat512_ok")
        )
        exfat_snapshot_stale_seconds = (
            configuration["exfat_snapshot_stale_hours"] * 3600
        )
        exfat_snapshot = {
            "last_success_at": exfat_snapshot_at,
            "stale_hours": configuration["exfat_snapshot_stale_hours"],
            "stale": exfat_snapshot_at is None
            or now - exfat_snapshot_at > exfat_snapshot_stale_seconds,
            "running": bool(running_processes["exfat"]),
            "progress": self._exfat_rsync_progress(
                self._progress_state("exfat", running_processes["exfat"], now)
            ),
        }
        openwrt_at = self._stamp(os.path.join(self.stamp_dir, "openwrt_ok"))
        openwrt_stale_seconds = configuration["openwrt_backup_stale_hours"] * 3600
        openwrt = {
            "last_success_at": openwrt_at,
            "stale_hours": configuration["openwrt_backup_stale_hours"],
            "stale": openwrt_at is None
            or now - openwrt_at > openwrt_stale_seconds,
            "running": bool(running_processes["openwrt"]),
            "progress": self._progress_state(
                "openwrt", running_processes["openwrt"], now
            ),
        }
        time_machine = self._time_machine(now)
        with self.lock:
            operation = copy.deepcopy(self.operation)
            stop_operation = copy.deepcopy(self.stop_operation)
        if operation["status"] == "running":
            if operation["kind"] == "borg":
                borg["running"] = True
            elif operation["kind"] == "exfat":
                exfat_snapshot["running"] = True
        attention = (
            borg["stale"]
            or exfat_snapshot["stale"]
            or openwrt["stale"]
            or any(card["stale"] for card in hotswaps)
        )
        if not time_machine["available"] or time_machine["last_backup_at"] is None:
            attention = True
        health = "running" if (
            operation["status"] == "running"
            or stop_operation["status"] == "running"
            or borg["running"]
            or exfat_snapshot["running"]
            or openwrt["running"]
            or time_machine["running"]
        ) else (
            "attention" if attention else "good"
        )
        return {
            "checked_at": now,
            "health": health,
            "settings": {
                "clone_card_nominal_gb": configuration["clone_card_nominal_gb"],
                "root_used_max_gib": configuration["root_used_max_gib"],
                "clone_card_nominal_gb_options": list(
                    self.CLONE_CARD_NOMINAL_GB_OPTIONS
                ),
            },
            "borg": borg,
            "exfat_snapshot": exfat_snapshot,
            "openwrt": openwrt,
            "hotswaps": hotswaps,
            "time_machine": time_machine,
            "operation": operation,
            "stop": stop_operation,
        }

    def start_clone(self, target):
        configuration = self._configuration()
        allowed = {item["label"] for item in configuration["targets"]}
        if target not in allowed:
            raise ValueError("unknown hotspare target")
        current = self.status()
        if current["borg"]["running"] or current["exfat_snapshot"]["running"]:
            raise BackupStatusError("a scheduled backup is already running")
        card = next(item for item in current["hotswaps"] if item["label"] == target)
        if not card["attached"]:
            raise BackupStatusError(f"{target} is not attached")
        if card["mounted"]:
            raise BackupStatusError(f"{target} has mounted partitions")
        with self.lock:
            if self.operation["status"] == "running":
                raise BackupStatusError("another dashboard clone is already running")
            started_at = int(self.wall_clock())
            self.operation = {
                "status": "running",
                "kind": "clone",
                "target": target,
                "started_at": started_at,
                "completed_at": None,
                "error": None,
            }
            self.thread = threading.Thread(
                target=self._run_clone,
                args=(target, started_at),
                name="backup-clone",
                daemon=True,
            )
            self.thread.start()
        return self.status()

    def _start_manual_backup(self, kind, tool, stamp_name):
        current = self.status()
        if current["borg"]["running"] or current["exfat_snapshot"]["running"]:
            raise BackupStatusError("a scheduled backup is already running")
        previous_at = self._stamp(os.path.join(self.stamp_dir, stamp_name))
        with self.lock:
            if self.operation["status"] == "running":
                raise BackupStatusError("another dashboard backup operation is running")
            started_at = int(self.wall_clock())
            self.operation = {
                "status": "running",
                "kind": kind,
                "target": None,
                "started_at": started_at,
                "completed_at": None,
                "error": None,
            }
            self.thread = threading.Thread(
                target=self._run_manual_backup,
                args=(started_at, kind, tool, stamp_name, previous_at),
                name=f"{kind}-backup",
                daemon=True,
            )
            self.thread.start()
        return self.status()

    def start_borg_backup(self):
        return self._start_manual_backup(
            "borg", self.borg_tool, "borg_ok"
        )

    def start_exfat_backup(self):
        return self._start_manual_backup(
            "exfat", self.exfat_tool, "exfat512_ok"
        )

    def request_stop(self, kind):
        if kind not in ("borg", "exfat"):
            raise ValueError("unknown backup kind")
        running = self._running_backup_processes()
        if not running[kind]:
            raise BackupStatusError(f"no active {kind} backup is available to stop")
        with self.lock:
            if self.stop_operation["status"] == "running":
                raise BackupStatusError("another backup stop request is already running")
            started_at = int(self.wall_clock())
            self.stop_operation = {
                "status": "running",
                "kind": kind,
                "started_at": started_at,
                "completed_at": None,
                "error": None,
            }
            self.stop_thread = threading.Thread(
                target=self._run_stop,
                args=(kind, started_at),
                name=f"stop-{kind}-backup",
                daemon=True,
            )
            self.stop_thread.start()
        return self.status()

    def _run_stop(self, kind, started_at):
        error = None
        try:
            result = self.command(
                [SUDO, "-n", self.abort_tool, "--user", kind],
                timeout=self.stop_timeout,
            )
            if result.returncode:
                error = (
                    result.stderr
                    or result.stdout
                    or f"could not stop {kind} backup"
                ).strip()[-500:]
        except subprocess.TimeoutExpired:
            error = f"backup stop timed out after {self.stop_timeout:g} seconds"
        except OSError as exc:
            error = f"could not request backup stop: {exc}"
        with self.lock:
            if self.stop_operation.get("started_at") == started_at:
                self.stop_operation.update(
                    {
                        "status": "error" if error else "complete",
                        "completed_at": int(self.wall_clock()),
                        "error": error,
                    }
                )

    def _run_clone(self, target, started_at):
        error = None
        try:
            result = self.command(
                [SUDO, "-n", self.clone_tool, target], timeout=self.clone_timeout
            )
            if result.returncode:
                detail = (result.stderr or result.stdout or "clone failed").strip()[-500:]
                error = detail
        except subprocess.TimeoutExpired:
            error = f"clone timed out after {self.clone_timeout:g} seconds"
        except OSError as exc:
            error = f"could not start clone: {exc}"
        with self.lock:
            if self.operation.get("started_at") == started_at:
                self.operation.update(
                    {
                        "status": "error" if error else "complete",
                        "completed_at": int(self.wall_clock()),
                        "error": error,
                    }
                )

    @staticmethod
    def _stamp_advanced(current, previous):
        return current is not None and (previous is None or current > previous)

    def _run_manual_backup(
        self, started_at, kind, tool, stamp_name, previous_at
    ):
        label = "Borg" if kind == "borg" else "EXFAT512 snapshot"
        error = None
        stopped = False
        try:
            result = self.command(
                [SUDO, "-n", tool, "--force"],
                timeout=self.backup_timeout,
            )
            if result.returncode:
                with self.lock:
                    stop_operation = copy.deepcopy(self.stop_operation)
                stopped = (
                    result.returncode == 143
                    and stop_operation.get("kind") == kind
                    and stop_operation.get("started_at") is not None
                    and stop_operation["started_at"] >= started_at
                )
                if not stopped:
                    error = (
                        result.stderr or result.stdout or f"{label} backup failed"
                    ).strip()[-500:]
            else:
                current_at = self._stamp(os.path.join(self.stamp_dir, stamp_name))
                if not self._stamp_advanced(current_at, previous_at):
                    output = (result.stderr or result.stdout or "").strip().splitlines()
                    detail = output[-1][-300:] if output else ""
                    error = f"backup finished without recording a new {label} success"
                    if detail:
                        error = f"{error}: {detail}"
        except subprocess.TimeoutExpired:
            error = f"backup timed out after {self.backup_timeout:g} seconds"
        except (OSError, BackupStatusError) as exc:
            error = f"could not run backup: {exc}"
        with self.lock:
            if self.operation.get("started_at") == started_at:
                self.operation.update(
                    {
                        "status": "stopped" if stopped else "error" if error else "complete",
                        "completed_at": int(self.wall_clock()),
                        "error": error,
                    }
                )
