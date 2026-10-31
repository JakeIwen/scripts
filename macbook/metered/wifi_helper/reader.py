"""Fresh Wi-Fi identities through LaunchServices, never a cached or CLI-redacted SSID.

Public boundary: read_wifi(). Run `python3 -m macbook.metered.wifi_helper.reader`
from the checkout for a one-shot JSON diagnostic. Build with the sibling build.zsh.
Only an explicit app launch with --request-authorization requests Location access;
background reads never prompt. A GUI login session is required, even for launchd.
"""
from enum import Enum
import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Optional, TypedDict


class WiFiStatus(str, Enum):
    OK = "ok"
    NOT_AUTHORIZED = "not_authorized"
    NO_WIFI = "no_wifi"
    ERROR = "error"


class WiFiReading(TypedDict):
    interface: Optional[str]
    ssid: Optional[str]
    bssid: Optional[str]
    status: str
    detail: str


APP_NAME = "MeteredWiFi.app"
OPEN = "/usr/bin/open"
MAX_OUTPUT_BYTES = 16384
BSSID = re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\Z")


def _empty(status: WiFiStatus, detail: str, interface: Optional[str] = None) -> WiFiReading:
    return {"interface": interface, "ssid": None, "bssid": None,
            "status": status.value, "detail": detail}


def _parse(value: object) -> WiFiReading:
    if not isinstance(value, dict):
        return _empty(WiFiStatus.ERROR, "invalid_helper_record")
    try:
        status = WiFiStatus(value.get("status"))
    except (TypeError, ValueError):
        return _empty(WiFiStatus.ERROR, "invalid_helper_status")
    interface = value.get("interface")
    detail = value.get("detail")
    ssid, bssid = value.get("ssid"), value.get("bssid")
    if (not isinstance(detail, str) or not detail
            or (interface is not None and (not isinstance(interface, str) or not interface))
            or any(item is not None and not isinstance(item, str) for item in (ssid, bssid))):
        return _empty(WiFiStatus.ERROR, "invalid_helper_fields")
    if status != WiFiStatus.OK:
        return _empty(status, detail, interface)
    if any(isinstance(item, str) and item.lower() == "<redacted>" for item in (ssid, bssid)):
        return _empty(WiFiStatus.NOT_AUTHORIZED, "redacted_helper_identity", interface)
    if not interface or not ssid or not bssid or not BSSID.fullmatch(bssid):
        return _empty(WiFiStatus.ERROR, "incomplete_helper_identity", interface)
    if bssid.lower() == "00:00:00:00:00:00":
        return _empty(WiFiStatus.NOT_AUTHORIZED, "redacted_helper_identity", interface)
    return {"interface": interface, "ssid": ssid, "bssid": bssid,
            "status": status.value, "detail": detail}


def read_wifi(*, app_path: Optional[Path] = None, timeout: float = 10.0) -> WiFiReading:
    """Return {interface, ssid, bssid, status, detail}; only status=ok identifies Wi-Fi.

    Each invocation opens a fresh app process with its own private output file.
    LaunchServices, rather than a directly exec'd binary, owns TCC attribution.
    `app_path` is an install/test override. No authorization is inferred from a
    missing SSID, subprocess success, or an earlier reading. An unavailable or
    unassociated powered-on interface is conservatively error, not unmetered.
    """
    app = Path(app_path) if app_path is not None else Path.home() / "Applications" / APP_NAME
    if not (app / "Contents/MacOS/MeteredWiFi").is_file():
        return _empty(WiFiStatus.ERROR, "helper_not_built")
    try:
        with tempfile.TemporaryDirectory(prefix="metered-wifi-") as directory:
            output = Path(directory) / "wifi.json"
            command = [OPEN, "-g", "-j", "-n", "-W", str(app), "--args", "--output", str(output)]
            subprocess.run(command, check=True, capture_output=True, timeout=timeout)
            with output.open("rb") as source:
                data = source.read(MAX_OUTPUT_BYTES + 1)
            if len(data) > MAX_OUTPUT_BYTES:
                return _empty(WiFiStatus.ERROR, "helper_output_too_large")
            return _parse(json.loads(data))
    except subprocess.TimeoutExpired:
        return _empty(WiFiStatus.ERROR, "helper_timeout")
    except subprocess.CalledProcessError:
        return _empty(WiFiStatus.ERROR, "helper_launch_failed")
    except (ValueError, OSError) as error:
        return _empty(WiFiStatus.ERROR, "helper_output_unavailable:" + type(error).__name__)


if __name__ == "__main__":
    print(json.dumps(read_wifi(), sort_keys=True, ensure_ascii=False))
