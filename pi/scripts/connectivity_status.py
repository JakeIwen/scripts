#!/usr/bin/env python3
"""Report van network uplink and UBNT radio state as JSON.

This collector is intentionally passive and cheap: it reuses mwan3's existing
reachability tracking, pings the UBNT once to distinguish an unplugged cable,
and reads the UBNT's current association data without running a wireless scan.
"""

import json
import os
import re
import subprocess
import time


ROUTER_TARGET = os.environ.get("CONNECTIVITY_ROUTER_TARGET", "root@OpenWrt")
UBNT_TARGET = os.environ.get("CONNECTIVITY_UBNT_TARGET", "ubnt@192.168.8.20")
UBNT_HOST = os.environ.get("CONNECTIVITY_UBNT_HOST", UBNT_TARGET.rsplit("@", 1)[-1])
UBNT_IDENTITY = os.path.expanduser(
    os.environ.get("CONNECTIVITY_UBNT_IDENTITY", "~/.ssh/id_rsa")
)
SSH = os.environ.get("CONNECTIVITY_SSH", "/usr/bin/ssh")
PING = os.environ.get("CONNECTIVITY_PING", "/usr/bin/ping")

MWAN_PRIORITY = ("clientwan", "lifiwan", "wan")


def run_command(args, timeout=8):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def parse_mwan3_interfaces(output):
    """Parse ``mwan3 interfaces`` without depending on OpenWrt JSON helpers."""
    interfaces = []
    pattern = re.compile(
        r"^\s*interface\s+(?P<name>\S+)\s+is\s+"
        r"(?P<state>online|offline|disabled|unknown)(?P<detail>.*)$",
        re.IGNORECASE,
    )
    for line in output.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        detail = match.group("detail").strip(" ,")
        interfaces.append(
            {
                "name": match.group("name"),
                "state": match.group("state").lower(),
                "tracking": "paused" if "tracking is paused" in detail.lower() else "active",
                "detail": detail or None,
            }
        )
    return interfaces


def select_mode(interfaces):
    online = {item["name"] for item in interfaces if item["state"] == "online"}
    ordered = []
    for name in MWAN_PRIORITY:
        if name in online:
            ordered.append(name)
    ordered.extend(
        item["name"]
        for item in interfaces
        if item["state"] == "online" and item["name"] not in ordered
    )
    return " + ".join(ordered) or None


def _number(pattern, output, cast=int):
    match = re.search(pattern, output, re.IGNORECASE | re.MULTILINE)
    if not match:
        return None
    try:
        return cast(match.group(1))
    except (TypeError, ValueError):
        return None


def parse_ubnt_wireless(output):
    """Separate the configured ESSID from actual AP association state."""
    # AirOS can expose a profile label (for example STARLINK) as ESSID while
    # wpa_supplicant targets the real access-point SSID (for example denlink).
    essid_match = re.search(r"^target_ssid=(.*)$", output, re.IGNORECASE | re.MULTILINE)
    if not essid_match:
        essid_match = re.search(r'ESSID:"([^"]*)"', output, re.IGNORECASE)
    if not essid_match:
        essid_match = re.search(r"^essid=(.*)$", output, re.IGNORECASE | re.MULTILINE)
    ssid = essid_match.group(1).strip() if essid_match else None
    if ssid in ("", "off/any"):
        ssid = None

    ap_match = re.search(r"Access Point:\s*(\S+)", output, re.IGNORECASE)
    access_point = ap_match.group(1).strip() if ap_match else None
    disconnected_aps = {None, "not-associated", "00:00:00:00:00:00"}
    connected = access_point.lower() not in disconnected_aps if access_point else False

    quality_match = re.search(r"Link Quality[=:]\s*(\d+)\s*/\s*(\d+)", output, re.IGNORECASE)
    quality_percent = None
    if quality_match and int(quality_match.group(2)):
        quality_percent = round(100 * int(quality_match.group(1)) / int(quality_match.group(2)))

    bitrate_match = re.search(
        r"Bit Rate[=:]\s*([0-9.]+)\s*([^\s]+(?:/s)?)", output, re.IGNORECASE
    )
    bitrate = None
    if bitrate_match:
        bitrate = f"{bitrate_match.group(1)} {bitrate_match.group(2)}"

    return {
        "connected": connected,
        "ssid": ssid,
        "access_point": access_point,
        "signal_dbm": _number(r"Signal level[=:]\s*(-?\d+)", output),
        "noise_dbm": _number(r"Noise level[=:]\s*(-?\d+)", output),
        "quality_percent": quality_percent,
        "ccq_percent": _number(r"^ccq=(-?\d+)", output),
        "bitrate": bitrate,
        "frequency_ghz": _number(r"Frequency[=:]\s*([0-9.]+)\s*GHz", output, float),
    }


def _ssh_args(target, identity=None):
    args = [
        SSH,
        "-n",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=4",
    ]
    if identity:
        args.extend(("-i", identity))
    args.append(target)
    return args


def _failure_detail(result):
    detail = (result.stderr or result.stdout or "command failed").strip()
    return detail[-300:] if detail else "command failed"


def collect_status(command=run_command, wall_clock=time.time):
    checked_at = int(wall_clock())
    router = {
        "reachable": False,
        "mode": None,
        "online": [],
        "interfaces": [],
        "error": None,
    }
    internet_online = None

    try:
        result = command(
            _ssh_args(ROUTER_TARGET) + ["/usr/sbin/mwan3 interfaces"], timeout=8
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        router["error"] = str(exc)
    else:
        if result.returncode == 0:
            router["reachable"] = True
            router["interfaces"] = parse_mwan3_interfaces(result.stdout)
            router["online"] = [
                item["name"] for item in router["interfaces"] if item["state"] == "online"
            ]
            router["mode"] = select_mode(router["interfaces"])
            if router["interfaces"]:
                internet_online = bool(router["online"])
            else:
                router["error"] = "mwan3 returned no interface state"
        else:
            router["error"] = _failure_detail(result)

    ubnt = {
        "reachable": False,
        "connected": False,
        "ssid": None,
        "access_point": None,
        "signal_dbm": None,
        "noise_dbm": None,
        "quality_percent": None,
        "ccq_percent": None,
        "bitrate": None,
        "frequency_ghz": None,
        "error": None,
    }
    try:
        ping = command([PING, "-c", "1", "-W", "2", UBNT_HOST], timeout=4)
    except (OSError, subprocess.TimeoutExpired) as exc:
        ubnt["error"] = str(exc)
    else:
        ubnt["reachable"] = ping.returncode == 0
        if not ubnt["reachable"]:
            ubnt["error"] = "UBNT did not answer ping (check Ethernet cable/power)"

    if ubnt["reachable"]:
        remote_status = (
            "/sbin/iwconfig ath0 2>&1; "
            "/usr/bin/mca-status 2>/dev/null | "
            "/bin/grep -E '^(essid|signal|noise|ccq|uptime)=' || true; "
            "/usr/bin/awk -F= '/^wpasupplicant\\.profile\\.1\\.network\\.1\\.ssid=/"
            "{print \"target_ssid=\" $2; exit}' /tmp/system.cfg"
        )
        try:
            result = command(
                _ssh_args(UBNT_TARGET, UBNT_IDENTITY) + [remote_status], timeout=8
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            ubnt["error"] = f"UBNT status failed: {exc}"
        else:
            if result.returncode == 0:
                ubnt.update(parse_ubnt_wireless(result.stdout))
                ubnt["error"] = None
            else:
                ubnt["error"] = f"UBNT status failed: {_failure_detail(result)}"

    return {
        "checked_at": checked_at,
        "internet": {
            "online": internet_online,
            "source": "mwan3 reachability tracking",
        },
        "router": router,
        "ubnt": ubnt,
    }


def main():
    print(json.dumps(collect_status(), separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
