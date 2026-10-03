"""Sonos volume boundary for the video library."""

from __future__ import annotations

if __package__:
    pass
else:
    pass


class AudioPreparingError(RuntimeError):
    """Raised when rear-room setup would overwrite a Sonos volume change."""
