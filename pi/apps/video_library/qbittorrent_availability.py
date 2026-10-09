"""qBittorrent per-file availability values that carry no fraction."""

from __future__ import annotations

from typing import Any, Mapping

# qBittorrent reports -1 for every file of a seeding torrent: libtorrent
# keeps no piece availability once all pieces are local.
SEEDING_AVAILABILITY = -1


def availability_unknown(item: Mapping[str, Any]) -> bool:
    """True when a torrents/files entry has no availability fraction to validate."""
    value = item.get("availability")
    if value is None:
        return True
    return not isinstance(value, bool) and value == SEEDING_AVAILABILITY
