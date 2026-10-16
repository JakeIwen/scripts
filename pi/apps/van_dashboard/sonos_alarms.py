"""Native Sonos alarm management; never cache mutable SoCo Alarm instances."""

from dataclasses import asdict, dataclass, replace
from datetime import time
import hashlib
import json
import re
import threading
from xml.etree import ElementTree as ET

from .sonos_clock import sonos_time_zone


PLAY_MODES = ("NORMAL", "SHUFFLE_NOREPEAT", "SHUFFLE", "REPEAT_ALL",
              "REPEAT_ONE", "SHUFFLE_REPEAT_ONE")
CHIME = "x-rincon-buzzer:0"


class AlarmConflict(Exception):
    """The speaker's alarm changed since the form was opened."""


class AlarmValidationError(ValueError):
    """Input was rejected before any device mutation."""


class AlarmNotFound(Exception):
    """A fresh device read did not contain the requested alarm."""


@dataclass(frozen=True)
class SonosAlarm:
    id: str
    room: str
    start_time: str
    duration: str
    recurrence: str
    enabled: bool
    volume: int
    include_linked_zones: bool
    play_mode: str
    program_uri: str
    program_metadata: str

    @classmethod
    def from_element(cls, element):
        a = element.attrib
        return cls(a["ID"], a["RoomUUID"], a["StartTime"], a["Duration"],
                   a["Recurrence"], a["Enabled"] == "1", int(a["Volume"]),
                   a["IncludeLinkedZones"] == "1", a["PlayMode"],
                   a["ProgramURI"], a["ProgramMetaData"])

    @property
    def revision(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    def public(self, rooms):
        result = asdict(self)
        del result["program_uri"], result["program_metadata"]
        result.update(revision=self.revision, room_name=rooms.get(self.room, "Unavailable speaker"),
                      sound_title=self.sound_title())
        return result

    def sound_title(self):
        if self.program_uri in ("", CHIME):
            return "Sonos chime"
        try:
            root = ET.fromstring(self.program_metadata)
            title = root.find(".//{http://purl.org/dc/elements/1.1/}title")
            if title is not None and title.text:
                return title.text
        except ET.ParseError:
            pass
        return "Existing music"

    def arguments(self):
        return [("StartLocalTime", self.start_time), ("Duration", self.duration),
                ("Recurrence", self.recurrence), ("Enabled", int(self.enabled)),
                ("RoomUUID", self.room), ("ProgramURI", self.program_uri),
                ("ProgramMetaData", self.program_metadata), ("PlayMode", self.play_mode),
                ("Volume", self.volume), ("IncludeLinkedZones", int(self.include_linked_zones))]


def alarm_clock(value, label, allow_empty=False):
    if value == "" and allow_empty:
        return ""
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", value):
        raise AlarmValidationError(f"{label} must be HH:MM or HH:MM:SS")
    try:
        return time.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise AlarmValidationError(f"{label} is not a valid time") from exc


def alarm_fields(fields):
    required = {"room", "start_time", "duration", "recurrence", "enabled", "volume",
                "include_linked_zones", "play_mode", "sound"}
    if set(fields) != required:
        raise AlarmValidationError("provide all alarm settings")
    values = dict(fields)
    for name in ("enabled", "include_linked_zones"):
        if values[name] not in ("0", "1"):
            raise AlarmValidationError(f"{name} must be 0 or 1")
        values[name] = values[name] == "1"
    if not re.fullmatch(r"\d{1,3}", values["volume"]) or not 0 <= int(values["volume"]) <= 100:
        raise AlarmValidationError("volume must be from 0 to 100")
    values["volume"] = int(values["volume"])
    values["start_time"] = alarm_clock(values["start_time"], "Start time")
    values["duration"] = alarm_clock(values["duration"], "Duration", allow_empty=True)
    if values["duration"] == "00:00:00":
        raise AlarmValidationError("duration must be positive or blank for no limit")
    recurrence = values["recurrence"]
    if recurrence not in ("ONCE", "DAILY", "WEEKDAYS", "WEEKENDS"):
        if not re.fullmatch(r"ON_[0-6]{1,7}", recurrence):
            raise AlarmValidationError("choose a repeat schedule or at least one day")
        values["recurrence"] = "ON_" + "".join(sorted(set(recurrence[3:])))
    if values["play_mode"] not in PLAY_MODES:
        raise AlarmValidationError("unsupported play mode")
    return values


class SonosAlarmController:
    def __init__(self, sonos):
        self.sonos = sonos
        self.lock = threading.Lock()

    @staticmethod
    def _read(zone):
        # Bypass SoCo's versioned singleton: after a timed-out update its Python
        # objects can disagree with the device even when the version is unchanged.
        response = zone.alarmClock.ListAlarms()
        return {a.id: a for a in map(SonosAlarm.from_element,
                                    ET.fromstring(response["CurrentAlarmList"]))}

    def snapshot(self):
        with self.lock:
            zone = self.sonos.coordinator()
            rooms = {z.uid: name for name, z in self.sonos.get_zones().items()}
            alarms = self._read(zone)
            time_zone = None
            try:
                clock = zone.alarmClock.GetTimeNow()
                local_time = clock["CurrentLocalTime"]
                time_zone = sonos_time_zone(clock)
            except Exception:
                local_time = None
            return {"ok": True, "alarms": [a.public(rooms) for a in
                    sorted(alarms.values(), key=lambda a: (a.start_time, a.id))],
                    "rooms": [{"id": uid, "name": name} for uid, name in rooms.items()],
                    "local_time": local_time, "time_zone": time_zone,
                    "play_modes": list(PLAY_MODES)}

    def favorites(self):
        zone = self.sonos.coordinator()
        return {f.item_id: f for f in zone.music_library.get_sonos_favorites(complete_result=True)
                if f.resources and f.resources[0].uri}

    def save(self, fields, alarm_id="", revision=""):
        values = alarm_fields(fields)
        with self.lock:
            zone = self.sonos.coordinator()
            existing = self._existing(zone, alarm_id, revision) if alarm_id else None
            rooms = {z.uid for z in self.sonos.get_zones().values()}
            if values["room"] not in rooms:
                raise AlarmValidationError("choose an available speaker")
            sound = values.pop("sound")
            uri, metadata = self._sound(sound, existing)
            alarm = SonosAlarm(id=alarm_id, program_uri=uri, program_metadata=metadata, **values)
            args = alarm.arguments()
            if alarm_id:
                zone.alarmClock.UpdateAlarm([("ID", alarm_id)] + args)
            else:
                alarm_id = zone.alarmClock.CreateAlarm(args)["AssignedID"]
            return str(alarm_id)

    def _sound(self, sound, existing):
        if sound == "keep" and existing:
            return existing.program_uri, existing.program_metadata
        if sound == "chime":
            return CHIME, ""
        if sound.startswith("favorite:"):
            favorite = self.favorites().get(sound.removeprefix("favorite:"))
            if favorite:
                return favorite.resources[0].uri, favorite.resource_meta_data
        raise AlarmValidationError("choose Sonos chime or an available favorite")

    def _existing(self, zone, alarm_id, revision):
        alarm = self._read(zone).get(alarm_id)
        if alarm is None:
            raise AlarmNotFound("alarm no longer exists; refresh alarms")
        if not revision or alarm.revision != revision:
            raise AlarmConflict("Alarm changed elsewhere. Reopen it before making changes.")
        return alarm

    def set_enabled(self, alarm_id, revision, enabled):
        if not isinstance(enabled, bool):
            raise AlarmValidationError("enabled must be a boolean")
        with self.lock:
            zone = self.sonos.coordinator()
            alarm = self._existing(zone, alarm_id, revision)
            alarm = replace(alarm, enabled=enabled)
            zone.alarmClock.UpdateAlarm([("ID", alarm_id)] + alarm.arguments())

    def delete(self, alarm_id, revision):
        with self.lock:
            zone = self.sonos.coordinator()
            self._existing(zone, alarm_id, revision)
            zone.alarmClock.DestroyAlarm([("ID", alarm_id)])
