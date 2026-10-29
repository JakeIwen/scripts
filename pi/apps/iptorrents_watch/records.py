"""Wire records shared by the CLI and the dashboard (plain JSON round trips).

The dashboard's React feature mirrors these shapes in its schema; change both.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SOURCE_LIBRARY = "library"   # derived from the video library's playback history
SOURCE_MANUAL = "manual"     # added by hand in the panel or CLI
SOURCES = (SOURCE_LIBRARY, SOURCE_MANUAL)

CANDIDATE_NEW = "new"
CANDIDATE_DOWNLOADED = "downloaded"   # .torrent written to the watch folder
CANDIDATE_DISMISSED = "dismissed"
CANDIDATE_STATUSES = (CANDIDATE_NEW, CANDIDATE_DOWNLOADED, CANDIDATE_DISMISSED)

LOGIN_OK = "ok"
LOGIN_NEEDED = "needed"       # cookies missing, expired or rejected
LOGIN_UNKNOWN = "unknown"     # no check yet, or the site was unreachable
LOGIN_STATES = (LOGIN_OK, LOGIN_NEEDED, LOGIN_UNKNOWN)


@dataclass
class WantedShow:
    name: str                      # search name, e.g. "Silo"
    source: str                    # one of SOURCES
    library_show_id: str | None    # the video library's show id when derived
    last_watched: int | None       # epoch seconds from the library, None for manual
    have_season: int | None        # newest season on disk (library or manual)
    have_episode: int | None       # newest episode of that season on disk
    added_at: str                  # ISO 8601 UTC
    paused: bool = False           # keep on the list but skip checks

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WantedShow":
        return cls(**data)


@dataclass
class Candidate:
    torrent_id: str                # site id from /t/<id>
    name: str
    show: str                      # WantedShow.name it answers
    season: int | None
    episode: int | None            # None for a season pack
    size_bytes: int
    seeders: int
    leechers: int
    snatches: int
    freeleech: bool
    age_text: str                  # the site's "3.1 minutes ago" text as shown
    category: str                  # the site's category label, e.g. "TV/x264"
    download_path: str             # site-relative /download.php/<id>/<name>.torrent
    first_seen: str                # ISO 8601 UTC
    status: str = CANDIDATE_NEW    # one of CANDIDATE_STATUSES
    downloaded_at: str | None = None
    file_path: str | None = None   # the .torrent written to TFILES_DIR
    error: str | None = None       # last download failure, if any

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Candidate":
        return cls(**data)


@dataclass
class Status:
    """What the panel shows at a glance (``status --json``)."""

    login: str                     # one of LOGIN_STATES
    last_check: str | None         # ISO 8601 UTC of the last completed check
    last_error: str | None
    checking: bool                 # a check is running now (pid alive)
    wanted_count: int
    new_candidate_count: int
    tfiles_writable: bool
    library_available: bool
    wanted: list[dict[str, Any]] = field(default_factory=list)      # WantedShow.to_dict()
    candidates: list[dict[str, Any]] = field(default_factory=list)  # Candidate.to_dict(), new first, newest first

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
