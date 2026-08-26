"""Connectivity, OpenWrt, UBNT Wi-Fi, and speed-test controllers.

This module supports both repository package imports and the flat sibling
layout used by the deployed van-dashboard service.
"""

__all__ = [
    "ConnectivityMonitor",
    "OpenWrtClientsController",
    "OpenWrtClientsError",
    "SpeedTestManager",
    "UbntWifiController",
    "parse_speedtest_output",
]

if __package__:
    from .van_dashboard_common import (
        CONNECTIVITY_ACTIVE_LEASE,
        CONNECTIVITY_ACTIVE_RETRY_INTERVAL,
        CONNECTIVITY_INTERVAL,
        CONNECTIVITY_STATUS,
        OPENWRT_CLIENTS_TIMEOUT,
        SPEEDTEST,
        SPEEDTEST_TIMEOUT,
        UBNT_WIFI_TOOL,
        copy,
        json,
        re,
        run_command,
        subprocess,
        threading,
        time,
    )
else:
    from van_dashboard_common import (
        CONNECTIVITY_ACTIVE_LEASE,
        CONNECTIVITY_ACTIVE_RETRY_INTERVAL,
        CONNECTIVITY_INTERVAL,
        CONNECTIVITY_STATUS,
        OPENWRT_CLIENTS_TIMEOUT,
        SPEEDTEST,
        SPEEDTEST_TIMEOUT,
        UBNT_WIFI_TOOL,
        copy,
        json,
        re,
        run_command,
        subprocess,
        threading,
        time,
    )


class ConnectivityMonitor:
    """Cache the reusable connectivity collector away from HTTP request threads."""

    def __init__(
        self,
        collector=CONNECTIVITY_STATUS,
        interval=CONNECTIVITY_INTERVAL,
        command=run_command,
        clock=time.monotonic,
        wall_clock=time.time,
    ):
        self.collector = collector
        self.interval = interval
        self.command = command
        self.clock = clock
        self.wall_clock = wall_clock
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.stop_event = threading.Event()
        self.thread = None
        self.refreshing = False
        self.refresh_requested = False
        self.active_until = 0.0
        self.last_error = None
        self.data = {
            "checked_at": None,
            "internet": {"online": None, "source": "mwan3 reachability tracking"},
            "router": {
                "reachable": None,
                "mode": None,
                "online": [],
                "interfaces": [],
                "default_policy": None,
                "route_members": [],
                "error": None,
            },
            "ubnt": {
                "reachable": None,
                "connected": None,
                "ssid": None,
                "signal_dbm": None,
                "noise_dbm": None,
                "quality_percent": None,
                "ccq_percent": None,
                "bitrate": None,
                "error": None,
            },
        }

    def start(self):
        if not self.thread:
            self.thread = threading.Thread(
                target=self._loop, name="connectivity-monitor", daemon=True
            )
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        with self.condition:
            self.condition.notify_all()

    def request_refresh(self):
        """Schedule a sample without doing router I/O in the request thread."""
        with self.condition:
            self.refresh_requested = True
            self.condition.notify_all()

    def mark_active(self, lease=CONNECTIVITY_ACTIVE_LEASE):
        """Keep sequential collection active while a dashboard is visible."""
        with self.condition:
            self.active_until = max(self.active_until, self.clock() + lease)
            self.condition.notify_all()

    def _active_locked(self):
        return self.clock() < self.active_until

    def refresh(self):
        with self.condition:
            self.refreshing = True
        payload = None
        error = None
        try:
            result = self.command([self.collector], timeout=25)
            if result.returncode:
                detail = (result.stderr or result.stdout or "collector failed").strip()
                raise RuntimeError(detail[-300:])
            payload = json.loads(result.stdout)
            if not isinstance(payload, dict) or not all(
                key in payload for key in ("checked_at", "internet", "router", "ubnt")
            ):
                raise ValueError("collector returned an incomplete payload")
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            error = str(exc)
        finally:
            with self.condition:
                if error is not None:
                    self.last_error = error
                else:
                    assert payload is not None
                    self.data = payload
                    self.last_error = None
                self.refreshing = False
                self.condition.notify_all()
        return self.snapshot()

    def snapshot(self):
        with self.lock:
            data = copy.deepcopy(self.data)
            data["refreshing"] = self.refreshing
            data["last_error"] = self.last_error
            data["active_mode"] = self._active_locked()
        checked_at = data.get("checked_at")
        data["stale"] = checked_at is None or (
            self.wall_clock() - checked_at > max(60.0, self.interval * 2.5)
        )
        return data

    def _loop(self):
        while not self.stop_event.is_set():
            with self.condition:
                self.refresh_requested = False
            started = self.clock()
            status = self.refresh()
            remaining = max(1.0, self.interval - (self.clock() - started))
            with self.condition:
                if self._active_locked() and status.get("last_error") is None:
                    continue
                if self._active_locked():
                    self.condition.wait_for(
                        lambda: self.stop_event.is_set() or self.refresh_requested,
                        timeout=min(remaining, CONNECTIVITY_ACTIVE_RETRY_INTERVAL),
                    )
                else:
                    self.condition.wait_for(
                        lambda: self.stop_event.is_set()
                        or self.refresh_requested
                        or self._active_locked(),
                        timeout=remaining,
                    )


class OpenWrtClientsError(RuntimeError):
    pass


class OpenWrtClientsController:
    """Read the fixed, passive OpenWrt client inventory on demand."""

    CLIENT_FIELDS = {
        "name",
        "hostname_known",
        "ip",
        "mac",
        "connection",
        "interface",
        "radio",
        "band",
        "neighbor_state",
        "signal_dbm",
        "rx_rate_bps",
        "tx_rate_bps",
        "rx_bytes",
        "tx_bytes",
        "lease_expires_at",
    }
    NUMBER_FIELDS = {
        "rx_rate_bps",
        "tx_rate_bps",
        "rx_bytes",
        "tx_bytes",
        "lease_expires_at",
    }

    def __init__(
        self,
        collector=CONNECTIVITY_STATUS,
        command=run_command,
        timeout=OPENWRT_CLIENTS_TIMEOUT,
    ):
        self.collector = collector
        self.command = command
        self.timeout = timeout

    @staticmethod
    def _valid_ipv4(value):
        if value is None:
            return True
        if not isinstance(value, str) or not re.fullmatch(
            r"(?:\d{1,3}\.){3}\d{1,3}", value
        ):
            return False
        return all(0 <= int(part) <= 255 for part in value.split("."))

    @classmethod
    def parse_status(cls, output):
        try:
            payload = json.loads(output)
        except (TypeError, ValueError) as exc:
            raise OpenWrtClientsError("client collector returned invalid JSON") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or not isinstance(payload.get("checked_at"), int)
            or isinstance(payload.get("checked_at"), bool)
            or not isinstance(payload.get("clients"), list)
            or len(payload["clients"]) > 256
        ):
            raise OpenWrtClientsError("client collector returned an invalid payload")

        clients = []
        for item in payload["clients"]:
            if not isinstance(item, dict) or set(item) != cls.CLIENT_FIELDS:
                raise OpenWrtClientsError("client collector returned an invalid device")
            if (
                not isinstance(item["name"], str)
                or not item["name"]
                or len(item["name"]) > 100
                or not isinstance(item["hostname_known"], bool)
                or not cls._valid_ipv4(item["ip"])
                or not isinstance(item["mac"], str)
                or not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", item["mac"])
                or item["connection"] not in ("wifi", "lan")
                or not isinstance(item["interface"], str)
                or len(item["interface"]) > 50
                or (
                    item["radio"] is not None
                    and (
                        not isinstance(item["radio"], str)
                        or not re.fullmatch(r"radio\d+", item["radio"])
                    )
                )
                or item["band"] not in (None, "2.4 GHz", "5 GHz", "6 GHz")
                or (item["radio"] is None) != (item["band"] is None)
                or (
                    item["connection"] == "lan"
                    and (item["radio"] is not None or item["band"] is not None)
                )
                or (
                    item["neighbor_state"] is not None
                    and item["neighbor_state"]
                    not in ("REACHABLE", "DELAY", "PROBE", "PERMANENT", "STALE")
                )
                or (
                    item["signal_dbm"] is not None
                    and (
                        not isinstance(item["signal_dbm"], int)
                        or not -150 <= item["signal_dbm"] <= 0
                    )
                )
                or any(
                    item[field] is not None
                    and (
                        not isinstance(item[field], int)
                        or isinstance(item[field], bool)
                        or item[field] < 0
                    )
                    for field in cls.NUMBER_FIELDS
                )
            ):
                raise OpenWrtClientsError("client collector returned invalid device data")
            clients.append(item)

        wifi_count = sum(item["connection"] == "wifi" for item in clients)
        lan_count = len(clients) - wifi_count
        invalid_counts = any(
            not isinstance(payload.get(field), int)
            or isinstance(payload.get(field), bool)
            for field in ("client_count", "wifi_count", "lan_count")
        )
        if (
            invalid_counts
            or payload.get("client_count") != len(clients)
            or payload.get("wifi_count") != wifi_count
            or payload.get("lan_count") != lan_count
        ):
            raise OpenWrtClientsError("client collector returned inconsistent counts")
        return {
            "version": 1,
            "checked_at": payload["checked_at"],
            "client_count": len(clients),
            "wifi_count": wifi_count,
            "lan_count": lan_count,
            "clients": clients,
        }

    def status(self):
        try:
            result = self.command([self.collector, "--clients"], timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise OpenWrtClientsError(
                f"client query timed out after {self.timeout:g} seconds"
            ) from exc
        except OSError as exc:
            raise OpenWrtClientsError(f"could not start client query: {exc}") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout or "client query failed").strip()
            raise OpenWrtClientsError(detail[-300:])
        return self.parse_status(result.stdout)


class UbntWifiController:
    """Run scans and network changes off-thread through the reusable JSON tool."""

    TIMEOUTS = {
        "status": 20,
        "scan": 45,
        "connect": 260,
        "provision": 260,
        "update-profile": 280,
        "resume": 20,
    }

    def __init__(
        self,
        tool=UBNT_WIFI_TOOL,
        command=run_command,
        wall_clock=time.time,
        on_change=None,
    ):
        self.tool = tool
        self.command = command
        self.wall_clock = wall_clock
        self.on_change = on_change
        self.lock = threading.Lock()
        self.thread = None
        self.wifi = {
            "version": 1,
            "reachable": None,
            "checked_at": None,
            "state": {
                "configured_ssid": None,
                "associated_ssid": None,
                "ccq_percent": None,
                "automatic_paused": None,
                "selector_running": None,
            },
            "profiles": [],
            "networks": [],
        }
        self.operation = {
            "status": "idle",
            "kind": None,
            "started_at": None,
            "completed_at": None,
            "message": None,
            "error": None,
        }

    def snapshot(self):
        with self.lock:
            return {
                "wifi": copy.deepcopy(self.wifi),
                "operation": dict(self.operation),
            }

    def request_refresh(self, max_age=20):
        with self.lock:
            checked_at = self.wifi.get("checked_at")
            running = self.operation["status"] == "running"
            completed_at = self.operation.get("completed_at")
        recent_attempt = completed_at is not None and (
            self.wall_clock() - completed_at <= max_age
        )
        if not running and (
            (checked_at is None and not recent_attempt)
            or (checked_at is not None and self.wall_clock() - checked_at > max_age)
        ):
            self.start("status")

    def start(self, kind, payload=None):
        if kind not in self.TIMEOUTS:
            raise ValueError("unknown UBNT Wi-Fi operation")
        with self.lock:
            if self.operation["status"] == "running":
                return False
            self.operation = {
                "status": "running",
                "kind": kind,
                "started_at": int(self.wall_clock()),
                "completed_at": None,
                "message": None,
                "error": None,
            }
            self.thread = threading.Thread(
                target=self._run,
                args=(kind, dict(payload or {})),
                name=f"ubnt-wifi-{kind}",
                daemon=True,
            )
            self.thread.start()
            return True

    def _tool_result(self, kind, payload=None):
        input_text = json.dumps(payload, separators=(",", ":")) if payload else None
        try:
            result = self.command(
                [self.tool, "--json", kind],
                timeout=self.TIMEOUTS[kind],
                input_text=input_text,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"UBNT {kind} timed out") from exc
        except OSError as exc:
            raise RuntimeError(f"could not start UBNT {kind}: {exc}") from exc
        finally:
            input_text = None
            if payload and "password" in payload:
                payload["password"] = ""
        try:
            parsed = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("UBNT Wi-Fi tool returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("UBNT Wi-Fi tool returned invalid data")
        if result.returncode or parsed.get("ok") is not True:
            raise RuntimeError(str(parsed.get("message") or "UBNT Wi-Fi operation failed"))
        wifi = parsed.get("wifi")
        if not isinstance(wifi, dict) or wifi.get("version") != 1:
            raise RuntimeError("UBNT Wi-Fi tool returned invalid status")
        return parsed

    def _refresh_after_failure(self):
        try:
            return self._tool_result("status").get("wifi")
        except RuntimeError:
            return None

    def _run(self, kind, payload):
        error = None
        message = None
        wifi = None
        try:
            result = self._tool_result(kind, payload)
            wifi = result["wifi"]
            message = result.get("message")
            if kind in ("connect", "provision", "update-profile", "resume") and self.on_change:
                self.on_change()
        except RuntimeError as exc:
            error = str(exc)
            wifi = self._refresh_after_failure()
        finally:
            if "password" in payload:
                payload["password"] = ""
        with self.lock:
            if wifi is not None:
                self.wifi = wifi
            self.operation = {
                "status": "error" if error else "complete",
                "kind": kind,
                "started_at": self.operation["started_at"],
                "completed_at": int(self.wall_clock()),
                "message": message,
                "error": error,
            }


def parse_speedtest_output(output):
    values = {}
    patterns = {
        "download_mbps": r"^Download(?: Speed)?:\s*([0-9.]+)\s*(?:Mbit/s|Mbps)",
        "upload_mbps": r"^Upload(?: Speed)?:\s*([0-9.]+)\s*(?:Mbit/s|Mbps)",
        "latency_ms": r"^(?:Ping|Latency):\s*([0-9.]+)\s*ms",
    }
    for name, pattern in patterns.items():
        match = re.search(pattern, output, re.IGNORECASE | re.MULTILINE)
        if match:
            values[name] = float(match.group(1))
    return values


class SpeedTestManager:
    """Run the existing speedtest script once at a time, outside request threads."""

    def __init__(
        self,
        script=SPEEDTEST,
        command=run_command,
        timeout=SPEEDTEST_TIMEOUT,
        wall_clock=time.time,
    ):
        self.script = script
        self.command = command
        self.timeout = timeout
        self.wall_clock = wall_clock
        self.lock = threading.Lock()
        self.thread = None
        self.data = {
            "status": "idle",
            "started_at": None,
            "completed_at": None,
            "download_mbps": None,
            "upload_mbps": None,
            "latency_ms": None,
            "error": None,
        }

    def start(self):
        with self.lock:
            if self.data["status"] == "running":
                return False
            self.data = {
                "status": "running",
                "started_at": int(self.wall_clock()),
                "completed_at": None,
                "download_mbps": None,
                "upload_mbps": None,
                "latency_ms": None,
                "error": None,
            }
            self.thread = threading.Thread(target=self._run, name="speedtest", daemon=True)
            self.thread.start()
            return True

    def snapshot(self):
        with self.lock:
            return dict(self.data)

    def _run(self):
        error = None
        values = {}
        try:
            result = self.command([self.script], timeout=self.timeout)
            if result.returncode:
                detail = (result.stderr or result.stdout or "speed test failed").strip()
                error = detail[-500:]
            else:
                values = parse_speedtest_output(result.stdout)
                if len(values) != 3:
                    error = "speed test returned incomplete results"
        except subprocess.TimeoutExpired:
            error = f"speed test timed out after {self.timeout:g} seconds"
        except OSError as exc:
            error = f"could not start speed test: {exc}"

        with self.lock:
            self.data.update(values)
            self.data["status"] = "error" if error else "complete"
            self.data["completed_at"] = int(self.wall_clock())
            self.data["error"] = error
