"""Media library boundary for the video library."""

from __future__ import annotations

import os
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable

if __package__:
    from .config import CATEGORY_PRIORITY, DEFAULT_FAVORITES, MIN_CONTINUE_POSITION, VIDEO_EXTENSIONS
    from .media_models import LibrarySource, MediaItem, Show
    from .naming import canonical_series, natural_key, normalized, parse_candidate, stable_id
else:
    from config import (  # type: ignore[no-redef]
        CATEGORY_PRIORITY,
        DEFAULT_FAVORITES,
        MIN_CONTINUE_POSITION,
        VIDEO_EXTENSIONS,
    )
    from media_models import LibrarySource, MediaItem, Show  # type: ignore[no-redef]
    from naming import (  # type: ignore[no-redef]
        canonical_series,
        natural_key,
        normalized,
        parse_candidate,
        stable_id,
    )


class MediaLibrary:
    def __init__(
        self,
        sources: Iterable[LibrarySource],
        *,
        require_mount: bool = True,
        mount_check: Callable[[str], bool] = os.path.ismount,
    ):
        self.sources = tuple(sources)
        self.require_mount = require_mount
        self.mount_check = mount_check
        self.lock = threading.RLock()
        self.items: dict[str, MediaItem] = {}
        self.items_by_key: dict[str, MediaItem] = {}
        self.items_by_path: dict[str, MediaItem] = {}
        self.items_by_rel: dict[str, MediaItem] = {}
        self.shows: dict[str, Show] = {}
        self.available = False
        self.source: LibrarySource | None = None
        self.error: str | None = "library has not been scanned"
        self.last_scan = 0.0

    def _available_source(self) -> LibrarySource | None:
        for source in self.sources:
            if self.require_mount and not self.mount_check(source.mount_path):
                continue
            if os.path.isdir(source.index_path):
                return source
        return None

    @staticmethod
    def _inside(path: str, parent: str) -> bool:
        try:
            return os.path.commonpath((os.path.realpath(path), os.path.realpath(parent))) == os.path.realpath(parent)
        except ValueError:
            return False

    @staticmethod
    def _lexically_inside(path: str, parent: str) -> bool:
        try:
            return os.path.commonpath((os.path.abspath(path), os.path.abspath(parent))) == os.path.abspath(parent)
        except ValueError:
            return False

    def scan(self) -> bool:
        source = self._available_source()
        if not source:
            with self.lock:
                self.available = False
                self.source = None
                self.error = "media drive is not mounted or its links index is unavailable"
                self.last_scan = time.time()
            return False

        found: dict[str, MediaItem] = {}
        found_by_target: dict[str, MediaItem] = {}
        for category in CATEGORY_PRIORITY:
            category_path = os.path.join(source.index_path, category)
            if not os.path.isdir(category_path):
                continue
            for root, dirs, files in os.walk(category_path, followlinks=False):
                dirs[:] = sorted((name for name in dirs if not name.startswith(".")), key=natural_key)
                for filename in sorted(files, key=natural_key):
                    if filename.startswith("."):
                        continue
                    link_path = os.path.join(root, filename)
                    if not os.path.islink(link_path):
                        continue
                    real_path = os.path.realpath(link_path)
                    if not self._inside(real_path, source.mount_path):
                        continue
                    if not os.path.isfile(real_path) or Path(real_path).suffix.casefold() not in VIDEO_EXTENSIONS:
                        continue
                    relative = os.path.relpath(link_path, source.index_path)
                    candidate = parse_candidate(
                        category,
                        relative,
                        link_path,
                        real_path,
                        source.name,
                        library_root=source.mount_path,
                    )
                    existing = found_by_target.get(real_path) or found.get(candidate.key)
                    if existing:
                        existing.categories.update(candidate.categories)
                        existing.aliases.update(candidate.aliases)
                        existing.mtime = max(existing.mtime, candidate.mtime)
                        if candidate.rank < existing.rank:
                            categories, aliases, mtime = existing.categories, existing.aliases, existing.mtime
                            found.pop(existing.key, None)
                            found[candidate.key] = candidate
                            candidate.categories = categories
                            candidate.aliases = aliases
                            candidate.mtime = mtime
                            for target, value in tuple(found_by_target.items()):
                                if value is existing:
                                    found_by_target[target] = candidate
                            found_by_target[real_path] = candidate
                        else:
                            found_by_target[real_path] = existing
                    else:
                        found[candidate.key] = candidate
                        found_by_target[real_path] = candidate

        shows_by_name: dict[tuple[str, str], list[MediaItem]] = {}
        for item in found.values():
            if item.media_type == "episode" and item.series:
                identity = (item.series_kind or "tv", canonical_series(item.series))
                shows_by_name.setdefault(identity, []).append(item)
        shows = {}
        for (show_kind, show_name_key), episodes in shows_by_name.items():
            episodes.sort(
                key=lambda item: (
                    item.season if item.season is not None else 9999,
                    item.episode if item.episode is not None else 9999,
                    natural_key(item.rel_path),
                )
            )
            key = f"show:{show_kind}:{show_name_key}"
            show = Show(
                key=key,
                id=stable_id(key),
                name=episodes[0].series or show_name_key,
                kind=show_kind,
                episodes=episodes,
            )
            shows[show.id] = show

        by_path: dict[str, MediaItem] = {}
        by_rel: dict[str, MediaItem] = {}
        by_id: dict[str, MediaItem] = {}
        for item in found.values():
            by_id[item.id] = item
            by_path[os.path.abspath(item.path)] = item
            by_path[os.path.realpath(item.path)] = item
            for alias in item.aliases:
                by_rel[alias] = item
                by_path[os.path.abspath(os.path.join(source.index_path, alias.lstrip("/")))] = item
        for target, item in found_by_target.items():
            by_path[os.path.realpath(target)] = item

        with self.lock:
            self.items = by_id
            self.items_by_key = found
            self.items_by_path = by_path
            self.items_by_rel = by_rel
            self.shows = shows
            self.available = True
            self.source = source
            self.error = None
            self.last_scan = time.time()
        return True

    def item_for_path(self, path: str | None) -> MediaItem | None:
        if not path:
            return None
        with self.lock:
            return self.items_by_path.get(os.path.abspath(path)) or self.items_by_path.get(os.path.realpath(path))

    def item_for_rel(self, rel_path: str) -> MediaItem | None:
        normalized_rel = "/" + rel_path.lstrip("/")
        with self.lock:
            return self.items_by_rel.get(normalized_rel)

    def resolve_for_play(self, item: MediaItem) -> str:
        """Revalidate an indexed link and return a symlink-independent target."""
        with self.lock:
            source = self.source
            if not self.available or source is None or item.source != source.name:
                raise RuntimeError(self.error or "media library unavailable")
            try:
                mounted = not self.require_mount or self.mount_check(source.mount_path)
            except Exception:
                mounted = False
            if not mounted or not os.path.isdir(source.index_path):
                raise RuntimeError("media drive is no longer mounted")
            if not self._lexically_inside(item.path, source.index_path):
                raise RuntimeError(f"media link escaped the active index: {item.title}")
            if not os.path.islink(item.path):
                raise RuntimeError(f"media disappeared from the index: {item.title}")
            real_path = os.path.realpath(item.path)
            if (
                not self._inside(real_path, source.mount_path)
                or not os.path.isfile(real_path)
                or Path(real_path).suffix.casefold() not in VIDEO_EXTENSIONS
            ):
                raise RuntimeError(f"media target is no longer safe to play: {item.title}")
            return real_path

    def snapshot(self) -> tuple[list[MediaItem], list[Show]]:
        with self.lock:
            return list(self.items.values()), list(self.shows.values())


class LibraryViewMixin:
    def show_for_item(self, item: MediaItem) -> Show | None:
        if not item.series:
            return None
        show_id = stable_id(
            f"show:{item.series_kind or 'tv'}:{canonical_series(item.series)}"
        )
        return self.library.shows.get(show_id)

    def _next_episode(self, show: Show, progress: dict[str, dict[str, Any]]) -> MediaItem:
        unfinished = [
            item
            for item in show.episodes
            if (progress.get(item.key) or {}).get("position", 0) >= MIN_CONTINUE_POSITION
            and not (progress.get(item.key) or {}).get("finished")
        ]
        if unfinished:
            return max(unfinished, key=lambda item: progress[item.key].get("updated", 0))
        for item in show.episodes:
            record = progress.get(item.key)
            if not record or not record.get("finished"):
                return item
        return show.episodes[0]

    def _show_dict(self, show: Show, progress: dict[str, dict[str, Any]]) -> dict[str, Any]:
        next_item = self._next_episode(show, progress)
        watched = sum(bool((progress.get(item.key) or {}).get("finished")) for item in show.episodes)
        latest = max((progress.get(item.key, {}).get("updated", 0) for item in show.episodes), default=0)
        return {
            "id": show.id,
            "name": show.name,
            "type": "show",
            "kind": show.kind,
            "episodes": len(show.episodes),
            "watched": watched,
            "new": show.new,
            "last_watched": int(latest),
            "next": next_item.as_dict(progress.get(next_item.key)),
        }

    def _favorite_specs(self, shows: list[Show]) -> list[dict[str, Any]]:
        by_name = {
            canonical_series(show.name): show for show in shows if show.kind == "tv"
        }
        result = []
        for name, no_subtitles in DEFAULT_FAVORITES:
            wanted = canonical_series(name)
            show = by_name.get(wanted)
            if not show:
                show = next((candidate for key, candidate in by_name.items() if wanted in key or key in wanted), None)
            if show:
                result.append(
                    {
                        "id": show.id,
                        "name": show.name,
                        "no_subtitles": no_subtitles,
                    }
                )
        return result

    def library_payload(self) -> dict[str, Any]:
        items, shows = self.library.snapshot()
        progress = self._progress_all()
        continuing = [
            item
            for item in items
            if (progress.get(item.key) or {}).get("position", 0) >= MIN_CONTINUE_POSITION
            and not (progress.get(item.key) or {}).get("finished")
        ]
        continuing.sort(key=lambda item: progress[item.key].get("updated", 0), reverse=True)
        features = [item for item in items if item.media_type != "episode"]
        movies = sorted((item for item in features if item.media_type == "movie"), key=lambda item: natural_key(item.title))
        documentary_features = sorted(
            (item for item in features if item.media_type == "documentary"),
            key=lambda item: natural_key(item.title),
        )
        show_cards = [self._show_dict(show, progress) for show in shows]
        show_cards.sort(key=lambda show: natural_key(show["name"]))
        documentary_show_ids = {show.id for show in shows if show.kind == "documentary"}
        documentary_shows = [
            show for show in show_cards if show["id"] in documentary_show_ids
        ]
        documentaries = sorted(
            [
                *(item.as_dict(progress.get(item.key)) for item in documentary_features),
                *documentary_shows,
            ],
            key=lambda value: natural_key(value.get("name") or value.get("title") or ""),
        )
        recent = sorted((item for item in items if item.new), key=lambda item: item.mtime, reverse=True)[:60]

        up_next = [show for show in show_cards if show["last_watched"] and show["watched"] < show["episodes"]]
        up_next.sort(key=lambda show: show["last_watched"], reverse=True)
        return {
            "ok": True,
            "library": self.status()["library"],
            "continue": [item.as_dict(progress.get(item.key)) for item in continuing[:20]],
            "up_next": up_next[:12],
            "new": [item.as_dict(progress.get(item.key)) for item in recent],
            "favorites": self._favorite_specs(shows),
            "movies": [item.as_dict(progress.get(item.key)) for item in movies],
            "documentaries": documentaries,
            "shows": [show for show in show_cards if show.get("kind") == "tv"],
        }

    @staticmethod
    def _score(query: str, text: str) -> float:
        query_tokens = normalized(query).split()
        text_tokens = normalized(text).split()
        if not query_tokens or not text_tokens:
            return 0.0
        if normalized(query) in normalized(text):
            return 1.0 - min(0.2, (len(text_tokens) - len(query_tokens)) * 0.01)
        best = []
        for query_token in query_tokens:
            best.append(max(SequenceMatcher(None, query_token, token).ratio() for token in text_tokens))
        return sum(best) / len(best)

    def search(self, query: str, limit: int = 24) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        items, shows = self.library.snapshot()
        progress = self._progress_all()
        candidates: list[tuple[float, str, Any]] = []
        for show in shows:
            score = self._score(query, show.name)
            if score >= 0.5:
                candidates.append((score, "show", show))
        for item in items:
            label = " ".join(filter(None, (item.series, item.episode_code, item.episode_title, item.title)))
            score = self._score(query, label)
            if score >= 0.58:
                candidates.append((score, "item", item))
        candidates.sort(key=lambda row: (-row[0], natural_key(row[2].name if row[1] == "show" else row[2].title)))
        result = []
        seen = set()
        for score, kind, value in candidates:
            if value.id in seen:
                continue
            seen.add(value.id)
            card = self._show_dict(value, progress) if kind == "show" else value.as_dict(progress.get(value.key))
            card["score"] = round(score, 3)
            result.append(card)
            if len(result) >= limit:
                break
        return result

    def show_payload(self, show_id: str) -> dict[str, Any]:
        show = self.library.shows.get(show_id)
        if not show:
            raise KeyError("unknown show id")
        progress = self._progress_all()
        return {
            "ok": True,
            "show": self._show_dict(show, progress),
            "episodes": [item.as_dict(progress.get(item.key)) for item in show.episodes],
        }

    def _resolve_play(self, item_id: str | None, show_id: str | None, query: str | None, shuffle: bool) -> tuple[MediaItem, list[MediaItem]]:
        progress = self._progress_all()
        show = None
        item = None
        if item_id:
            item = self.library.items.get(item_id)
            if not item:
                raise KeyError("unknown media id")
            show = self.show_for_item(item)
        elif show_id:
            show = self.library.shows.get(show_id)
            if not show:
                raise KeyError("unknown show id")
            item = self.random.choice(show.episodes) if shuffle else self._next_episode(show, progress)
        elif query:
            matches = self.search(query, limit=1)
            if not matches:
                raise KeyError(f"no match for '{query}'")
            match = matches[0]
            if match["type"] == "show":
                show = self.library.shows[match["id"]]
                item = self.random.choice(show.episodes) if shuffle else self._next_episode(show, progress)
            else:
                item = self.library.items[match["id"]]
                show = self.show_for_item(item)
        else:
            raise ValueError("play requires item, show, or q")

        assert item is not None
        if show and not shuffle:
            start = show.episodes.index(item)
            queue = show.episodes[start:]
        else:
            queue = [item]
        return item, queue

    def surprise(self, media_type: str = "any", subtitles: str = "auto") -> dict[str, Any]:
        items, _shows = self.library.snapshot()
        progress = self._progress_all()
        choices = [item for item in items if not (progress.get(item.key) or {}).get("finished")]
        if media_type == "movie":
            choices = [item for item in choices if item.media_type in ("movie", "documentary")]
        elif media_type == "show":
            choices = [item for item in choices if item.media_type == "episode"]
        elif media_type != "any":
            raise ValueError("type must be any, movie, or show")
        if not choices:
            raise RuntimeError("no unwatched choices are available")
        chosen = self.random.choice(choices)
        return self.play(item_id=chosen.id, restart=True, subtitles=subtitles)


def default_sources() -> tuple[LibrarySource, ...]:
    return (
        LibrarySource("movingparts", "/mnt/movingparts", "/mnt/movingparts/links"),
        LibrarySource("bigboi", "/mnt/bigboi", "/mnt/bigboi/mp_backup/links"),
    )
