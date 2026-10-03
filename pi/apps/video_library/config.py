"""Configuration boundary for the video library."""

from __future__ import annotations

import math
import os
import re

if __package__:
    pass
else:
    pass


VIDEO_EXTENSIONS = {
    ".mkv",
    ".avi",
    ".mp4",
    ".m4v",
    ".mov",
    ".webm",
    ".mpg",
    ".mpeg",
    ".ts",
}

CATEGORY_PRIORITY = {"TV": 0, "Movies": 1, "Documentaries": 2, "New": 3}

PORT = int(os.environ.get("VAN_VIDEO_PORT", "8789"))
STATE_PATH = os.path.expanduser(
    os.environ.get(
        "VAN_VIDEO_STATE_PATH",
        "~/.local/share/van-video-library/progress.sqlite3",
    )
)
LEGACY_POSITIONS_PATH = os.path.expanduser(
    os.environ.get("VAN_VIDEO_LEGACY_POSITIONS", "~/vlc-positions.txt")
)
SCAN_INTERVAL = float(os.environ.get("VAN_VIDEO_SCAN_INTERVAL", "900"))
POLL_INTERVAL = float(os.environ.get("VAN_VIDEO_POLL_INTERVAL", "3"))
RESUME_REWIND = float(os.environ.get("VAN_VIDEO_RESUME_REWIND", "12"))
MIN_CONTINUE_POSITION = float(os.environ.get("VAN_VIDEO_MIN_CONTINUE", "30"))
WATCHED_FRACTION = float(os.environ.get("VAN_VIDEO_WATCHED_FRACTION", "0.92"))
VLC_FIXED_VOLUME = float(os.environ.get("VAN_VIDEO_VLC_FIXED_VOLUME", "1.0"))
if not math.isfinite(VLC_FIXED_VOLUME) or not 0 <= VLC_FIXED_VOLUME <= 1.25:
    raise ValueError("VAN_VIDEO_VLC_FIXED_VOLUME must be from 0 to 1.25")

REAR_SONOS_UIDS = tuple(
    value.strip()
    for value in os.environ.get(
        "VAN_VIDEO_REAR_SONOS_UIDS",
        "RINCON_7828CA20F21A01400,RINCON_7828CA20F1DA01400",
    ).split(",")
    if value.strip()
)
SONOS_DISCOVERY_TTL = float(os.environ.get("VAN_VIDEO_SONOS_DISCOVERY_TTL", "10"))
ROOM_PREPARE_TIMEOUT = float(
    os.environ.get("VAN_VIDEO_ROOM_PREPARE_TIMEOUT", "45")
)
if not math.isfinite(ROOM_PREPARE_TIMEOUT) or ROOM_PREPARE_TIMEOUT <= 0:
    raise ValueError("VAN_VIDEO_ROOM_PREPARE_TIMEOUT must be positive")

DISPLAY = os.environ.get("VAN_VIDEO_DISPLAY", ":0")
RUNTIME_DIR = os.environ.get("VAN_VIDEO_XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
SESSION_BUS = os.environ.get(
    "VAN_VIDEO_SESSION_BUS", f"unix:path={RUNTIME_DIR}/bus"
)
PLAYER_UNIT = os.environ.get("VAN_VIDEO_PLAYER_UNIT", "van-video-player.service")

VLC = os.environ.get("VAN_VIDEO_VLC", "/usr/bin/vlc")
SYSTEMD_RUN = os.environ.get("VAN_VIDEO_SYSTEMD_RUN", "/usr/bin/systemd-run")
SYSTEMCTL = os.environ.get("VAN_VIDEO_SYSTEMCTL", "/usr/bin/systemctl")
XSET = os.environ.get("VAN_VIDEO_XSET", "/usr/bin/xset")
SNS = os.environ.get("VAN_VIDEO_SONOS_SETUP", "/home/pi/sns.sh")
MKVMERGE = os.environ.get("VAN_VIDEO_MKVMERGE", "/usr/bin/mkvmerge")
PKILL = os.environ.get("VAN_VIDEO_PKILL", "/usr/bin/pkill")

QBITTORRENT_URL = os.environ.get(
    "VAN_VIDEO_QBITTORRENT_URL", "http://127.0.0.1:8080"
)
QBITTORRENT_CLIENT_ID = os.environ.get("VAN_VIDEO_QBITTORRENT_CLIENT_ID", "vanpi")
QBITTORRENT_TIMEOUT = float(os.environ.get("VAN_VIDEO_QBITTORRENT_TIMEOUT", "3"))
QBITTORRENT_TEMP_ROOTS = tuple(
    path
    for path in os.environ.get(
        "VAN_VIDEO_QBITTORRENT_TEMP_ROOTS",
        os.pathsep.join(
            (
                "/mnt/movingparts/torrent/incomplete",
                "/mnt/bigboi/mp_backup/torrent/incomplete",
            )
        ),
    ).split(os.pathsep)
    if path
)
QBITTORRENT_FINAL_ROOTS = tuple(
    path
    for path in os.environ.get(
        "VAN_VIDEO_QBITTORRENT_FINAL_ROOTS",
        os.pathsep.join(
            (
                "/mnt/movingparts/torrent",
                "/mnt/bigboi/mp_backup/torrent",
            )
        ),
    ).split(os.pathsep)
    if path
)


LEGACY_LINE_RE = re.compile(
    r"^(?P<rel>/.*?)\s+(?P<micros>\d+)\s+\d{1,3}:\d{2}:\d{2}\s*$"
)

MPRIS_NAME = "org.mpris.MediaPlayer2.vlc"
MPRIS_PATH = "/org/mpris/MediaPlayer2"
MPRIS_ROOT = "org.mpris.MediaPlayer2"
MPRIS_PLAYER = "org.mpris.MediaPlayer2.Player"
DBUS_PROPERTIES = "org.freedesktop.DBus.Properties"

DEFAULT_FAVORITES = (
    ("The Simpsons", False),
    ("South Park", False),
    ("Rick and Morty", True),
    ("Metalocalypse", True),
    ("Gospel", True),
)

_UNSET = object()
