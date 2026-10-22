"""Bounded native commands; writes use defaults so cfprefsd sees each change."""
from datetime import datetime
from pathlib import Path
import os
import plistlib
import subprocess
import uuid

from .model import APP_PATH, DOMAIN, ControlError, Session


class NativeApp:
    def __init__(self, home=None):
        home = Path(home) if home is not None else Path.home()
        self.domain = home / 'Library/Containers' / DOMAIN / 'Data/Library/Preferences' / DOMAIN
        self.state_dir = home / '.local/state/amphetamine-control'

    def command(self, argv, timeout=12, allowed=(0,)):
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise ControlError('Amphetamine did not respond within {} seconds. Check for a '
                               'macOS Automation permission prompt or an open Amphetamine dialog.'.format(timeout)) from exc
        except OSError as exc:
            raise ControlError(str(exc)) from exc
        if result.returncode not in allowed:
            raise ControlError((result.stderr or result.stdout).strip() or 'Native command failed.')
        return result.stdout.strip()

    def script(self, body):
        return self.command(('/usr/bin/osascript', '-e', body))

    def read_preferences(self):
        # A background defaults client can export an empty domain even while the
        # container plist exists. Read persisted values instead of inventing OFF.
        try:
            raw = self.domain.parent / (self.domain.name + '.plist')
            values = plistlib.loads(raw.read_bytes())
        except OSError as exc:
            raise ControlError('Cannot read Amphetamine preferences: {}'.format(exc)) from exc
        except (ValueError, plistlib.InvalidFileException) as exc:
            raise ControlError('Amphetamine preferences could not be read.') from exc
        if not isinstance(values, dict):
            raise ControlError('Amphetamine preferences are not a dictionary.')
        return values

    def write_preferences(self, changes):
        for key, value in changes.items():
            if value is None:
                current = self.read_preferences()
                if key in current:
                    self.command(('/usr/bin/defaults', 'delete', str(self.domain), key))
            else:
                kind = '-bool' if isinstance(value, bool) else '-int'
                text = str(value).lower()
                self.command(('/usr/bin/defaults', 'write', str(self.domain), key, kind, text))

    def backup(self, values):
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state_dir.chmod(0o700)
        name = '{}-{}.plist'.format(datetime.now().strftime('%Y%m%d-%H%M%S'), uuid.uuid4().hex[:8])
        descriptor = os.open(str(self.state_dir / name), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            plistlib.dump(values, stream)

    def session(self):
        # AppleScript's `running` property can silently say false in a sandbox.
        # Require a working process-list boundary before reporting a stopped app.
        if not self.command(('/usr/bin/pgrep', '-x', 'Amphetamine'), allowed=(0, 1)):
            return Session()
        raw = self.script('''if application "APP" is not running then return "stopped"
tell application "APP"
return {(session is active), (session time remaining), (display sleep allowed), (screen saver allowed), (closed display mode enabled)}
end tell'''.replace('APP', APP_PATH))
        if raw == 'stopped':
            return Session()
        parts = [part.strip() for part in raw.split(',')]
        if len(parts) != 5 or any(parts[i] not in ('true', 'false') for i in (0, 2, 3, 4)):
            raise ControlError('Amphetamine returned an unrecognized session state.')
        try:
            remaining = int(parts[1])
        except ValueError as exc:
            raise ControlError('Amphetamine returned an invalid session duration.') from exc
        return Session(True, parts[0] == 'true', remaining, parts[2] == 'true',
                       parts[3] == 'true', parts[4] == 'true')

    def quit(self):
        self.script('''if application "APP" is running then
tell application "APP" to quit
repeat 40 times
if application "APP" is not running then return
delay 0.1
end repeat
error "Amphetamine did not quit; no preferences were written."
end if'''.replace('APP', APP_PATH))

    def resume(self, previous, start=False):
        if not previous.running and not start:
            return
        self.command(('/usr/bin/open', '-g', '-a', APP_PATH))
        if not previous.active and not start:
            # Launch-at-start preferences must not turn disabling auto-lock into
            # a new manual keep-awake session.
            self.script('tell application "{}" to end session'.format(APP_PATH))
            return
        if previous.active or start:
            display = str(previous.display_sleep).lower()
            options = '{duration:0, interval:0'
            if previous.active:
                options += ', displaySleepAllowed:' + display
            self.script('tell application "{}" to start new session with options {}'.format(
                APP_PATH, options + '}'))
            if previous.active:
                verb = 'allow' if previous.screen_saver else 'prevent'
                self.script('tell application "{}" to {} screen saver'.format(APP_PATH, verb))
