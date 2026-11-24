"""Paths and deadlines for the login-session, one-shot reconciler."""
from dataclasses import dataclass
from pathlib import Path

INTERVAL = 15
STATE_TTL = 75.0
# Tolerate Pi evidence age plus late launchd ticks; fresh evidence always wins.
HOME_GRACE = 90.0
REMOTE_INTERVAL = 900.0
SSH_TIMEOUT = 20.0
REMOTE_CLI = '/home/pi/scripts/vanpi-metered'
SETTINGS_TOOL = 'metered-mac'
PI_HOST = 'vanpi'
# Private mux socket, so the 15 s sync reuses one login instead of opening a
# new one per tick. %C hashes the resolved HostName, so the LAN and Tailscale
# routes of vanpi get different sockets. macOS caps socket paths at 104 bytes:
# home + this + ssh's 17-character temporary suffix stays near 90.
CONTROL_PATH = '~/.ssh/cm-metered-%C'
CONTROL_PERSIST = 120
ALIVE_INTERVAL = 5
ALIVE_COUNT_MAX = 2
TEARDOWN_TIMEOUT = 2.0
LABEL = 'com.jacobr.metered'


@dataclass(frozen=True)
class Paths:
    directory: Path
    policy: Path

    @classmethod
    def default(cls):
        return cls(Path.home() / '.local/state/metered',
                   Path.home() / '.config/jacobr/metered.json')

    def file(self, name):
        return self.directory / (name + '.json')
