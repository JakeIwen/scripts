"""Paths and deadlines for the login-session, one-shot reconciler."""
from dataclasses import dataclass
from pathlib import Path

INTERVAL = 60
STATE_TTL = 75.0
REMOTE_INTERVAL = 900.0
SSH_TIMEOUT = 20.0
REMOTE_CLI = '/home/pi/scripts/vanpi-metered'
SETTINGS_TOOL = 'metered-mac'
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
