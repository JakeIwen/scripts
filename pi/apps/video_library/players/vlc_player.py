"""VLC player boundary for the video library."""

from __future__ import annotations

if __package__:
    pass
else:
    pass


class RoomPreparationError(RuntimeError):
    """Raised when rear_movie cannot be completed before playback resumes."""
