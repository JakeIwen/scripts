"""Constants shared by the watcher, the status command and the seek client."""
import os

STATE_DIR_ENV = "MEDIA_SCRUB_STATE_DIR"
SSH_ENV = "MEDIA_SCRUB_SSH"
# Test-only knob: how long the heartbeat may be silent before the watcher exits.
IDLE_ENV = "MEDIA_SCRUB_IDLE_SECONDS"
DEFAULT_STATE_DIR = "~/.local/state/media-scrub"
DEFAULT_SSH = "/usr/bin/ssh"

# vanpi.lan, never bare vanpi: this must not fall back to the metered Tailscale route.
REMOTE_HOST = "pi@vanpi.lan"
REMOTE_COMMAND = "exec /usr/bin/python3 -I /home/pi/scripts/media_position.py serve --interval 0.5"
SSH_OPTIONS = ("-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=2",
               "-o", "ServerAliveInterval=2", "-o", "ServerAliveCountMax=2")

FRESH_STATUS_SECONDS = 3.0     # older than this: "Status unavailable"
SEEKABLE_AGE_SECONDS = 2.0     # older than this: not seekable
HEARTBEAT_IDLE_SECONDS = 4.0
SPAWN_CLAIM_SECONDS = 3.0
WATCHER_MAX_SECONDS = 30 * 60
SEEK_WAIT_SECONDS = 5.0        # the CLI's wait for a result
SEEK_BROKER_TIMEOUT = 4.5      # the watcher answers before the CLI gives up
SSH_CLOSE_WAIT_SECONDS = 2.0
BACKOFF_CAP_SECONDS = 60.0
MAX_LINE_BYTES = 64 * 1024
MAX_BAD_LINES = 20


def idle_seconds():
    try:
        return float(os.environ[IDLE_ENV])
    except (KeyError, ValueError):
        return HEARTBEAT_IDLE_SECONDS
