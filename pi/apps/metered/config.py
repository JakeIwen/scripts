"""Installed paths and reconciliation budgets; no network policy identities."""
from dataclasses import dataclass
import os
from pathlib import Path


TICK_SECONDS = 10.0
PROBE_TIMEOUT = 35.0
UPLINK_HELPER = '/home/pi/scripts/backup/icloud_uplink.py'
POLICYCTL = '/home/pi/scripts/policyctl'
INTERNET_SWITCHES = '/home/pi/scripts/internet_switches.sh'
POLICY_START = ('/usr/bin/sudo', '-n', '/usr/bin/systemctl', '--no-block', 'start', 'vanpi-policy.service')
IGNITION_FLAG = Path('/home/pi/hooks/ignition_is_on')
LIFECYCLE_LOCK = Path('/home/pi/.internet_switches.lock')
TORRENT_PROCESS = 'qbittorrent-nox'
TORRENT_API = 'http://127.0.0.1:8080'
TORRENT_TIMEOUT = 3.0
HOLD_TAG = 'metered-hold'


@dataclass(frozen=True)
class Paths:
    shared: Path
    policy: Path
    state: Path
    mac_report: Path

    @classmethod
    def default(cls):
        home = Path(os.environ.get('VANPI_METERED_HOME', str(Path.home())))
        return cls(home / '.config/vanpi/metered.json',
                   home / '.config/vanpi/metered-policy.json',
                   home / '.local/state/vanpi-metered/state.json',
                   home / '.local/state/vanpi-metered/mac-report.json')
