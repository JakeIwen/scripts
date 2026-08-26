"""Managed USB-disk status and guarded lifecycle controller.

This module supports both repository package imports and the flat sibling
layout used by the deployed van-dashboard service.
"""

__all__ = ["DiskCommandError", "DiskManager"]

if __package__:
    from .van_dashboard_common import (
        BOOT_ID_PATH,
        DISKCTL,
        DISK_ACTION_TIMEOUT,
        DISK_EJECT_HOLD_DIR,
        DISK_HEALTH_STATE_DIR,
        DISK_POLICY_CONF,
        DISK_STATUS_TIMEOUT,
        LSBLK,
        SUDO,
        SYSTEM_MONITOR_DB,
        copy,
        json,
        os,
        re,
        read_text_file,
        run_command,
        shlex,
        sqlite3,
        subprocess,
        threading,
        time,
    )
else:
    from van_dashboard_common import (
        BOOT_ID_PATH,
        DISKCTL,
        DISK_ACTION_TIMEOUT,
        DISK_EJECT_HOLD_DIR,
        DISK_HEALTH_STATE_DIR,
        DISK_POLICY_CONF,
        DISK_STATUS_TIMEOUT,
        LSBLK,
        SUDO,
        SYSTEM_MONITOR_DB,
        copy,
        json,
        os,
        re,
        read_text_file,
        run_command,
        shlex,
        sqlite3,
        subprocess,
        threading,
        time,
    )

if __package__:
    from .van_dashboard_block_devices import flatten_block_devices, root_block_device
else:
    from van_dashboard_block_devices import flatten_block_devices, root_block_device


class DiskCommandError(RuntimeError):
    pass


class DiskManager:
    """Report configured USB disks and run label-only lifecycle actions."""

    LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

    def __init__(
        self,
        config=DISK_POLICY_CONF,
        control=DISKCTL,
        hold_dir=DISK_EJECT_HOLD_DIR,
        health_dir=DISK_HEALTH_STATE_DIR,
        event_database=SYSTEM_MONITOR_DB,
        boot_id_path=BOOT_ID_PATH,
        command=run_command,
        timeout=DISK_STATUS_TIMEOUT,
        action_timeout=DISK_ACTION_TIMEOUT,
        wall_clock=time.time,
    ):
        self.config = config
        self.control = control
        self.hold_dir = hold_dir
        self.health_dir = health_dir
        self.event_database = event_database
        self.boot_id_path = boot_id_path
        self.command = command
        self.timeout = timeout
        self.action_timeout = action_timeout
        self.wall_clock = wall_clock
        self.lock = threading.Lock()
        self.thread = None
        self.operation = {
            "status": "idle",
            "action": None,
            "label": None,
            "started_at": None,
            "completed_at": None,
            "error": None,
        }

    @classmethod
    def _parse_array(cls, text, name):
        match = re.search(
            rf"^\s*{re.escape(name)}=\((.*?)^\s*\)",
            text,
            re.MULTILINE | re.DOTALL,
        )
        if not match:
            raise DiskCommandError(f"disk policy has no {name}")
        try:
            labels = shlex.split(match.group(1), comments=True, posix=True)
        except ValueError as exc:
            raise DiskCommandError(f"could not parse {name}: {exc}") from exc
        if not labels or len(labels) != len(set(labels)) or any(
            not cls.LABEL_RE.fullmatch(label) for label in labels
        ):
            raise DiskCommandError(f"disk policy has invalid {name}")
        return labels

    @classmethod
    def parse_config(cls, text):
        mount_labels = cls._parse_array(text, "MOUNT_LABELS")
        always_mount_labels = cls._parse_array(text, "ALWAYS_MOUNT_LABELS")
        manual_mount_labels = cls._parse_array(text, "MANUAL_MOUNT_LABELS")
        hdd_labels = cls._parse_array(text, "HDD_LABELS")
        if not set(always_mount_labels).issubset(mount_labels):
            raise DiskCommandError("ALWAYS_MOUNT_LABELS must be a subset of MOUNT_LABELS")
        controllable_labels = [
            *mount_labels,
            *(label for label in manual_mount_labels if label not in mount_labels),
        ]
        observed = [
            *controllable_labels,
            *(label for label in hdd_labels if label not in controllable_labels),
        ]
        return {
            "mount_labels": mount_labels,
            "always_mount_labels": always_mount_labels,
            "manual_mount_labels": manual_mount_labels,
            "controllable_labels": controllable_labels,
            "hdd_labels": hdd_labels,
            "labels": observed,
        }

    def _configuration(self):
        try:
            return self.parse_config(read_text_file(self.config))
        except OSError as exc:
            raise DiskCommandError(f"could not read disk policy: {exc}") from exc

    def _block_devices(self):
        args = [
            LSBLK,
            "--json",
            "--bytes",
            "--output",
            "NAME,PATH,PKNAME,LABEL,PARTLABEL,FSTYPE,SIZE,TRAN,MOUNTPOINTS,TYPE",
        ]
        try:
            result = self.command(args, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise DiskCommandError(
                f"disk discovery timed out after {self.timeout:g} seconds"
            ) from exc
        except OSError as exc:
            raise DiskCommandError(f"could not inspect disks: {exc}") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout or "lsblk failed").strip()[-500:]
            raise DiskCommandError(detail)
        try:
            payload = json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            raise DiskCommandError("disk discovery returned invalid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("blockdevices"), list):
            raise DiskCommandError("disk discovery returned an invalid schema")
        return flatten_block_devices(payload["blockdevices"])

    def _hold(self, label, now):
        path = os.path.join(self.hold_dir, label)
        try:
            if os.path.islink(path):
                return {"until": None, "remaining_seconds": None, "error": "unsafe hold marker"}
            with open(path, encoding="ascii") as handle:
                raw = handle.readline().strip()
        except FileNotFoundError:
            return {"until": None, "remaining_seconds": 0, "error": None}
        except OSError as exc:
            return {"until": None, "remaining_seconds": None, "error": str(exc)}
        if not re.fullmatch(r"[1-9][0-9]{0,10}", raw):
            return {"until": None, "remaining_seconds": None, "error": "malformed hold marker"}
        deadline = int(raw)
        if deadline <= now:
            return {"until": None, "remaining_seconds": 0, "error": None}
        return {
            "until": deadline,
            "remaining_seconds": deadline - now,
            "error": None,
        }

    @staticmethod
    def _row_mounts(row):
        points = row.get("mountpoints")
        if not isinstance(points, list):
            points = [row.get("mountpoint")]
        return [point for point in points if isinstance(point, str) and point]

    def _saved_health(self, label):
        path = os.path.join(self.health_dir, f"{label}.json")
        try:
            if os.path.islink(path):
                raise DiskCommandError("unsafe disk-health state")
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise DiskCommandError(f"could not read disk health for {label}: {exc}") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or payload.get("label") != label
            or payload.get("state") not in ("checking", "healthy", "warning", "critical")
            or not isinstance(payload.get("message"), str)
            or not isinstance(payload.get("checked_at"), int)
            or isinstance(payload.get("checked_at"), bool)
        ):
            raise DiskCommandError(f"disk health for {label} has an invalid schema")
        return payload

    def _storage_events(self, labels, since):
        mapped = {label: [] for label in labels}
        try:
            absolute = os.path.abspath(self.event_database)
            uri = f"file:{absolute.replace('?', '%3f').replace('#', '%23')}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=2)
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(
                    """
                    SELECT timestamp, severity, message, state_json
                    FROM events
                    WHERE category = 'storage' AND timestamp >= ?
                    ORDER BY timestamp DESC
                    LIMIT 2000
                    """,
                    (since,),
                ).fetchall()
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            return mapped, "Storage event history unavailable"

        wanted = set(labels)
        for row in rows:
            message = str(row["message"])
            device_match = re.search(
                r"\b((?:sd[a-z][0-9]*|mmcblk[0-9]+p?[0-9]*))(?:-[0-9]+)?\b",
                message,
                re.IGNORECASE,
            )
            if not device_match:
                continue
            event_device = device_match.group(1).lower()
            if event_device.startswith("mmcblk"):
                event_parent = re.sub(r"p[0-9]+$", "", event_device)
            else:
                event_parent = re.sub(r"[0-9]+$", "", event_device)
            try:
                state = json.loads(row["state_json"] or "{}")
            except (TypeError, ValueError):
                continue
            event_boot_id = state.get("boot_id") if isinstance(state, dict) else None
            if not isinstance(event_boot_id, str) or not re.fullmatch(
                r"[0-9a-f]{32}", event_boot_id
            ):
                event_boot_id = None
            event_labels = set()
            disk_io = state.get("disk_io") if isinstance(state, dict) else None
            devices = disk_io.get("devices") if isinstance(disk_io, dict) else None
            for device in devices if isinstance(devices, list) else ():
                if not isinstance(device, dict):
                    continue
                device_name = str(device.get("name") or "").lower()
                if device_name not in (event_device, event_parent):
                    continue
                device_labels = device.get("labels")
                if isinstance(device_labels, list):
                    event_labels.update(
                        label for label in device_labels if isinstance(label, str)
                    )
            for label in event_labels & wanted:
                mapped[label].append(
                    {
                        "timestamp": int(row["timestamp"]),
                        "severity": row["severity"],
                        "message": message[:500],
                        "boot_id": event_boot_id,
                    }
                )
        return mapped, None

    def _current_boot_id(self):
        try:
            boot_id = (
                read_text_file(self.boot_id_path).strip().lower().replace("-", "")
            )
        except OSError:
            return None
        return boot_id if re.fullmatch(r"[0-9a-f]{32}", boot_id) else None

    @staticmethod
    def _mount_health(label, mounted, expected_mount):
        if not mounted:
            return {
                "read_only": None,
                "accessible": None,
                "writable": None,
                "error": None,
            }
        try:
            flags = os.statvfs(expected_mount).f_flag
            read_only = bool(flags & getattr(os, "ST_RDONLY", 1))
            accessible = os.access(expected_mount, os.R_OK | os.X_OK)
            writable = os.access(expected_mount, os.W_OK | os.X_OK)
        except OSError as exc:
            return {
                "read_only": None,
                "accessible": False,
                "writable": False,
                "error": f"Cannot inspect mounted filesystem: {exc}",
            }
        error = None
        if read_only:
            error = "Filesystem is mounted read-only"
        elif not accessible:
            error = "Mounted filesystem is not accessible by dashboard user"
        elif not writable:
            error = "Mounted filesystem is not writable by dashboard user"
        return {
            "read_only": read_only,
            "accessible": accessible,
            "writable": writable,
            "error": error,
        }

    def status(self):
        now = int(self.wall_clock())
        configuration = self._configuration()
        rows = self._block_devices()
        mount_labels = set(configuration["mount_labels"])
        always_mount_labels = set(configuration["always_mount_labels"])
        controllable_labels = set(configuration["controllable_labels"])
        current_boot_id = self._current_boot_id()
        storage_events, event_error = self._storage_events(
            configuration["labels"], now - 7 * 24 * 60 * 60
        )
        disks = []
        for label in configuration["labels"]:
            matches = [
                row
                for row in rows
                if row.get("label") == label or row.get("partlabel") == label
            ]
            # LABEL and PARTLABEL can both match the same partition; flatten
            # yields it only once. Multiple rows are an ambiguous unsafe state.
            row = matches[0] if len(matches) == 1 else None
            root = root_block_device(row) if row is not None else None
            mounts = self._row_mounts(row) if row is not None else []
            expected_mount = f"/mnt/{label}"
            hold = self._hold(label, now) if label in mount_labels else {
                "until": None,
                "remaining_seconds": 0,
                "error": None,
            }
            error = None
            if len(matches) > 1:
                error = "label resolves to multiple devices"
            elif root is not None and root.get("tran") != "usb":
                error = "label is not on a USB disk"
            elif any(point != expected_mount for point in mounts):
                error = "mounted at an unexpected path"
            elif hold["error"]:
                error = hold["error"]
            attached = row is not None
            mounted = expected_mount in mounts
            saved_health = self._saved_health(label)
            checked_at = saved_health["checked_at"] if saved_health else None
            events = storage_events.get(label, [])
            unresolved_events = [
                event
                for event in events
                if checked_at is None or event["timestamp"] > checked_at
            ]
            current_boot_events = [
                event
                for event in unresolved_events
                if current_boot_id is not None and event["boot_id"] == current_boot_id
            ]
            previous_boot_events = [
                event
                for event in unresolved_events
                if current_boot_id is not None
                and event["boot_id"] is not None
                and event["boot_id"] != current_boot_id
            ]
            unknown_boot_events = [
                event
                for event in unresolved_events
                if event not in current_boot_events and event not in previous_boot_events
            ]
            mount_health = self._mount_health(label, mounted, expected_mount)
            if mounted and not mount_health["error"]:
                observation = "Currently mounted read/write; access checks pass"
            elif mounted:
                observation = "Currently mounted with an access fault"
            elif attached:
                observation = "Currently attached and unmounted"
            else:
                observation = "Currently not attached"
            if mount_health["error"]:
                health_state = "critical"
                health_message = mount_health["error"]
                health_basis = "mount"
                event_scope = None
            elif current_boot_events:
                health_state = "critical"
                health_message = current_boot_events[0]["message"]
                health_basis = "kernel_event"
                event_scope = "current_boot"
            elif saved_health and saved_health["state"] in ("warning", "critical"):
                health_state = saved_health["state"]
                health_message = saved_health["message"]
                health_basis = "offline_check"
                event_scope = "cleared" if events else None
            elif saved_health and not unresolved_events:
                health_state = saved_health["state"]
                health_message = saved_health["message"]
                health_basis = "offline_check"
                event_scope = "cleared" if events else None
            elif event_error:
                health_state = "unknown"
                health_message = event_error
                health_basis = "history_unavailable"
                event_scope = None
            elif attached:
                health_state = "unknown"
                health_message = "No offline filesystem check has been recorded"
                health_basis = "unverified"
                event_scope = None
            else:
                health_state = "unknown"
                health_message = "Disk is not attached"
                health_basis = "unverified"
                event_scope = None
            disks.append(
                {
                    "label": label,
                    "role": (
                        "always"
                        if label in always_mount_labels
                        else "policy" if label in mount_labels else "backup"
                    ),
                    "automatic_mount": label in mount_labels,
                    "requires_disk_policy": label not in always_mount_labels,
                    "controllable": label in controllable_labels,
                    "attached": attached,
                    "mounted": mounted,
                    "mountpoints": mounts,
                    "device": root.get("path") if root is not None else None,
                    "size_bytes": root.get("size") if root is not None else None,
                    "filesystem": row.get("fstype") if row is not None else None,
                    "expected_mount": expected_mount,
                    "hold_until": hold["until"],
                    "hold_remaining_seconds": hold["remaining_seconds"],
                    "error": error,
                    "health": {
                        "state": health_state,
                        "message": health_message,
                        "basis": health_basis,
                        "observation": observation,
                        "event_scope": event_scope,
                        "checked_at": checked_at,
                        "read_only": mount_health["read_only"],
                        "accessible": mount_health["accessible"],
                        "writable": mount_health["writable"],
                        "recent_error_count": len(unresolved_events),
                        "current_boot_error_count": len(current_boot_events),
                        "previous_boot_error_count": len(previous_boot_events),
                        "historical_error_count": len(events),
                        "latest_error_at": events[0]["timestamp"] if events else None,
                        "latest_error": events[0]["message"] if events else None,
                        "current_error_at": (
                            current_boot_events[0]["timestamp"]
                            if current_boot_events
                            else checked_at
                            if saved_health
                            and saved_health["state"] in ("warning", "critical")
                            else None
                        ),
                        "current_error_message": (
                            health_message
                            if health_state in ("warning", "critical")
                            else None
                        ),
                        "repairable": (
                            attached
                            and row.get("fstype") in ("exfat", "ext4")
                            and label in controllable_labels
                        ),
                    },
                }
            )
        with self.lock:
            operation = copy.deepcopy(self.operation)
        return {"checked_at": now, "disks": disks, "operation": operation}

    def start_action(self, label, action):
        configuration = self._configuration()
        if label not in configuration["controllable_labels"]:
            raise ValueError("unknown controllable disk label")
        if action not in ("eject", "mount", "repair"):
            raise ValueError("unknown disk action")
        current = self.status()
        disk = next(item for item in current["disks"] if item["label"] == label)
        if disk["error"]:
            raise DiskCommandError(f"{label}: {disk['error']}")
        if not disk["attached"]:
            raise DiskCommandError(f"{label} is not attached")
        if action == "eject" and not disk["mounted"]:
            raise DiskCommandError(f"{label} is not mounted")
        if action == "mount" and disk["mounted"]:
            raise DiskCommandError(f"{label} is already mounted")
        if action == "repair" and not disk["health"]["repairable"]:
            raise DiskCommandError(f"{label} does not support automatic repair")
        with self.lock:
            if self.operation["status"] == "running":
                raise DiskCommandError("another disk action is already running")
            started_at = int(self.wall_clock())
            self.operation = {
                "status": "running",
                "action": action,
                "label": label,
                "started_at": started_at,
                "completed_at": None,
                "error": None,
            }
            self.thread = threading.Thread(
                target=self._run_action,
                args=(label, action, started_at),
                name="disk-action",
                daemon=True,
            )
            self.thread.start()
        return self.status()

    def _run_action(self, label, action, started_at):
        error = None
        try:
            result = self.command(
                [self.control, action, label], timeout=self.action_timeout
            )
            if result.returncode:
                error = (result.stderr or result.stdout or "disk action failed").strip()[-500:]
        except subprocess.TimeoutExpired:
            error = f"disk action timed out after {self.action_timeout:g} seconds"
        except OSError as exc:
            error = f"could not start disk action: {exc}"
        with self.lock:
            if self.operation.get("started_at") == started_at:
                self.operation.update(
                    {
                        "status": "error" if error else "complete",
                        "completed_at": int(self.wall_clock()),
                        "error": error,
                    }
                )
