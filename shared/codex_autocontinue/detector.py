"""Recognize new inline turn outcomes, never transcript-overlay redraws."""
from dataclasses import dataclass
import re

from .terminal_tokens import TerminalTokens


CAPACITY_PREFIX = "■ Selected model is at capacity."
WORKED_LINE = re.compile(r"^\s*Worked for <?\d+(?:\.\d+)?[smh](?:\s|$)")
ALTERNATE_MODES = frozenset(("47", "1047", "1049"))


@dataclass
class TurnEvents:
    capacity_errors: int = 0
    completed_turns: int = 0


class CapacityDetector:
    """Latch once per submitted turn; require an error or completion line prefix."""

    def __init__(self):
        self.tokens = TerminalTokens()
        self.alternate_modes = set()
        self.line = ""
        self.armed = True

    @property
    def alternate_screen(self):
        return bool(self.alternate_modes)

    def new_turn(self):
        self.line = ""
        self.armed = True

    def user_activity(self):
        # Composer echoes, including wrapped pasted error text, are not backend failures.
        self.line = ""
        self.armed = False

    def feed(self, data):
        events = TurnEvents()
        for kind, value in self.tokens.feed(data):
            if kind == "csi":
                self._control(value)
            elif not self.alternate_screen:
                if kind == "osc" and value == "7501;state=done:app=codex" and self.armed:
                    self.armed = False
                    events.completed_turns += 1
                elif kind == "text":
                    self._text(value, events)
        return events

    def _text(self, char, events):
        if char in "\r\n":
            self.line = ""
        elif char == "\b":
            self.line = self.line[:-1]
        elif char >= " " and char != "\x7f":
            self.line += char
            # Only a line prefix matters; bound memory on unbroken terminal output.
            if len(self.line) > 512:
                self.line = "!" + self.line[-256:]
            if self.armed and self.line.lstrip().startswith(CAPACITY_PREFIX):
                self.armed = False
                events.capacity_errors += 1
            elif self.armed and WORKED_LINE.match(self.line):
                self.armed = False
                events.completed_turns += 1

    def _control(self, sequence):
        final = sequence[-1]
        if sequence.startswith("?") and final in "hl":
            modes = set(sequence[1:-1].split(";")) & ALTERNATE_MODES
            if modes:
                if final == "h":
                    self.alternate_modes.update(modes)
                else:
                    self.alternate_modes.difference_update(modes)
                self.line = ""
        elif final in "HfABCDEFGdJK":
            # Cursor moves/erases separate rendered spans, unlike color/style changes.
            self.line = ""


class SubmissionDetector:
    """Enter submits, except inside a bracketed paste or a terminal reply."""

    def __init__(self):
        self.tokens = TerminalTokens()
        self.pasting = False
        self.text_tail = ""
        self.quotes_capacity = False
        self.submitted_capacity_quote = False

    def feed(self, data):
        submitted = False
        for kind, value in self.tokens.feed(data):
            if kind == "csi" and value in ("200~", "201~"):
                self.pasting = value == "200~"
            elif kind == "text":
                if value == "\r" and not self.pasting:
                    submitted = True
                    self.submitted_capacity_quote = self.quotes_capacity
                    self.text_tail = ""
                    self.quotes_capacity = False
                elif value in ("\x03", "\x15") and not self.pasting:
                    self.text_tail = ""
                    self.quotes_capacity = False
                elif value >= " ":
                    self.text_tail = (self.text_tail + value)[-len(CAPACITY_PREFIX):]
                    self.quotes_capacity |= self.text_tail == CAPACITY_PREFIX
        return submitted
