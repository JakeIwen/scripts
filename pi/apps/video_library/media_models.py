"""Shared media models and presentation helpers for the video library."""

from __future__ import annotations


from dataclasses import dataclass, field
from typing import Any


def seconds_text(seconds: float | int | None) -> str:
    total = max(0, int(seconds or 0))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


@dataclass
class MediaItem:
    key: str
    id: str
    title: str
    path: str
    real_path: str
    rel_path: str
    media_type: str
    source: str
    series_kind: str | None = None
    year: int | None = None
    series: str | None = None
    season: int | None = None
    episode: int | None = None
    episode_title: str | None = None
    mtime: float = 0.0
    categories: set[str] = field(default_factory=set)
    aliases: set[str] = field(default_factory=set)
    rank: tuple[Any, ...] = field(default_factory=tuple)
    asset_id: str | None = None
    work_id: str | None = None

    @property
    def episode_code(self) -> str | None:
        if self.season is None or self.episode is None:
            return None
        return f"S{self.season:02d}E{self.episode:02d}"

    @property
    def new(self) -> bool:
        return "New" in self.categories

    def as_dict(self, progress: dict[str, Any] | None = None) -> dict[str, Any]:
        value = {
            "id": self.id,
            "title": self.title,
            "type": self.media_type,
            "year": self.year,
            "series": self.series,
            "season": self.season,
            "episode": self.episode,
            "episode_code": self.episode_code,
            "episode_title": self.episode_title,
            "new": self.new,
            "categories": sorted(self.categories),
            "source": self.source,
            "series_kind": self.series_kind,
            "updated": int(self.mtime),
        }
        if progress:
            value["progress"] = public_progress(progress)
        return value


@dataclass(frozen=True)
class LibrarySource:
    name: str
    mount_path: str
    index_path: str


@dataclass
class Show:
    key: str
    id: str
    name: str
    kind: str
    episodes: list[MediaItem]

    @property
    def new(self) -> bool:
        return any(item.new for item in self.episodes)


def public_progress(value: dict[str, Any]) -> dict[str, Any]:
    position = float(value.get("position") or 0)
    duration = float(value.get("duration") or 0)
    return {
        "position": position,
        "position_text": seconds_text(position),
        "duration": duration,
        "duration_text": seconds_text(duration) if duration else None,
        "fraction": min(1.0, position / duration) if duration else None,
        "updated": int(value.get("updated") or 0),
        "finished": bool(value.get("finished")),
        "play_count": int(value.get("play_count") or 0),
    }
