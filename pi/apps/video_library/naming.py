"""Naming and normalization boundary for the video library."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from .config import CATEGORY_PRIORITY, VIDEO_EXTENSIONS
from .media_models import MediaItem

EPISODE_RE = re.compile(
    r"(?i)(?:^|[._\s-])S(?P<season>\d{1,2})[._\s-]*E(?P<episode>\d{1,3})"
)
X_EPISODE_RE = re.compile(
    r"(?i)(?:^|[._\s-])(?P<season>\d{1,2})x(?P<episode>\d{1,3})"
)
PART_EPISODE_RE = re.compile(
    r"(?i)(?:^|[._\s-])Part[._\s-]*(?P<episode>\d{1,3})(?:$|[._\s-])"
)
E_ONLY_EPISODE_RE = re.compile(
    r"(?i)(?:^|[._\s-])E(?P<episode>\d{1,3})(?:E\d{1,3})?(?:$|[._\s-])"
)
SEASON_PATH_RE = re.compile(r"(?i)(?:^|[/\\])S(?P<season>\d{1,2})(?:[/\\]|$)")
BARE_EPISODE_RE = re.compile(
    r"(?i)^(?:[._\s-]*)(?P<episode>\d{1,3})(?:$|[._\s-])"
)
FALLBACK_EPISODE_RE = re.compile(r"^(?P<code>\d{3})(?:\s|$)")
FEATURE_YEAR_RE = re.compile(r"(?<!\d)(?P<year>(?:19|20)\d{2})(?!\d)")


def natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def normalized(value: str) -> str:
    value = value.casefold().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


def canonical_series(value: str) -> str:
    name = normalized(value)
    name = re.sub(r"^(?:the)\s+", "", name)
    name = re.sub(r"\s+(?:19|20)\d{2}$", "", name)
    return name


def clean_name(value: str) -> str:
    if Path(value).suffix.casefold() in VIDEO_EXTENSIONS:
        value = str(Path(value).with_suffix(""))
    return " ".join(re.sub(r"[._]+", " ", value).split())


def stable_id(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def parse_candidate(
    category: str,
    relative: str,
    link_path: str,
    real_path: str,
    source: str,
    *,
    library_root: str | None = None,
) -> MediaItem:
    parts = Path(relative).parts
    content_parts = parts[1:] if parts and parts[0] == category else parts
    basename = parts[-1]
    basename_match = EPISODE_RE.search(basename) or X_EPISODE_RE.search(basename)
    path_match = None
    if category == "TV" and not basename_match and library_root is not None:
        resolved_root = os.path.realpath(library_root)
        resolved_real_path = os.path.realpath(real_path)
        try:
            relative_real_path = os.path.relpath(resolved_real_path, resolved_root)
        except ValueError:
            relative_real_path = None
        if relative_real_path is not None and not (
            relative_real_path == os.pardir
            or relative_real_path.startswith(os.pardir + os.sep)
        ):
            for segment in reversed(Path(relative_real_path).parts[:-1]):
                path_match = EPISODE_RE.search(segment) or X_EPISODE_RE.search(segment)
                if path_match:
                    break
    match = basename_match or path_match
    part_match = PART_EPISODE_RE.search(basename)
    part_is_episode = bool(part_match and category in ("TV", "Documentaries"))
    e_only_match = E_ONLY_EPISODE_RE.search(basename) if category == "TV" else None
    bare_match = None
    if category == "TV" and len(content_parts) > 1:
        series_prefix = content_parts[0]
        if basename.casefold().startswith(series_prefix.casefold()):
            bare_match = BARE_EPISODE_RE.search(basename[len(series_prefix) :])
    season = episode = None
    episode_title = None
    series = None

    series_kind = "documentary" if category == "Documentaries" else "tv"

    if match:
        season = int(match.group("season"))
        episode = int(match.group("episode"))
        prefix = basename[: match.start()].strip(" ._-") if basename_match else ""
        suffix = basename[match.end() :].strip(" ._-") if basename_match else ""
        series = clean_name(
            content_parts[0]
            if category == "TV" and len(content_parts) > 1
            else prefix
        )
        episode_title = clean_name(suffix) or None
    elif e_only_match:
        season_matches = list(SEASON_PATH_RE.finditer(real_path))
        season = int(season_matches[-1].group("season")) if season_matches else None
        episode = int(e_only_match.group("episode"))
        series = clean_name(content_parts[0] if len(content_parts) > 1 else basename)
        suffix = basename[e_only_match.end() :].strip(" ._-")
        episode_title = clean_name(suffix) or None
    elif bare_match:
        season = 1
        episode = int(bare_match.group("episode"))
        offset = len(content_parts[0])
        suffix = basename[offset + bare_match.end() :].strip(" ._-")
        series = clean_name(content_parts[0])
        episode_title = clean_name(suffix) or None
    elif part_is_episode:
        season = 1
        assert part_match is not None
        episode = int(part_match.group("episode"))
        prefix = basename[: part_match.start()].strip(" ._-")
        suffix = basename[part_match.end() :].strip(" ._-")
        series = clean_name(
            content_parts[0]
            if category == "TV" and len(content_parts) > 1
            else prefix
        )
        episode_title = clean_name(suffix) or None
    elif category == "TV":
        series = clean_name(content_parts[0] if len(content_parts) > 1 else basename)
        basename_words = normalized(clean_name(basename))
        series_words = normalized(series)
        remainder = (
            basename_words[len(series_words) :].strip()
            if series_words and basename_words.startswith(series_words)
            else basename_words
        )
        fallback = FALLBACK_EPISODE_RE.search(remainder)
        if fallback:
            code = fallback.group("code")
            season, episode = int(code[:-2]), int(code[-2:])

    is_episode = bool(category == "TV" or match or part_is_episode or bare_match)
    if is_episode:
        series = series or clean_name(
            content_parts[0] if len(content_parts) > 1 else basename
        )
        code_key = f"s{season}:e{episode}" if season is not None and episode is not None else normalized(relative)
        key = f"episode:{series_kind}:{canonical_series(series)}:{code_key}"
        title = episode_title or (
            f"{series} {match.group(0).strip(' ._-')}"
            if match
            else clean_name(basename)
        )
        media_type = "episode"
        year = None
    else:
        title = clean_name(basename)
        year_matches = list(FEATURE_YEAR_RE.finditer(title))
        year_match = year_matches[-1] if year_matches else None
        without_year = (
            " ".join((title[: year_match.start()] + " " + title[year_match.end() :]).split())
            if year_match
            else title
        )
        if year_match and without_year:
            year = int(year_match.group("year"))
            title = without_year
        else:
            year = None
        media_type = "documentary" if category == "Documentaries" else "movie"
        key = f"feature:{normalized(title)}:{year or ''}"
        series_kind = None

    rel_path = "/" + relative.replace(os.sep, "/")
    try:
        mtime = os.path.getmtime(real_path)
    except OSError:
        mtime = 0.0
    rank = (
        CATEGORY_PRIORITY.get(category, 99),
        len(content_parts),
        normalized(relative),
    )
    return MediaItem(
        key=key,
        id=stable_id(key),
        title=title,
        path=link_path,
        real_path=real_path,
        rel_path=rel_path,
        media_type=media_type,
        source=source,
        series_kind=series_kind,
        year=year,
        series=series,
        season=season,
        episode=episode,
        episode_title=episode_title,
        mtime=mtime,
        categories={category},
        aliases={rel_path},
        rank=rank,
    )
