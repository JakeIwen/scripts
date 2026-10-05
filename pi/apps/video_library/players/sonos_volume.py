"""Sonos volume boundary for the video library."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Iterable

from ..config import REAR_SONOS_UIDS, SONOS_DISCOVERY_TTL


class AudioPreparingError(RuntimeError):
    """Raised when rear-room setup would overwrite a Sonos volume change."""


class SonosVolumeController:
    """Read and set only the physical rear stereo zone used for movie audio."""

    def __init__(
        self,
        *,
        discover_func: Callable[..., Any] | None = None,
        rear_uids: Iterable[str] = REAR_SONOS_UIDS,
        clock: Callable[[], float] = time.monotonic,
        cache_ttl: float = SONOS_DISCOVERY_TTL,
    ):
        self.discover_func = discover_func
        self.rear_uids = tuple(rear_uids)
        if len(self.rear_uids) != 2 or len(set(self.rear_uids)) != 2:
            raise ValueError("exactly two distinct rear Sonos UIDs are required")
        self.clock = clock
        self.cache_ttl = cache_ttl
        self.lock = threading.RLock()
        self.zones: dict[str, Any] = {}
        self.zones_at = 0.0

    def invalidate(self) -> None:
        with self.lock:
            self.zones_at = 0.0

    def _get_zones(self, *, force: bool = False) -> dict[str, Any]:
        with self.lock:
            now = self.clock()
            if not force and self.zones_at and now - self.zones_at < self.cache_ttl:
                return self.zones
            if self.discover_func is None:
                from soco.discovery import discover

                found = discover(timeout=5, include_invisible=True) or set()
            else:
                try:
                    found = self.discover_func(timeout=5, include_invisible=True) or set()
                except TypeError:
                    found = self.discover_func(timeout=5) or set()
            self.zones = {
                str(zone.uid): zone
                for zone in found
                if str(getattr(zone, "uid", "")) in self.rear_uids
            }
            self.zones_at = now
            return self.zones

    def _rear_zone(self, *, force: bool = False) -> Any:
        zones = self._get_zones(force=force)
        missing = [uid for uid in self.rear_uids if uid not in zones]
        if missing:
            self.invalidate()
            raise RuntimeError("both physical rear Sonos speakers were not discovered")
        visible = [zones[uid] for uid in self.rear_uids if zones[uid].is_visible]
        if len(visible) != 1:
            self.invalidate()
            if visible:
                raise RuntimeError("rear Sonos speakers are not paired as one stereo zone")
            raise RuntimeError("rear Sonos stereo zone is not visible")
        return visible[0]

    def snapshot(self) -> dict[str, Any]:
        try:
            with self.lock:
                zone = self._rear_zone()
                return {
                    "available": True,
                    "device": str(zone.player_name),
                    "volume": int(zone.volume),
                    "muted": bool(zone.mute),
                }
        except Exception as exc:
            return {
                "available": False,
                "device": None,
                "volume": None,
                "muted": None,
                "error": str(exc),
            }

    def set_volume(self, volume: int) -> int:
        if isinstance(volume, bool) or not 0 <= int(volume) <= 100:
            raise ValueError("Sonos volume must be from 0 to 100")
        value = int(volume)
        with self.lock:
            zone = self._rear_zone(force=True)
            zone.volume = value
            return value
