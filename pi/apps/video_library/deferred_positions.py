"""Scan-local lookup of unresolved legacy checkpoints by exact path evidence."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DeferredPosition:
    order: int
    path: str
    microseconds: int
    record: dict[str, Any]
    raw: dict[str, Any]


class DeferredLegacyPositions:
    def __init__(self, records: Iterable[dict[str, Any]]):
        self._by_path: dict[str, dict[str, DeferredPosition]] = {}
        for order, record in enumerate(records):
            if record.get("source_kind") != "legacy-vlc-position-log":
                continue
            try:
                raw = json.loads(record.get("raw_json") or "{}")
                path = str(raw["relative_path"]).replace(os.sep, "/")
                microseconds = int(raw["position_microseconds"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            position = DeferredPosition(order, path, microseconds, record, raw)
            self._by_path.setdefault(path, {})[str(record["import_record_id"])] = position

    def matching(self, evidence: set[str]) -> list[DeferredPosition]:
        # Preserve the catalog's chronological order across all matching aliases.
        return sorted(
            (
                position
                for path in evidence
                for position in self._by_path.get(path, {}).values()
            ),
            key=lambda position: position.order,
        )

    def discard(self, position: DeferredPosition) -> None:
        # Later items must see a successful resolution just as a fresh query would.
        self._by_path[position.path].pop(str(position.record["import_record_id"]), None)
