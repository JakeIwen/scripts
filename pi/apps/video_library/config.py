"""Configuration boundary for the video library."""

from __future__ import annotations

if __package__:
    pass
else:
    pass


VIDEO_EXTENSIONS = {
    ".mkv",
    ".avi",
    ".mp4",
    ".m4v",
    ".mov",
    ".webm",
    ".mpg",
    ".mpeg",
    ".ts",
}

CATEGORY_PRIORITY = {"TV": 0, "Movies": 1, "Documentaries": 2, "New": 3}
