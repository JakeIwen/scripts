"""Recognize turn outcomes in inline and full-screen Codex, not old redraws."""
from dataclasses import dataclass
import re

from .terminal_tokens import TerminalTokens


CAPACITY_PREFIX = "■ Selected model is at capacity."
CAPACITY_COMPACT = "".join(CAPACITY_PREFIX.split())
CODEX_STATE = re.compile(r"7501;state=([^:]+):app=codex$")
ERROR_WINDOW_CHARACTERS = 8192
WORKED_LINE = re.compile(r"^\s*Worked for <?\d+(?:\.\d+)?[smh](?:\s|$)")
ALTERNATE_MODES = frozenset(("47", "1047", "1049"))


@dataclass
class TurnEvents:
    capacity_errors: int = 0
    completed_turns: int = 0


class CapacityDetector:
    """Use fresh failure signals, falling back to inline lines on older Codex."""

    def __init__(self):
        self.tokens = TerminalTokens()
        self.alternate_modes = set()
        self.has_state_signals = False
        self.line = ""
        self.error_text = ""
        self.error_window_end = 0
        self.armed = True

    @property
    def alternate_screen(self):
        return bool(self.alternate_modes)

    def new_turn(self):
        self._clear_window()
        self.armed = True

    def user_activity(self):
        # Composer echoes, including wrapped pasted error text, are not backend failures.
        self._clear_window()
        self.armed = False

    def _clear_window(self):
        self.line = ""
        self.error_text = ""
        self.error_window_end = 0

    def feed(self, data):
        events = TurnEvents()
        for kind, value in self.tokens.feed(data):
            if kind == "osc" and self._state(value, events):
                continue
            if self.error_window_end:
                self._error_window(kind, value, events)
            if kind == "csi":
                self._control(value)
            elif kind == "text" and not self.has_state_signals and not self.alternate_screen:
                self._text(value, events)
        return events

    def _state(self, sequence, events):
        match = CODEX_STATE.fullmatch(sequence)
        if not match:
            return False
        # This OSC is Codex's own terminal-integration signal. Without it, retain
        # inline detection; with it, old errors in redraws cannot start new retries.
        self.has_state_signals = True
        self._clear_window()
        if self.armed:
            if match[1] == "error":
                self.error_window_end = self.tokens.characters_seen + ERROR_WINDOW_CHARACTERS
            elif match[1] == "done":
                self.armed = False
                events.completed_turns += 1
        return True

    def _error_window(self, kind, value, events):
        # Bound the search even if the next state OSC never arrives. Include raw
        # controls, even ignored/truncated strings, so every redraw consumes budget.
        if self.tokens.characters_seen > self.error_window_end:
            self._clear_window()
        elif kind == "text" and value.isprintable() and not value.isspace():
            # Ratatui skips unchanged cells (including spaces) with cursor moves.
            # A compact rolling prefix survives these moves and split UTF-8 reads.
            self.error_text = (self.error_text + value)[-len(CAPACITY_COMPACT):]
            if self.error_text == CAPACITY_COMPACT:
                self.armed = False
                self._clear_window()
                events.capacity_errors += 1

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
