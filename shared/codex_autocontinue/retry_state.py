"""Clock-in, actions-out retry policy, independent of terminal and process I/O."""
from dataclasses import dataclass
import math
from typing import Optional

from .backoff import delay


@dataclass
class RetryState:
    attempts: int = 0
    streak_started: Optional[float] = None
    due: Optional[float] = None
    enter_due: Optional[float] = None
    gave_up: bool = False
    bell_pending: bool = False

    def _expired(self, now, settings):
        return (self.streak_started is not None
                and now - self.streak_started >= settings["streak_max_minutes"] * 60)

    def _give_up(self):
        self.due = None
        self.enter_due = None
        self.bell_pending = not self.gave_up
        self.gave_up = True

    def capacity_error(self, now, settings, rng):
        if not settings["enabled"]:
            return
        if self.streak_started is None:
            self.streak_started = now
        if self.due is not None or self.enter_due is not None:
            return
        if self.attempts >= settings["max_attempts"] or self._expired(now, settings):
            self._give_up()
        else:
            self.gave_up = False
            self.due = now + delay(self.attempts + 1, settings, rng)

    def completed_turn(self):
        self.cancel()
        self.attempts = 0
        self.streak_started = None

    def cancel(self):
        self.due = None
        self.enter_due = None
        self.gave_up = False
        self.bell_pending = False

    def tick(self, now, settings, alternate_screen=False):
        if not settings["enabled"] or alternate_screen:
            self.cancel()
            return b""
        pending = self.due is not None or self.enter_due is not None
        if pending and (self._expired(now, settings) or self.attempts >= settings["max_attempts"]):
            self._give_up()
            return b""
        if self.enter_due is not None and now >= self.enter_due:
            self.enter_due = None
            self.attempts += 1
            return b"\r"
        if self.due is not None and now >= self.due:
            self.due = None
            self.enter_due = now + settings["submit_pause_seconds"]
            return b"continue"
        return b""

    def title(self, now, settings):
        if self.gave_up:
            return "codex: gave up after {}".format(self.attempts)
        if self.due is not None:
            return "codex: retry {}/{} in {}s".format(
                self.attempts + 1, settings["max_attempts"],
                max(0, math.ceil(self.due - now)))
        if self.enter_due is not None:
            return "codex: retry {}/{}".format(self.attempts + 1, settings["max_attempts"])
        return ""
