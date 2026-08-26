"""Sonos discovery, grouping, transport, and album-art controller.

This module supports both repository package imports and the flat sibling
layout used by the deployed van-dashboard service.
"""

__all__ = ["SonosController"]

if __package__:
    from .van_dashboard_common import (
        DEFAULT_SONOS_DEVICE,
        Request,
        SONOS_ART_MAX_BYTES,
        SONOS_ART_TIMEOUT,
        hashlib,
        threading,
        time,
        urlopen,
        urlsplit,
    )
else:
    from van_dashboard_common import (
        DEFAULT_SONOS_DEVICE,
        Request,
        SONOS_ART_MAX_BYTES,
        SONOS_ART_TIMEOUT,
        hashlib,
        threading,
        time,
        urlopen,
        urlsplit,
    )


class SonosController:
    """Small Sonos grouping/volume controller matching the audiobook page."""

    def __init__(self, store, discover_func=None, clock=time.monotonic, art_opener=None):
        self.store = store
        self.discover_func = discover_func
        self.clock = clock
        self.art_opener = art_opener or urlopen
        self.lock = threading.Lock()
        self.zones = {}
        self.zones_at = 0.0

    def get_zones(self, force=False):
        with self.lock:
            if self.zones and not force and self.clock() - self.zones_at < 600:
                return self.zones
            if self.discover_func is None:
                from soco.discovery import discover

                found = discover(timeout=5) or set()
            else:
                found = self.discover_func(timeout=5) or set()
            fresh = {zone.player_name: zone for zone in found if zone.is_visible}
            if fresh:
                self.zones = fresh
                self.zones_at = self.clock()
            return self.zones

    def coordinator(self):
        zones = self.get_zones()
        if not zones:
            zones = self.get_zones(force=True)
        if not zones:
            raise RuntimeError("no Sonos speakers found")
        remembered = self.store.get("sonos_device")
        if remembered in zones:
            return zones[remembered].group.coordinator
        for zone in zones.values():
            if zone.group.coordinator != zone:
                continue
            try:
                state = zone.get_current_transport_info()["current_transport_state"]
            except Exception:
                continue
            if state == "PLAYING":
                return zone
        if DEFAULT_SONOS_DEVICE in zones:
            return zones[DEFAULT_SONOS_DEVICE].group.coordinator
        return next(iter(zones.values())).group.coordinator

    def snapshot(self):
        zones = self.get_zones()
        if not zones:
            raise RuntimeError("no Sonos speakers found")
        coordinator = self.coordinator()
        coordinator_name = coordinator.player_name
        members = {member.player_name for member in coordinator.group.members}
        try:
            group_volume = coordinator.group.volume
        except Exception:
            group_volume = None
        try:
            group_muted = coordinator.group.mute
        except Exception:
            group_muted = None
        try:
            transport_state = coordinator.get_current_transport_info()[
                "current_transport_state"
            ]
        except Exception:
            transport_state = "UNKNOWN"
        try:
            track = coordinator.get_current_track_info() or {}
        except Exception:
            track = {}
        now_playing = {
            "title": track.get("title") or track.get("radio_show") or "Nothing playing",
            "artist": track.get("artist") or track.get("album") or "",
            "album": track.get("album") or "",
            "position": track.get("position") or "",
            "duration": track.get("duration") or "",
            "transport_state": transport_state,
            "album_art": self.album_art_path(track.get("album_art")),
        }
        speakers = []
        for name, zone in sorted(zones.items()):
            try:
                volume = zone.volume
            except Exception:
                volume = None
            try:
                muted = zone.mute
            except Exception:
                muted = None
            speakers.append(
                {
                    "name": name,
                    "volume": volume,
                    "muted": muted,
                    "grouped": name in members,
                    "coordinator": name == coordinator_name,
                    "group_coordinator": zone.group.coordinator.player_name,
                }
            )
        return {
            "ok": True,
            "coordinator": coordinator_name,
            "group": {"volume": group_volume, "muted": group_muted},
            "now_playing": now_playing,
            "speakers": speakers,
        }

    @staticmethod
    def album_art_path(art_url):
        if not isinstance(art_url, str) or not art_url:
            return None
        key = hashlib.sha256(art_url.encode("utf-8")).hexdigest()[:16]
        return f"/api/speakers/art/{key}"

    def album_art(self, key):
        coordinator = self.coordinator()
        track = coordinator.get_current_track_info() or {}
        art_url = track.get("album_art")
        if not art_url or self.album_art_path(art_url).rsplit("/", 1)[-1] != key:
            raise KeyError("album art is no longer current")
        parsed = urlsplit(art_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != coordinator.ip_address
            or parsed.port != 1400
        ):
            raise ValueError("Sonos returned an unexpected album-art URL")
        request_object = Request(art_url, headers={"User-Agent": "van-dashboard/1"})
        with self.art_opener(request_object, timeout=SONOS_ART_TIMEOUT) as response:
            content = response.read(SONOS_ART_MAX_BYTES + 1)
        if len(content) > SONOS_ART_MAX_BYTES:
            raise ValueError("Sonos album art exceeded the size limit")
        signatures = (
            (b"\xff\xd8\xff", "image/jpeg"),
            (b"\x89PNG\r\n\x1a\n", "image/png"),
            (b"GIF87a", "image/gif"),
            (b"GIF89a", "image/gif"),
        )
        content_type = next(
            (mime for signature, mime in signatures if content.startswith(signature)),
            None,
        )
        if content_type is None and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            content_type = "image/webp"
        if content_type is None:
            raise ValueError("Sonos album art was not a supported image")
        return content, content_type

    def select(self, name):
        zones = self.get_zones()
        if name not in zones:
            raise KeyError(f"unknown speaker '{name}'")
        coordinator = zones[name].group.coordinator
        self.store.set("sonos_device", coordinator.player_name)
        return coordinator.player_name

    def group(self, name, grouped):
        zones = self.get_zones()
        if name not in zones:
            raise KeyError(f"unknown speaker '{name}'")
        coordinator = self.coordinator()
        speaker = zones[name]
        if not grouped and speaker.player_name == coordinator.player_name:
            raise ValueError("select another group before removing its coordinator")
        members = {member.player_name for member in coordinator.group.members}
        if grouped and name not in members:
            speaker.join(coordinator)
            message = f"added {name} to {coordinator.player_name}"
        elif not grouped and name in members:
            speaker.unjoin()
            message = f"removed {name} from {coordinator.player_name}"
        else:
            message = f"{name} group is unchanged"
        self.get_zones(force=True)
        return message

    def set_volume(self, name, volume):
        zones = self.get_zones()
        if name not in zones:
            raise KeyError(f"unknown speaker '{name}'")
        volume = max(0, min(100, int(volume)))
        zones[name].volume = volume
        return volume

    def set_mute(self, name, muted):
        zones = self.get_zones()
        if name not in zones:
            raise KeyError(f"unknown speaker '{name}'")
        zones[name].mute = bool(muted)
        return bool(muted)

    def set_group_volume(self, volume):
        volume = max(0, min(100, int(volume)))
        self.coordinator().group.volume = volume
        return volume

    def set_group_mute(self, muted):
        muted = bool(muted)
        self.coordinator().group.mute = muted
        return muted

    def transport(self, action):
        if action not in ("play_pause", "previous", "next"):
            raise ValueError("unknown Sonos transport action")
        coordinator = self.coordinator()
        if action == "play_pause":
            state = coordinator.get_current_transport_info()["current_transport_state"]
            if state == "PLAYING":
                coordinator.pause()
                return "Sonos paused"
            coordinator.play()
            return "Sonos playing"
        if action == "previous":
            coordinator.previous()
            return "Previous Sonos track"
        if action == "next":
            coordinator.next()
            return "Next Sonos track"
