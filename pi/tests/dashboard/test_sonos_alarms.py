"""Alarm API behavior against a stateful speaker substitute; no live mutations."""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from xml.etree import ElementTree as ET

from flask import Flask
from werkzeug.datastructures import MultiDict

from pi.apps.van_dashboard.http import reject_cross_origin_mutations
from pi.apps.van_dashboard.routes import sonos_alarms as routes
from pi.apps.van_dashboard.sonos_alarms import SonosAlarmController


class AlarmClock:
    def __init__(self):
        self.records = {"10": {
            "ID": "10", "RoomUUID": "front", "StartTime": "07:45:00", "Duration": "",
            "Recurrence": "ON_15", "Enabled": "0", "Volume": "63", "IncludeLinkedZones": "1",
            "PlayMode": "SHUFFLE", "ProgramURI": "music:keep-this",
            "ProgramMetaData": '<DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/"><item><dc:title>Morning mix</dc:title></item></DIDL-Lite>',
        }}
        self.writes = []
        self.fail_after_write = False

    def ListAlarms(self):
        root = ET.Element("Alarms")
        for record in self.records.values():
            ET.SubElement(root, "Alarm", record)
        return {"CurrentAlarmList": ET.tostring(root, encoding="unicode")}

    def GetTimeNow(self):
        return {"CurrentLocalTime": "2026-10-15 19:10:00"}

    def UpdateAlarm(self, args):
        self.writes.append(args)
        values = {key: str(value) for key, value in args}
        values["StartTime"] = values.pop("StartLocalTime")
        self.records[values["ID"]] = values
        if self.fail_after_write:
            raise TimeoutError("private device error")

    def CreateAlarm(self, args):
        self.UpdateAlarm([("ID", "11")] + args)
        return {"AssignedID": "11"}

    def DestroyAlarm(self, args):
        self.writes.append(args)
        del self.records[dict(args)["ID"]]


class AlarmTests(unittest.TestCase):
    def setUp(self):
        self.clock = AlarmClock()
        favorite = SimpleNamespace(item_id="FV:2/7", title="Radio", resources=[SimpleNamespace(uri="radio:example")], resource_meta_data="favorite metadata")
        self.zone = SimpleNamespace(uid="front", alarmClock=self.clock,
                                    music_library=SimpleNamespace(get_sonos_favorites=lambda **kw: [favorite]))
        sonos = SimpleNamespace(coordinator=lambda: self.zone, get_zones=lambda: {"Front": self.zone})
        self.controller = SonosAlarmController(sonos)
        app = Flask(__name__)
        app.before_request(reject_cross_origin_mutations)
        app.register_blueprint(routes.bp)
        self.client = app.test_client()
        patcher = patch.object(routes, "alarms", self.controller)
        patcher.start()
        self.addCleanup(patcher.stop)

    def snapshot(self):
        return self.client.get("/api/speakers/alarms").get_json()

    def fields(self):
        alarm = self.snapshot()["alarms"][0]
        return {"id": alarm["id"], "revision": alarm["revision"], "room": alarm["room"],
                "start_time": "08:30", "duration": "02:00", "recurrence": "ON_531",
                "enabled": "0", "volume": "42", "include_linked_zones": "1",
                "play_mode": "SHUFFLE", "sound": "keep"}

    def post(self, action, data):
        return self.client.post(f"/api/speakers/alarms/{action}", data=data)

    def test_list_is_read_only_and_does_not_expose_media_uris(self):
        result = self.snapshot()
        alarm = result["alarms"][0]
        self.assertEqual(alarm["sound_title"], "Morning mix")
        self.assertEqual(alarm["room_name"], "Front")
        self.assertNotIn("program_uri", alarm)
        self.assertEqual(self.clock.writes, [])
        response = self.client.get("/api/speakers/alarm-favorites")
        self.assertEqual(response.get_json()["favorites"], [{"id": "FV:2/7", "title": "Radio"}])

    def test_snapshot_labels_speaker_timezone(self):
        with patch.object(self.clock, "GetTimeNow", return_value={
            "CurrentLocalTime": "2026-10-15 21:00:00", "CurrentUTCTime": "2026-10-16 03:00:00",
            "CurrentTimeZone": "01a40b000102000003000202ffc4",
        }):
            self.assertEqual(self.snapshot()["time_zone"], "MDT")

    def test_edit_preserves_media_and_normalizes_time_and_days(self):
        before = dict(self.clock.records["10"])
        self.assertEqual(self.post("save", self.fields()).status_code, 200)
        after = self.clock.records["10"]
        self.assertEqual(after["ProgramURI"], before["ProgramURI"])
        self.assertEqual(after["ProgramMetaData"], before["ProgramMetaData"])
        self.assertEqual(after["StartTime"], "08:30:00")
        self.assertEqual(after["Duration"], "02:00:00")
        self.assertEqual(after["Recurrence"], "ON_135")

    def test_create_chime_or_favorite(self):
        for sound, uri, metadata in (("chime", "x-rincon-buzzer:0", ""),
                                      ("favorite:FV:2/7", "radio:example", "favorite metadata")):
            fields = self.fields() | {"id": "", "revision": "", "sound": sound}
            self.assertEqual(self.post("save", fields).get_json()["id"], "11")
            self.assertEqual(self.clock.records["11"]["ProgramURI"], uri)
            self.assertEqual(self.clock.records["11"]["ProgramMetaData"], metadata)

    def test_invalid_settings_do_not_write(self):
        fields = self.fields()
        for key, value in (("volume", "101"), ("volume", "-1"), ("start_time", "24:01"),
                           ("duration", "00:00"), ("duration", "25:00"), ("recurrence", "ON_"),
                           ("recurrence", "ON_7"), ("enabled", "yes"), ("play_mode", "BAD"),
                           ("include_linked_zones", "yes"), ("room", "missing"),
                           ("sound", "favorite:gone"), ("sound", "http://arbitrary")):
            with self.subTest(key=key, value=value):
                self.assertEqual(self.post("save", fields | {key: value}).status_code, 400)
        self.assertEqual(self.clock.writes, [])

    def test_toggle_preserves_all_other_settings_and_delete_requires_current_revision(self):
        alarm = self.snapshot()["alarms"][0]
        data = {"id": alarm["id"], "revision": alarm["revision"]}
        before = dict(self.clock.records["10"])
        self.assertEqual(self.post("enabled", data | {"enabled": "1"}).status_code, 200)
        self.assertEqual(self.clock.records["10"], before | {"Enabled": "1"})
        self.assertEqual(self.post("delete", data).status_code, 409)
        data["revision"] = self.snapshot()["alarms"][0]["revision"]
        self.assertEqual(self.post("delete", data).status_code, 200)
        self.assertEqual(self.snapshot()["alarms"], [])
        self.assertEqual(self.post("delete", data).status_code, 404)

    def test_external_change_conflicts_without_overwrite(self):
        fields = self.fields()
        self.clock.records["10"]["Volume"] = "20"
        self.assertEqual(self.post("save", fields).status_code, 409)
        self.assertEqual(self.clock.writes, [])

    def test_incomplete_enable_command_cannot_delete(self):
        alarm = self.snapshot()["alarms"][0]
        with self.assertRaises(TypeError):
            self.controller.set_enabled(alarm["id"], alarm["revision"])
        with self.assertRaises(ValueError):
            self.controller.set_enabled(alarm["id"], alarm["revision"], None)
        self.assertEqual(len(self.snapshot()["alarms"]), 1)
        self.assertEqual(self.clock.writes, [])

    def test_uncertain_write_is_not_retried_and_refresh_reads_actual_state(self):
        fields = self.fields()
        self.clock.fail_after_write = True
        result = self.post("save", fields)
        self.assertEqual(result.status_code, 502)
        self.assertNotIn("private device error", result.get_data(as_text=True))
        self.assertEqual(len(self.clock.writes), 1)
        self.assertEqual(self.snapshot()["alarms"][0]["volume"], 42)

    def test_route_rejects_duplicate_fields_and_cross_origin(self):
        fields = self.fields()
        duplicate = MultiDict(list(fields.items()) + [("enabled", "1")])
        self.assertEqual(self.post("save", duplicate).status_code, 400)
        self.assertEqual(self.client.post("/api/speakers/alarms/save", data=fields,
                         headers={"Origin": "http://elsewhere", "X-Van-Dashboard": "1"}).status_code, 403)
        self.assertEqual(self.clock.writes, [])

    def test_device_decoding_errors_are_uncertain_not_input_rejections(self):
        for failure in (ValueError("private response failure"), KeyError("private field")):
            fields = self.fields()
            update = self.clock.UpdateAlarm

            def write_then_fail(args):
                update(args)
                raise failure

            with patch.object(self.clock, "UpdateAlarm", side_effect=write_then_fail):
                response = self.post("save", fields)
            self.assertEqual(response.status_code, 502)
            self.assertNotIn("private", response.get_data(as_text=True))
            self.assertEqual(self.snapshot()["alarms"][0]["volume"], 42)

    def test_concurrent_edits_only_accept_one_revision(self):
        fields = self.fields()
        alarm_id, revision = fields.pop("id"), fields.pop("revision")
        results = []

        def save():
            try:
                self.controller.save(fields, alarm_id, revision)
                results.append("saved")
            except Exception as exc:
                results.append(type(exc).__name__)

        workers = [threading.Thread(target=save) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(2)
        self.assertCountEqual(results, ["saved", "AlarmConflict"])


if __name__ == "__main__":
    unittest.main()
