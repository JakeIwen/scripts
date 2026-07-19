#!/usr/bin/env python3
"""Reusable JSON interface between vanpi and the primary UBNT Wi-Fi manager."""

import argparse
import json
import os
import re
import subprocess
import sys
import time


SSH = os.environ.get("UBNT_WIFI_SSH", "/usr/bin/ssh")
TARGET = os.environ.get("UBNT_WIFI_TARGET", "ubnt@192.168.8.20")
IDENTITY = os.path.expanduser(os.environ.get("UBNT_WIFI_IDENTITY", "~/.ssh/id_rsa"))
REMOTE_MANAGER = os.environ.get(
    "UBNT_WIFI_REMOTE_MANAGER", "/etc/persistent/scripts/wifi_manager.sh"
)
BSSID_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
REMOTE_COMMANDS = {
    "status": "dashboard-status",
    "scan": "dashboard-scan",
    "connect": "manual-connect-stdin",
    "provision": "provision-stdin",
    "resume": "resume",
}


class UbntWifiError(RuntimeError):
    pass


def run_command(args, timeout, input_text=None):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        input=input_text,
    )


def _int(value, minimum=None, maximum=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if minimum is not None and parsed < minimum:
        return None
    if maximum is not None and parsed > maximum:
        return None
    return parsed


def _decode_hex(value):
    try:
        return bytes.fromhex(value).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise UbntWifiError("UBNT returned an invalid encoded network name") from exc


def parse_snapshot(output, checked_at=None):
    """Parse the manager's credential-free, hex-encoded dashboard records."""
    state = None
    profiles = []
    observations = []
    for raw_line in output.splitlines():
        fields = raw_line.rstrip("\r").split("|")
        if fields[0] == "state" and len(fields) == 6:
            ccq_raw = _int(fields[3], 0, 1000)
            state = {
                "configured_ssid": _decode_hex(fields[1]),
                "associated_ssid": _decode_hex(fields[2]),
                "ccq_percent": round(ccq_raw / 10, 1) if ccq_raw is not None else None,
                "automatic_paused": fields[4] == "yes",
                "selector_running": fields[5] == "yes",
            }
        elif fields[0] == "profile" and len(fields) == 4:
            security = fields[3]
            if security not in ("wpa", "wep", "none"):
                continue
            profiles.append(
                {
                    "name": _decode_hex(fields[1]),
                    "ssid": _decode_hex(fields[2]),
                    "security": security,
                }
            )
        elif fields[0] == "network" and len(fields) == 8:
            quality = _int(fields[1], 0, 100)
            frequency = _int(fields[4], 0)
            channel = _int(fields[5], 0)
            signal = _int(fields[7], -200, 100)
            security = fields[3]
            if quality is None or security not in ("wpa", "wep", "none", "enterprise"):
                continue
            observations.append(
                {
                    "ssid": _decode_hex(fields[2]),
                    "security": security,
                    "quality_percent": quality,
                    "frequency_mhz": frequency,
                    "channel": channel,
                    "bssid": fields[6].upper(),
                    "signal_dbm": signal,
                }
            )
    if state is None:
        raise UbntWifiError("UBNT manager returned no dashboard state")

    profile_map = {}
    for profile in profiles:
        profile_map.setdefault((profile["ssid"], profile["security"]), []).append(
            profile["name"]
        )
    best = {}
    for network in observations:
        key = (network["ssid"], network["security"])
        previous = best.get(key)
        if previous is None or network["quality_percent"] > previous["quality_percent"]:
            best[key] = network
    networks = []
    for key, network in best.items():
        known_profiles = sorted(profile_map.get(key, []), key=str.casefold)
        network = dict(network)
        network["profiles"] = known_profiles
        network["known"] = bool(known_profiles)
        network["connected"] = network["ssid"] == state["associated_ssid"]
        network["supported"] = network["security"] in ("wpa", "none")
        networks.append(network)
    networks.sort(
        key=lambda item: (
            not item["connected"],
            not item["known"],
            -item["quality_percent"],
            item["ssid"].casefold(),
        )
    )
    profiles.sort(key=lambda item: item["name"].casefold())
    return {
        "version": 1,
        "reachable": True,
        "checked_at": int(time.time() if checked_at is None else checked_at),
        "state": state,
        "profiles": profiles,
        "networks": networks,
    }


def _validate_text(value, label, maximum_bytes):
    if not isinstance(value, str):
        raise UbntWifiError(f"{label} must be text")
    size = len(value.encode("utf-8"))
    if size < 1 or size > maximum_bytes:
        raise UbntWifiError(f"{label} must be 1 to {maximum_bytes} bytes")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise UbntWifiError(f"{label} contains a control character")
    return value


class UbntWifiClient:
    def __init__(self, command=run_command, wall_clock=time.time):
        self.command = command
        self.wall_clock = wall_clock

    @staticmethod
    def _ssh_args(remote_command):
        return [
            SSH,
            "-n" if remote_command not in ("manual-connect-stdin", "provision-stdin") else "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            "-i",
            IDENTITY,
            TARGET,
            f"{REMOTE_MANAGER} {remote_command}",
        ]

    def _remote(self, operation, timeout, input_text=None, accepted=(0,)):
        remote_command = REMOTE_COMMANDS[operation]
        try:
            result = self.command(
                self._ssh_args(remote_command),
                timeout=timeout,
                input_text=input_text,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UbntWifiError(f"UBNT {operation} timed out or could not start") from exc
        if result.returncode not in accepted:
            detail = (result.stderr or result.stdout or "command failed").strip()
            raise UbntWifiError(f"UBNT {operation} failed: {detail[-300:]}")
        return result, parse_snapshot(result.stdout, self.wall_clock()) if operation in ("status", "scan") else None

    def status(self):
        return self._remote("status", timeout=15)[1]

    def scan(self):
        return self._remote("scan", timeout=35)[1]

    def connect(self, profile):
        profile = _validate_text(profile, "profile", 128)
        status = self.status()
        if profile not in {item["name"] for item in status["profiles"]}:
            raise UbntWifiError("selected UBNT profile is no longer available")
        result, _ = self._remote(
            "connect", timeout=240, input_text=f"{profile}\n", accepted=(0, 2)
        )
        refreshed = self.status()
        outcome = "connected" if result.returncode == 0 else "associated_no_internet"
        return {
            "outcome": outcome,
            "message": (
                f"Connected UBNT to {profile}"
                if outcome == "connected"
                else f"UBNT associated with {profile}; Internet login may still be required"
            ),
            "wifi": refreshed,
        }

    def provision(self, ssid, security, bssid, password):
        ssid = _validate_text(ssid, "SSID", 32)
        if ssid.startswith(".") or "/" in ssid:
            raise UbntWifiError("SSID cannot be safely stored as a profile filename")
        if security not in ("wpa", "none"):
            raise UbntWifiError("only WPA/WPA2 Personal and open networks are supported")
        if not isinstance(bssid, str) or not BSSID_RE.fullmatch(bssid):
            raise UbntWifiError("invalid access-point address")
        if not isinstance(password, str):
            raise UbntWifiError("password must be text")
        if any(ord(character) < 32 or ord(character) == 127 for character in password):
            raise UbntWifiError("password contains a control character")
        password_size = len(password.encode("utf-8"))
        if security == "wpa" and not 8 <= password_size <= 63:
            raise UbntWifiError("WPA password must be 8 to 63 bytes")
        if security == "none" and password:
            raise UbntWifiError("open networks do not use a password")

        status = self.status()
        if any(
            item["ssid"] == ssid and item["security"] == security
            for item in status["profiles"]
        ):
            raise UbntWifiError("network already has a saved UBNT profile")
        protocol = "\n".join((ssid, security, bssid.upper(), password)) + "\n"
        result, _ = self._remote(
            "provision", timeout=240, input_text=protocol, accepted=(0, 2)
        )
        refreshed = self.status()
        outcome = "connected" if result.returncode == 0 else "associated_no_internet"
        return {
            "outcome": outcome,
            "message": (
                f"Saved and connected UBNT to {ssid}"
                if outcome == "connected"
                else f"Saved {ssid}; Internet login may still be required"
            ),
            "wifi": refreshed,
        }

    def resume(self):
        self._remote("resume", timeout=15)
        return {
            "message": "UBNT automatic selection resumed",
            "wifi": self.status(),
        }


def _read_object():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise UbntWifiError("request input must be one JSON object") from exc
    if not isinstance(payload, dict):
        raise UbntWifiError("request input must be one JSON object")
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", required=True)
    parser.add_argument("action", choices=tuple(REMOTE_COMMANDS))
    args = parser.parse_args(argv)
    client = UbntWifiClient()
    try:
        if args.action == "status":
            result = {"ok": True, "wifi": client.status()}
        elif args.action == "scan":
            result = {"ok": True, "wifi": client.scan(), "message": "UBNT scan complete"}
        elif args.action == "connect":
            payload = _read_object()
            if set(payload) != {"profile"}:
                raise UbntWifiError("connect requires only profile")
            result = {"ok": True, **client.connect(payload["profile"])}
        elif args.action == "provision":
            payload = _read_object()
            expected = {"ssid", "security", "bssid", "password"}
            if set(payload) != expected:
                raise UbntWifiError("provision requires SSID, security, BSSID, and password")
            result = {"ok": True, **client.provision(**payload)}
        else:
            result = {"ok": True, **client.resume()}
    except UbntWifiError as exc:
        print(json.dumps({"ok": False, "message": str(exc)}, separators=(",", ":")))
        return 1
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
