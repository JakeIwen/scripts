"""Shared configuration and low-level helpers for the van dashboard."""

import copy
import csv
import datetime
import glob
import hashlib
import json
import math
import os
import plistlib
import re
import shlex
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

PORT = int(os.environ.get("VAN_DASHBOARD_PORT", "8788"))
REACT_FRONTEND_ROOT = os.environ.get(
    "VAN_DASHBOARD_FRONTEND_ROOT",
    "/home/pi/scripts/van-dashboard-preview/current",
)
TELEMETRY_SNAPSHOT_URL = os.environ.get(
    "VAN_DASHBOARD_TELEMETRY_SNAPSHOT_URL",
    "http://192.168.6.103:8765/v1/snapshot",
)
TELEMETRY_SNAPSHOT_TIMEOUT = float(
    os.environ.get("VAN_DASHBOARD_TELEMETRY_SNAPSHOT_TIMEOUT", "3")
)
VOLTAGE_MON_CSV = os.environ.get(
    "VAN_DASHBOARD_VOLTAGE_MON_CSV",
    "/home/pi/dev/obd-things/tmp/battery/bcan_voltage.csv",
)
ENGINE_OFF_VOLTAGE_STATUS = os.environ.get(
    "VAN_DASHBOARD_ENGINE_OFF_VOLTAGE_STATUS",
    "/var/lib/van-telemetry/engine-off-voltage.json",
)
ENGINE_OFF_VOLTAGE_STATUS_MAX_BYTES = 16 * 1024
VOLTAGE_MON_TOOL = os.environ.get(
    "VAN_DASHBOARD_VOLTAGE_MON_TOOL",
    "/home/pi/dev/obd-things/projects/battery/voltage_mon.sh",
)
VOLTAGE_CHECK_TIMEOUT = float(
    os.environ.get("VAN_DASHBOARD_VOLTAGE_CHECK_TIMEOUT", "100")
)
PROC_UPTIME = os.environ.get("VAN_DASHBOARD_PROC_UPTIME", "/proc/uptime")
BOOT_ID_PATH = os.environ.get(
    "VAN_DASHBOARD_BOOT_ID_PATH", "/proc/sys/kernel/random/boot_id"
)
STATE_PATH = os.path.expanduser(
    os.environ.get("VAN_DASHBOARD_STATE_PATH", "~/.van_dashboard_state.json")
)
RUNTIME_DIR = os.environ.get("VAN_DASHBOARD_RUNTIME_DIR", "/run/van-dashboard")
ACTIVE_MARKER = os.path.join(RUNTIME_DIR, "cop-alert.active")
COP_CAN_WAKE_STATUS = os.environ.get(
    "VAN_DASHBOARD_COP_CAN_WAKE_STATUS",
    "/run/van-cop-can-wake/status.json",
)
COP_CAN_WAKE_SERVICE = "van-cop-can-wake.service"
COP_CAN_WAKE_STATUS_MAX_BYTES = 16 * 1024
IGNITION_MARKER = os.environ.get(
    "VAN_DASHBOARD_IGNITION_MARKER", "/home/pi/hooks/ignition_is_on"
)

TUYA_TOGGLE = os.environ.get("VAN_DASHBOARD_TUYA_TOGGLE", "/home/pi/scripts/tuya_toggle.sh")
TUYA_STATUS = os.environ.get("VAN_DASHBOARD_TUYA_STATUS", "/home/pi/scripts/tuya_status.sh")
NTFY_SEND = os.environ.get("VAN_DASHBOARD_NTFY_SEND", "/home/pi/scripts/ntfy_send.sh")
TUYA_LIGHT = os.environ.get("VAN_DASHBOARD_TUYA_LIGHT", "/home/pi/scripts/tuya_light.sh")
POLICYCTL = "/home/pi/scripts/policyctl"
CONNECTIVITY_STATUS = os.environ.get(
    "VAN_DASHBOARD_CONNECTIVITY_STATUS", "/home/pi/scripts/connectivity_status.py"
)
UBNT_WIFI_TOOL = os.environ.get(
    "VAN_DASHBOARD_UBNT_WIFI_TOOL", "/home/pi/scripts/ubnt_wifi.py"
)
SPEEDTEST = os.path.expanduser(
    os.environ.get("VAN_DASHBOARD_SPEEDTEST", "/home/pi/scripts/speedtest.sh")
)
USB_WATCH_TOOL = os.environ.get(
    "VAN_DASHBOARD_USB_WATCH_TOOL", "/home/pi/scripts/usb_watch.py"
)
USB2_RECOVERY_TOOL = os.environ.get(
    "VAN_DASHBOARD_USB2_RECOVERY_TOOL", "/home/pi/scripts/recover_usb2.sh"
)
CONNECTIVITY_INTERVAL = float(os.environ.get("VAN_DASHBOARD_CONNECTIVITY_INTERVAL", "30"))
CONNECTIVITY_ACTIVE_LEASE = float(
    os.environ.get("VAN_DASHBOARD_CONNECTIVITY_ACTIVE_LEASE", "10")
)
CONNECTIVITY_ACTIVE_RETRY_INTERVAL = float(
    os.environ.get("VAN_DASHBOARD_CONNECTIVITY_ACTIVE_RETRY_INTERVAL", "1")
)
OPENWRT_CLIENTS_TIMEOUT = float(
    os.environ.get("VAN_DASHBOARD_OPENWRT_CLIENTS_TIMEOUT", "15")
)
SPEEDTEST_TIMEOUT = float(os.environ.get("VAN_DASHBOARD_SPEEDTEST_TIMEOUT", "180"))
USB_WATCH_TIMEOUT = float(os.environ.get("VAN_DASHBOARD_USB_WATCH_TIMEOUT", "10"))
UHUBCTL = os.environ.get("VAN_DASHBOARD_UHUBCTL", "/usr/sbin/uhubctl")
SUDO = os.environ.get("VAN_DASHBOARD_SUDO", "/usr/bin/sudo")
TEE = os.environ.get("VAN_DASHBOARD_TEE", "/usr/bin/tee")
USB_PORT_TIMEOUT = float(os.environ.get("VAN_DASHBOARD_USB_PORT_TIMEOUT", "15"))
USB_PORT_SNAPSHOT_TTL = float(
    os.environ.get("VAN_DASHBOARD_USB_PORT_SNAPSHOT_TTL", "300")
)
USB2_RECOVERY_TIMEOUT = float(
    os.environ.get("VAN_DASHBOARD_USB2_RECOVERY_TIMEOUT", "30")
)
PRICE_CHECK_TOOL = os.environ.get(
    "VAN_DASHBOARD_PRICE_CHECK_TOOL", "/home/pi/scripts/price_check/main.py"
)
PRICE_CHECK_DB = os.path.expanduser(
    os.environ.get(
        "VAN_DASHBOARD_PRICE_CHECK_DB",
        "/home/pi/.local/share/price_check/price_check.sqlite3",
    )
)
PRICE_CHECK_TIMEOUT = float(os.environ.get("VAN_DASHBOARD_PRICE_CHECK_TIMEOUT", "180"))
SYSTEM_MONITOR_TOOL = os.environ.get(
    "VAN_DASHBOARD_SYSTEM_MONITOR_TOOL", "/home/pi/scripts/system_event_monitor.py"
)
SYSTEM_MONITOR_DB = os.environ.get(
    "VAN_DASHBOARD_SYSTEM_MONITOR_DB", "/var/lib/vanpi-monitor/events.sqlite3"
)
SYSTEM_MONITOR_TIMEOUT = float(
    os.environ.get("VAN_DASHBOARD_SYSTEM_MONITOR_TIMEOUT", "15")
)
COMPUTE_ROOT = os.environ.get(
    "VAN_DASHBOARD_COMPUTE_ROOT", "/home/pi/dev/obd-things/tmp/compute"
)
BACKUP_CONF = os.environ.get(
    "VAN_DASHBOARD_BACKUP_CONF", "/home/pi/scripts/backup/backup_conf.sh"
)
BACKUP_STAMP_DIR = os.environ.get(
    "VAN_DASHBOARD_BACKUP_STAMP_DIR", "/home/pi/backups/stamps"
)
BACKUP_CLONE_NOW = os.environ.get(
    "VAN_DASHBOARD_BACKUP_CLONE_NOW", "/home/pi/scripts/backup/clone_now.sh"
)
BACKUP_BORG_RUNNER = os.environ.get(
    "VAN_DASHBOARD_BORG_RUNNER", "/home/pi/scripts/backup/pi_backup.sh"
)
BACKUP_EXFAT_RUNNER = os.environ.get(
    "VAN_DASHBOARD_EXFAT_RUNNER", "/home/pi/scripts/backup/exfat_snapshot.sh"
)
BACKUP_ABORT = os.environ.get(
    "VAN_DASHBOARD_BACKUP_ABORT", "/home/pi/scripts/backup/abort_backup.sh"
)
BACKUP_OPENWRT_RUNNER = os.environ.get(
    "VAN_DASHBOARD_OPENWRT_RUNNER", "/home/pi/scripts/backup/openwrt_backup.sh"
)
TIME_MACHINE_BUNDLE = os.environ.get(
    "VAN_DASHBOARD_TIME_MACHINE_BUNDLE", "/mnt/mbp2tbkup/m4mac.sparsebundle"
)
LSBLK = os.environ.get("VAN_DASHBOARD_LSBLK", "/usr/bin/lsblk")
BACKUP_STATUS_TIMEOUT = float(os.environ.get("VAN_DASHBOARD_BACKUP_STATUS_TIMEOUT", "10"))
BACKUP_CLONE_TIMEOUT = float(
    os.environ.get("VAN_DASHBOARD_BACKUP_CLONE_TIMEOUT", str(6 * 60 * 60 + 90))
)
BACKUP_RUN_TIMEOUT = float(
    os.environ.get("VAN_DASHBOARD_BACKUP_RUN_TIMEOUT", str(8 * 60 * 60 + 90))
)
BACKUP_STOP_TIMEOUT = float(
    os.environ.get("VAN_DASHBOARD_BACKUP_STOP_TIMEOUT", "150")
)
IGNITIONMONCTL = os.environ.get(
    "VAN_DASHBOARD_IGNITIONMONCTL", "/home/pi/scripts/ignitionmonctl"
)
SYSTEMCTL = os.environ.get("VAN_DASHBOARD_SYSTEMCTL", "/usr/bin/systemctl")
SYSTEMD_RUN = os.environ.get("VAN_DASHBOARD_SYSTEMD_RUN", "/usr/bin/systemd-run")
DASHBOARD_SERVICE = os.environ.get(
    "VAN_DASHBOARD_SERVICE", "van-dashboard.service"
)
DASHBOARD_RESTART_TIMEOUT = float(
    os.environ.get("VAN_DASHBOARD_RESTART_TIMEOUT", "8")
)
TELEMETRY_SERVICE = "van-telemetry.service"
TELEMETRY_SERVICE_TIMEOUT = 15
IGNITIONMON_TIMEOUT = float(os.environ.get("VAN_DASHBOARD_IGNITIONMON_TIMEOUT", "8"))
IGNITIONMON_MAX_MINUTES = 366 * 24 * 60
DISK_POLICY_CONF = os.environ.get(
    "VAN_DASHBOARD_DISK_POLICY_CONF", "/home/pi/scripts/disk_policy.sh"
)
DISKCTL = os.environ.get("VAN_DASHBOARD_DISKCTL", "/home/pi/scripts/diskctl")
DISK_EJECT_HOLD_DIR = os.environ.get(
    "VAN_DASHBOARD_DISK_EJECT_HOLD_DIR", "/run/lock/vanpi-disk-eject"
)
DISK_HEALTH_STATE_DIR = os.environ.get(
    "VAN_DASHBOARD_DISK_HEALTH_STATE_DIR", "/var/lib/vanpi-disk-health"
)
DISK_STATUS_TIMEOUT = float(os.environ.get("VAN_DASHBOARD_DISK_STATUS_TIMEOUT", "10"))
DISK_ACTION_TIMEOUT = float(os.environ.get("VAN_DASHBOARD_DISK_ACTION_TIMEOUT", "1800"))
SAFE_REBOOT = os.environ.get(
    "VAN_DASHBOARD_SAFE_REBOOT", "/home/pi/scripts/safe_reboot.sh"
)
SAFE_POWER_DOWN = os.environ.get(
    "VAN_DASHBOARD_SAFE_POWER_DOWN", "/home/pi/scripts/safe_power_down.sh"
)
SYSTEM_POWER_TIMEOUT = float(
    os.environ.get("VAN_DASHBOARD_SYSTEM_POWER_TIMEOUT", "180")
)
TUYA_POLL_INTERVAL = float(os.environ.get("VAN_DASHBOARD_TUYA_POLL_INTERVAL", "15"))
POLICYCTL_TIMEOUT = 15
COP_LED_TARGET = os.environ.get("VAN_DASHBOARD_COP_LED_TARGET", "light.ext_led")
# Captured from solder_led on 2026-07-18. COP ALERT deliberately uses this
# fixed look; it does not query or depend on solder_led at activation time.
COP_LED_BRIGHTNESS = 255
COP_LED_COLOR_TEMP_KELVIN = 2702
COP_LED_RETRY_INTERVAL = float(os.environ.get("VAN_DASHBOARD_COP_LED_RETRY_INTERVAL", "5"))
COP_LED_VERIFY_INTERVAL = float(
    os.environ.get("VAN_DASHBOARD_COP_LED_VERIFY_INTERVAL", "30")
)
COP_LED_CONNECT_GRACE = float(os.environ.get("VAN_DASHBOARD_COP_LED_CONNECT_GRACE", "90"))

NTFY_INTERVAL = float(os.environ.get("VAN_DASHBOARD_NTFY_INTERVAL", "300"))
NTFY_TIMEOUT = float(os.environ.get("VAN_DASHBOARD_NTFY_TIMEOUT", "20"))
FLOOD_CHECK_INTERVAL = float(os.environ.get("VAN_DASHBOARD_FLOOD_CHECK_INTERVAL", "15"))

DEFAULT_SONOS_DEVICE = os.environ.get("VAN_DASHBOARD_SONOS_DEVICE", "vonFront")
SONOS_ART_TIMEOUT = 5
SONOS_ART_MAX_BYTES = 2 * 1024 * 1024

LIGHT_GROUPS = (
    (
        "cab",
        "Cab",
        (
            ("light.wiz_front_driver", "Driver"),
            ("light.wiz_front_passenger", "Passenger"),
        ),
    ),
    (
        "rear",
        "Rear",
        (
            ("light.wiz_dresser", "Dresser"),
            ("light.wiz_werkbench", "Workbench"),
        ),
    ),
    ("kitchen", "Kitchen", (("light.wiz_kitchen", "Kitchen"),)),
    ("exterior", "Exterior", (("light.ext_led", "Exterior LED"),)),
    ("solder", "Solder", (("light.solder_led", "Solder LED"),)),
    (
        "extra",
        "Extra",
        (
            ("light.extra_led_1", "LED 1"),
            ("light.extra_led_2", "LED 2"),
        ),
    ),
)
LIGHT_POWER_SWITCHES = {
    "exterior": ("switch.ext_flood", "Exterior power"),
    "solder": ("switch.solder_flood", "Solder power"),
}
LIGHT_COMMAND_TIMEOUT = 20
LIGHT_HUE_MODES = {"hs", "rgb", "rgbw", "rgbww", "xy"}


def atomic_json_write(path, value):
    """Write JSON without leaving a partially-written state file."""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=1, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


class StateStore:
    def __init__(self, path=STATE_PATH):
        self.path = path
        self.lock = threading.RLock()
        try:
            with open(path, encoding="utf-8") as handle:
                loaded = json.load(handle)
            self.data = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("cop_alert", False)
        self.data.setdefault("sonos_device", None)

    def get(self, key, default=None):
        with self.lock:
            return self.data.get(key, default)

    def set(self, key, value):
        with self.lock:
            self.data[key] = value
            atomic_json_write(self.path, self.data)


def run_command(args, timeout=20, input_text=None):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        input=input_text,
    )


def read_text_file(path):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read().strip()
