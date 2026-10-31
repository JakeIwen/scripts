"""Native preference names, session records and configuration validation."""
from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Optional, TypedDict


APP_PATH = '/Applications/Amphetamine.app'
DOMAIN = 'com.if.Amphetamine'
ENABLED = 'Lock Screen After Inactivity'
DELAY = 'Lock Screen Inactivity Delay'
DISPLAY_SLEEP = 'Allow Display Sleep When Screen Is Locked'
CONTROLLED_KEYS = (ENABLED, DELAY, DISPLAY_SLEEP)
DEFAULT_MINUTES = 20
MIN_MINUTES = 1
MAX_MINUTES = 240


class ControlError(RuntimeError):
    """The operation could not be completed safely."""


class Conflict(ControlError):
    """Refresh or finish the current session before trying again."""


class PermissionDenied(ControlError):
    """macOS privacy settings refused access; only the user can grant it."""


@dataclass(frozen=True)
class Session:
    running: bool = False
    active: bool = False
    remaining: int = -3
    display_sleep: bool = False
    screen_saver: bool = False
    closed_display: bool = False

    def ensure_restartable(self):
        if self.active and (self.remaining != 0 or self.closed_display):
            raise Conflict('Finish the timed, triggered, app-based or closed-display session '
                           'in Amphetamine before changing its lock settings.')


class Status(TypedDict):
    enabled: bool
    delay_seconds: int
    display_sleep_locked: bool
    low_battery_enabled: bool
    low_battery_percent: int
    revision: str
    session: Optional[dict]
    error: Optional[str]
    minimum_minutes: int
    maximum_minutes: int


def revision(values):
    # Only these keys conflict; Amphetamine also writes running counters frequently.
    selected = [(key, values.get(key)) for key in CONTROLLED_KEYS]
    return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()


def validate_change(enabled, minutes, expected_revision):
    if type(enabled) is not bool or type(minutes) is not int:
        raise ControlError('Enabled must be a boolean and minutes must be an integer.')
    if not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise ControlError('Lock delay must be between {} and {} minutes.'.format(MIN_MINUTES, MAX_MINUTES))
    if (not isinstance(expected_revision, str) or len(expected_revision) != 64
            or any(char not in '0123456789abcdef' for char in expected_revision)):
        raise ControlError('Refresh the current settings before saving.')


def status_record(values, session=None, error=None) -> Status:
    return Status(enabled=bool(values.get(ENABLED, False)),
                  delay_seconds=int(values.get(DELAY, DEFAULT_MINUTES * 60)),
                  display_sleep_locked=bool(values.get(DISPLAY_SLEEP, False)),
                  low_battery_enabled=bool(values.get('End Session On Low Battery', False)),
                  low_battery_percent=int(values.get('Low Battery Percent', 10)),
                  revision=revision(values), session=asdict(session) if session is not None else None,
                  error=error, minimum_minutes=MIN_MINUTES, maximum_minutes=MAX_MINUTES)
