import copy
import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from pi.apps.van_dashboard import van_dashboard_common as common
from pi.apps.van_dashboard import van_dashboard_projects as project_module
from pi.apps.van_dashboard.hosted_project_report import Host, LinkKind, Source, Status
from pi.apps.van_dashboard.van_dashboard_common import StateStore
from pi.apps.van_dashboard.van_dashboard_projects import HostedProjectStore, HostedProjectConflict

# Runtime construction migrates state, so importing these tests must not open the
# user's real state file even when no test-runner environment is configured.
with tempfile.TemporaryDirectory() as import_folder:
    with mock.patch.object(common, "StateStore", side_effect=lambda: StateStore(
        os.path.join(import_folder, "state.json")
    )):
        from pi.apps.van_dashboard import runtime
        from pi.apps.van_dashboard.van_dashboard import create_app
        dashboard_app = create_app()


SCANNED_AT = "2026-10-16T18:15:00Z"
LATER = "2026-10-16T18:30:00Z"
WRITER = "pi.apps.van_dashboard.van_dashboard_common.atomic_json_write"


def listener(host=Host.PI, unit="example.service", name="Example", port=19001):
    kind, hostname = {
        Host.PI: (LinkKind.LAN, "vanpi.lan"),
        Host.MAC: (LinkKind.LOCAL, "127.0.0.1"),
        Host.WEB: (LinkKind.WEB, "example.com"),
    }[host]
    return {
        "key": f"{host}:{unit}", "host": host, "name": name,
        "description": "Discovered description.",
        "links": [{"kind": kind, "url": f"http://{hostname}:{port}/"}],
    }


def web_listener(app, port):
    return {
        "key": f"web:{app}", "host": Host.WEB, "name": app.title(), "description": "",
        "links": [{"kind": LinkKind.WEB, "url": f"https://{app}.lionfeather.dev/"}],
        "service_port": port,
    }


def keys(entry):
    return [service["key"] for service in entry["services"]]


def counts(created=0, adopted=0, merged=0, updated=0, stale=0):
    return {"created": created, "adopted": adopted, "merged": merged, "updated": updated, "stale": stale}


def report(*listeners, hosts=None, scanned_at=SCANNED_AT):
    return {
        "hosts": list(dict.fromkeys(item["host"] for item in listeners)) if hosts is None else hosts,
        "scanned_at": scanned_at, "listeners": list(listeners),
    }


class HostedProjectFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, "state.json")
        self.state = StateStore(self.path)
        self.projects = HostedProjectStore(self.state)
        self.initial = self.projects.snapshot()
        self.notification_threads = []
        self.process_threads = []
        self.process = mock.Mock(returncode=0)
        self.run = self.enterContext(mock.patch.object(
            common.subprocess, "run", side_effect=self.record_process,
        ))
        self.real_notify = project_module.notify_new_projects
        self.notify = self.enterContext(mock.patch.object(
            project_module, "notify_new_projects", side_effect=self.record_notification,
        ))
        self.addCleanup(self.join_notifications)

    def record_process(self, *args, **kwargs):
        self.process_threads.append(threading.current_thread())
        return self.process

    def record_notification(self, names):
        thread = self.real_notify(names)
        self.notification_threads.append(thread)
        return thread

    def join_notifications(self):
        for thread in self.notification_threads:
            thread.join(2)
            self.assertFalse(thread.is_alive(), "notification thread did not finish")

    def named(self, name):
        return next(entry for entry in self.projects.snapshot() if entry["name"] == name)


class HostedProjectMigrationTests(HostedProjectFixture):
    def write_legacy(self, name="Legacy"):
        legacy = {
            "id": "custom-legacy", "name": name, "host": "Custom", "description": "Keep me",
            "links": [{"kind": "web", "label": "Original", "url": "https://legacy.example/"}],
        }
        payload = {"hosted_projects": [legacy], "sonos_device": "Front", "other": {"keep": [1, 2]}}
        Path(self.path).write_text(json.dumps(payload), encoding="utf-8")
        return legacy, payload

    def test_legacy_migration_seeds_preserves_other_state_and_survives_reload(self):
        legacy, payload = self.write_legacy()
        state = StateStore(self.path)
        with mock.patch(WRITER, wraps=common.atomic_json_write) as write:
            store = HostedProjectStore(state)
            write.assert_not_called()
            # Any later save persists the migration along with the change.
            migrated = store.update("custom-legacy", {"status": Status.LISTED})
            write.assert_not_called()
            migrated = store.update("custom-legacy", {"status": Status.HIDDEN})
            write.assert_called_once()
        self.assertEqual(state.get("hosted_projects_schema"), 3)
        self.assertEqual(len(migrated), len(self.initial) + 1)
        saved_legacy = next(entry for entry in migrated if entry["id"] == legacy["id"])
        for field in ("id", "name", "description", "links"):
            self.assertEqual(saved_legacy[field], legacy[field])
        for field, expected in {"host": Host.CUSTOM, "status": Status.HIDDEN,
                                "source": Source.MANUAL, "services": [], "live": True,
                                "first_seen": None, "last_seen": None}.items():
            self.assertEqual(saved_legacy[field], expected)
        self.assertRegex(saved_legacy["updated_at"], r"^\d{4}-\d{2}-\d{2}T.*Z$")
        self.assertEqual(state.get("other"), payload["other"])
        self.assertEqual(state.get("sonos_device"), "Front")
        with mock.patch(WRITER) as write:
            self.assertEqual(HostedProjectStore(StateStore(self.path)).snapshot(), migrated)
            write.assert_not_called()
        visual = next(entry for entry in migrated if entry["id"] == "seed-visual-guides")
        self.assertEqual(visual["host"], Host.PI)
        self.assertEqual(visual["links"], [
            {"kind": "local", "label": "This Mac", "url": "http://127.0.0.1:8792/"},
            {"kind": "lan", "label": "LAN", "url": "http://vanpi.lan:8791/"},
            {"kind": "ts", "label": "Tailscale", "url": "http://100.82.91.76:8791/"},
        ])
        self.assertTrue(all("note" not in entry for entry in migrated))
        self.assertTrue(all("note" not in link for entry in migrated for link in entry["links"]))

    def test_seeding_skips_existing_name_case_insensitively(self):
        self.write_legacy("FIELDWORK")
        migrated = HostedProjectStore(StateStore(self.path)).snapshot()
        self.assertEqual(len(migrated), len(self.initial))
        self.assertNotIn("seed-fieldwork", {entry["id"] for entry in migrated})
        self.assertEqual(sum(entry["name"].casefold() == "fieldwork" for entry in migrated), 1)

    def test_construction_migrates_in_memory_without_touching_disk(self):
        self.write_legacy()
        before = Path(self.path).read_bytes()
        state = StateStore(self.path)
        with mock.patch(WRITER) as write:
            projects = HostedProjectStore(state)
            write.assert_not_called()
        self.assertEqual(len(projects.snapshot()), len(self.initial) + 1)
        self.assertEqual(state.get("hosted_projects_schema"), 3)
        self.assertEqual(Path(self.path).read_bytes(), before)
        missing = os.path.join(self.temp.name, "absent.json")
        HostedProjectStore(StateStore(missing))
        self.assertFalse(os.path.exists(missing))


class HostedProjectTests(HostedProjectFixture):
    def test_all_url_types_survive_restart_without_changing_other_state(self):
        self.state.set("sonos_device", "Front")
        fields = {"name": "Example", "local": "http://127.0.0.1:1234/",
                  "lan": "http://example.lan/", "ts": "http://100.64.0.2:1234/",
                  "web": "https://example.com/"}
        saved = self.projects.add(fields)
        entry = self.named("Example")
        self.assertEqual([x["kind"] for x in entry["links"]], list(LinkKind))
        self.assertEqual(entry["host"], Host.CUSTOM)
        self.assertEqual(entry["status"], Status.LISTED)
        self.assertEqual(entry["source"], Source.MANUAL)
        self.assertTrue(entry["live"])
        self.assertEqual(entry["services"], [])
        self.assertIsNone(entry["first_seen"])
        self.assertIsNone(entry["last_seen"])
        self.assertTrue(entry["updated_at"].endswith("Z"))
        reloaded = StateStore(self.path)
        self.assertEqual(HostedProjectStore(reloaded).snapshot(), saved)
        self.assertEqual(reloaded.get("sonos_device"), "Front")
        saved[0]["links"].clear()
        self.assertTrue(self.projects.snapshot()[0]["links"])

    def test_rejects_invalid_input_without_saving(self):
        invalid = [{"name": "Example"}, {"name": "", "local": "http://localhost/"},
                   {"name": "Example", "command": "anything", "web": "https://example.com"}]
        for url in ("javascript:alert(1)", "file:///etc/passwd", "https://user:secret@example.com/",
                    "http://example.com:99999", "http:///missing-host", "http://ex ample.com/",
                    "https://example.com\\@evil.com/", "http://example.com/\nHeader:stuff"):
            invalid.append({"name": "Example", "web": url})
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.projects.add(fields)
        self.assertEqual(self.projects.snapshot(), self.initial)

    def test_same_submission_is_idempotent_but_conflicting_name_is_rejected(self):
        fields = {"name": "Example", "web": "https://example.com/"}
        self.assertEqual(self.projects.add(fields), self.projects.add(fields))
        with self.assertRaises(HostedProjectConflict):
            self.projects.add({"name": "EXAMPLE", "web": "https://other.example/"})
        self.assertEqual(len(self.projects.snapshot()), len(self.initial) + 1)

    def test_failed_disk_write_does_not_leave_a_phantom_project(self):
        with mock.patch(WRITER, side_effect=OSError("full")):
            with self.assertRaises(OSError):
                self.projects.add({"name": "Example", "web": "https://example.com"})
        self.assertEqual(self.projects.snapshot(), self.initial)

    def test_concurrent_additions_do_not_lose_entries(self):
        threads = [threading.Thread(target=self.projects.add, args=(
            {"name": f"Project {i}", "web": f"https://example.com/{i}"},
        )) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(HostedProjectStore(StateStore(self.path)).snapshot()), len(self.initial) + 4)


class HostedProjectReportTests(HostedProjectFixture):
    def test_create_and_idempotent_repost_do_not_write_or_notify_again(self):
        payload = report(listener())
        self.assertEqual(self.projects.apply_report(payload), counts(created=1))
        created = self.named("Example")
        self.assertEqual(created["id"], "pi:example.service")
        self.assertEqual(created["services"], [{"key": created["id"], "live": True}])
        self.assertEqual(created["status"], Status.NEW)
        self.assertEqual(created["source"], Source.SCANNED)
        self.assertEqual(created["first_seen"], SCANNED_AT)
        self.assertEqual(created["last_seen"], SCANNED_AT)
        self.assertEqual(created["links"][0]["label"], "LAN")
        before = self.projects.snapshot()
        with mock.patch(WRITER) as write:
            self.assertEqual(self.projects.apply_report(payload), counts())
            write.assert_not_called()
        self.assertEqual(self.projects.snapshot(), before)
        self.notify.assert_called_once_with(["Example"])
        self.assertEqual(HostedProjectStore(StateStore(self.path)).snapshot(), before)
        payload["listeners"][0]["links"][0]["url"] = "https://changed.example/"
        self.assertEqual(self.projects.snapshot(), before)

    def test_reordered_report_links_are_idempotent(self):
        item = listener()
        item["links"].insert(0, {"kind": "ts", "url": "http://100.82.91.76:19001/"})
        self.projects.apply_report(report(item))
        before = self.projects.snapshot()
        with mock.patch(WRITER) as write:
            self.assertEqual(self.projects.apply_report(report(item))["updated"], 0)
            write.assert_not_called()
        self.assertEqual(self.projects.snapshot(), before)

    def test_report_accepts_200_listeners_and_is_not_limited_by_form_capacity(self):
        items = [listener(unit=f"app-{i}.service", name=f"App {i}", port=19000 + i)
                 for i in range(200)]
        result = self.projects.apply_report(report(*items))
        self.assertEqual(result["created"], 200)
        self.assertEqual(len(self.projects.snapshot()), len(self.initial) + 200)
        self.notify.assert_called_once()

    def test_key_match_preserves_review_and_refreshes_only_reported_link_kinds(self):
        self.projects.apply_report(report(listener()))
        self.projects.update("pi:example.service", {
            "name": "My name", "description": "My description", "status": Status.HIDDEN,
            "links": [
                {"kind": "lan", "url": "http://vanpi.lan:19001/"},
                {"kind": "web", "label": "Keep", "url": "https://my.example/"},
            ],
        })
        before = self.named("My name")
        result = self.projects.apply_report(report(listener(port=19002), scanned_at=LATER))
        after = self.named("My name")
        self.assertEqual(result, counts(updated=1))
        for field in ("name", "description", "status", "updated_at", "first_seen"):
            self.assertEqual(after[field], before[field])
        self.assertEqual(after["last_seen"], LATER)
        self.assertTrue(after["live"])
        self.assertEqual(after["links"], [
            {"kind": "lan", "label": "LAN", "url": "http://vanpi.lan:19002/"},
            {"kind": "web", "label": "Keep", "url": "https://my.example/"},
        ])

    def test_port_adoption_keeps_seed_identity_name_status_source_and_url(self):
        before = self.named("Fieldwork")
        item = listener(Host.MAC, "com.jacobr.fieldwork", "Scanner name", 4317)
        item["links"][0]["url"] += "new-path"
        result = self.projects.apply_report(report(item))
        after = self.named("Fieldwork")
        self.assertEqual(result, counts(adopted=1))
        for field in ("id", "name", "description", "status", "source", "updated_at"):
            self.assertEqual(after[field], before[field])
        self.assertEqual(keys(after), [item["key"]])
        self.assertEqual(after["first_seen"], SCANNED_AT)
        self.assertEqual(after["last_seen"], SCANNED_AT)
        self.assertEqual(after["links"], before["links"])
        self.notify.assert_not_called()

    def test_network_history_keeps_its_path_when_the_dashboard_unit_adopts_it(self):
        before = self.named("Network History")
        item = listener(Host.PI, "van-dashboard.service", "Van Dashboard", 8788)
        self.assertEqual(self.projects.apply_report(report(item))["adopted"], 1)
        after = self.named("Network History")
        self.assertEqual(keys(after), [item["key"]])
        self.assertEqual(after["links"], before["links"])
        self.assertIn("#network-history", after["links"][0]["url"])

    def test_visual_guides_mac_and_pi_services_share_the_seeded_row(self):
        pi = listener(Host.PI, "visual-guides.service", "Visual Guides", 8791)
        mac = listener(Host.MAC, "com.jacobr.visual-guides", "Visual Guides", 8792)
        self.projects.apply_report(report(pi))
        self.assertEqual(self.projects.apply_report(report(mac)), counts(adopted=1))
        seed = self.named("Visual Guides")
        self.assertEqual(seed["id"], "seed-visual-guides")
        self.assertEqual(seed["host"], Host.PI)
        self.assertEqual(keys(seed), [mac["key"], pi["key"]])
        self.assertEqual([link["kind"] for link in seed["links"]], ["local", "lan", "ts"])
        self.assertEqual(len(self.projects.snapshot()), len(self.initial))

    def test_cross_host_adoption_changes_host_and_preserves_hidden_status(self):
        self.projects.update("seed-visual-guides", {"status": Status.HIDDEN})
        item = listener(Host.MAC, "com.jacobr.visual-guides", "Visual Guides", 8792)
        self.assertEqual(self.projects.apply_report(report(item))["adopted"], 1)
        seed = self.named("Visual Guides")
        self.assertEqual(seed["host"], Host.MAC)
        self.assertEqual(seed["status"], Status.HIDDEN)
        self.assertEqual(seed["source"], Source.MANUAL)
        self.assertEqual(len(seed["links"]), 3)

    def test_scan_names_are_disambiguated_and_stay_within_name_limit(self):
        name = "x" * 80
        self.projects.add({"name": name, "web": "https://manual.example/"})
        items = [listener(Host.PI, f"app-{i}.service", name, 19001 + i) for i in range(3)]
        self.assertEqual(self.projects.apply_report(report(*items))["created"], 3)
        names = [entry["name"] for entry in self.projects.snapshot()]
        self.assertEqual(len({value.casefold() for value in names}), len(names))
        self.assertTrue(all(len(value) <= 80 for value in names))
        self.assertIn(name[:72] + " (Vanpi)", names)
        self.assertIn(name[:70] + " (Vanpi) 2", names)
        self.assertIn(name[:70] + " (Vanpi) 3", names)

    def test_stale_affects_claimed_entries_of_reported_hosts_only(self):
        pi = listener()
        mac = listener(Host.MAC, "com.jacobr.mac-example", "Mac example", 19002)
        web = listener(Host.WEB, "web-example", "Web example", 19003)
        adopted = listener(Host.MAC, "com.jacobr.fieldwork", "Fieldwork", 4317)
        self.projects.apply_report(report(pi, mac, web, adopted))
        self.projects.update(pi["key"], {"status": Status.HIDDEN})
        result = self.projects.apply_report(report(hosts=[Host.PI], scanned_at=LATER))
        self.assertEqual(result, counts(stale=1))
        self.assertFalse(self.named("Example")["live"])
        self.assertEqual(self.named("Example")["last_seen"], SCANNED_AT)
        self.assertEqual(self.named("Example")["status"], Status.HIDDEN)
        self.assertTrue(self.named("Mac example")["live"])
        self.assertTrue(self.named("Web example")["live"])
        result = self.projects.apply_report(report(hosts=[Host.MAC], scanned_at=LATER))
        self.assertEqual(result["stale"], 2)
        self.assertFalse(self.named("Mac example")["live"])
        self.assertFalse(self.named("Fieldwork")["live"])
        self.assertEqual(self.named("Fieldwork")["source"], Source.MANUAL)
        self.assertTrue(self.named("Health Calendar")["live"])
        self.assertEqual(self.projects.apply_report(report(hosts=[Host.PI]))["stale"], 0)
        self.projects.apply_report(report(listener(port=19004), scanned_at=LATER))
        self.assertTrue(self.named("Example")["live"])
        self.assertEqual(self.named("Example")["status"], Status.HIDDEN)

    def test_one_invalid_listener_rejects_whole_report_without_stale_or_disk_change(self):
        self.projects.apply_report(report(listener()))
        before, disk = self.projects.snapshot(), Path(self.path).read_bytes()
        invalid = listener(unit="bad.service", name="Bad", port=19003)
        invalid["links"] = [{"kind": "lan", "url": "javascript:alert(1)"}]
        payload = report(listener(unit="new.service", name="New", port=19002), invalid)
        with self.assertRaises(ValueError):
            self.projects.apply_report(payload)
        self.assertEqual(self.projects.snapshot(), before)
        self.assertEqual(Path(self.path).read_bytes(), disk)
        self.notify.assert_called_once()

    def test_rejects_invalid_report_fields_without_state_change(self):
        invalid = [None, [], {}, {**report(), "extra": True}, report(hosts=["custom"]),
                   report(hosts=[{}]), report(hosts=["pi", "pi"]), report(listener(), hosts=["mac"]),
                   report(listener(), listener())]
        for value in (None, 123, "bad", "2026-02-30T00:00:00Z", "2026-10-16T18:15:00+00:00"):
            invalid.append(report(scanned_at=value))
        for field, value in (("key", []), ("key", "mac:wrong"), ("key", "pi:"), ("host", {}),
                             ("name", "bad\x85name"), ("name", "x" * 81), ("description", []),
                             ("description", "x" * 201), ("links", []), ("links", "bad")):
            item = listener()
            item[field] = value
            invalid.append(report(item, hosts=[Host.PI]))
        invalid.append(report(*[listener(unit=f"{i}.service") for i in range(201)]))
        for value in (True, 0, 65536, "4317", None):
            item = web_listener("example", 4317)
            item["service_port"] = value
            invalid.append(report(item))
        item = listener()
        item["service_port"] = 4317
        invalid.append(report(item))
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.projects.apply_report(payload)
        self.assertEqual(self.projects.snapshot(), self.initial)
        self.notify.assert_not_called()

    def test_report_disk_failure_rolls_back_without_notifying(self):
        with mock.patch(WRITER, side_effect=OSError("full")):
            with self.assertRaises(OSError):
                self.projects.apply_report(report(listener()))
        self.assertEqual(self.projects.snapshot(), self.initial)
        self.notify.assert_not_called()

    def test_notification_uses_one_background_subprocess_for_all_new_names(self):
        self.projects.apply_report(report(
            listener(), listener(unit="another.service", name="Another", port=19002),
            listener(Host.MAC, "com.jacobr.fieldwork", "Fieldwork", 4317),
        ))
        self.join_notifications()
        self.run.assert_called_once_with(
            [common.NTFY_SEND, "Van dashboard", "2 new hosted projects: Example, Another"],
            capture_output=True, text=True, timeout=20, check=False, input=None,
        )
        self.assertEqual(len(self.notification_threads), 1)
        self.assertTrue(self.notification_threads[0].daemon)
        self.assertIs(self.process_threads[0], self.notification_threads[0])
        self.assertIsNot(self.process_threads[0], threading.current_thread())

    def test_notification_failure_is_logged_without_failing_report(self):
        for failure in (OSError("missing sender"), subprocess.TimeoutExpired("sender", 20)):
            with self.subTest(failure=failure), self.assertLogs(project_module.__name__):
                self.run.side_effect = failure
                thread = self.real_notify(["Example"])
                thread.join(2)
                self.assertFalse(thread.is_alive())
        self.run.side_effect = None
        self.run.return_value = mock.Mock(returncode=1)
        with self.assertLogs(project_module.__name__):
            result = self.projects.apply_report(report(listener()))
            self.join_notifications()
        self.assertEqual(result["created"], 1)
        self.assertEqual(self.named("Example")["status"], Status.NEW)


def link(kind, url):
    return {"kind": kind, "label": {"local": "This Mac", "lan": "LAN", "ts": "Tailscale", "web": "Web"}[kind],
            "url": url}


def v2_entry(entry_id, name, host, key, links, status=Status.LISTED, source=Source.MANUAL):
    return {
        "id": entry_id, "key": key, "name": name, "description": f"{name} description",
        "host": host, "links": links, "status": status, "source": source, "live": True,
        "first_seen": SCANNED_AT if key else None, "last_seen": SCANNED_AT if key else None,
        "updated_at": SCANNED_AT,
    }


def pi_links(port):
    return [link("lan", f"http://vanpi.lan:{port}/"), link("ts", f"http://100.82.91.76:{port}/")]


class HostedProjectDuplicateTests(HostedProjectFixture):
    def test_units_sharing_an_address_share_one_row_until_both_stop(self):
        tailscale = listener(Host.PI, "van-telemetry-web-tailscale.service", "Tailscale unit", 8765)
        lan = listener(Host.PI, "van-telemetry-web.service", "LAN unit", 8765)
        self.assertEqual(self.projects.apply_report(report(tailscale, lan)), counts(adopted=2))
        row = self.named("Vehicle Telemetry")
        self.assertEqual(keys(row), [tailscale["key"], lan["key"]])
        self.assertEqual(len(self.projects.snapshot()), len(self.initial))
        self.projects.apply_report(report(lan, scanned_at=LATER))
        row = self.named("Vehicle Telemetry")
        self.assertTrue(row["live"])
        self.assertEqual([service["live"] for service in row["services"]], [False, True])
        self.assertEqual(self.projects.apply_report(report(hosts=[Host.PI]))["stale"], 1)
        self.assertFalse(self.named("Vehicle Telemetry")["live"])

    def test_new_units_sharing_an_address_create_one_row_named_for_the_shorter_unit(self):
        tailscale = listener(unit="demo-tailscale.service", name="Demo Tailscale")
        plain = listener(unit="demo.service", name="Demo")
        self.assertEqual(self.projects.apply_report(report(tailscale, plain)), counts(created=1))
        row = self.named("Demo")
        self.assertEqual(row["id"], plain["key"])
        self.assertEqual(keys(row), [tailscale["key"], plain["key"]])
        self.notify.assert_called_once_with(["Demo"])

    def test_web_route_joins_the_mac_app_on_its_upstream_port(self):
        mac = listener(Host.MAC, "com.jacobr.gizmo", "Gizmo", 4400)
        web = web_listener("gadget", 4400)
        self.assertEqual(self.projects.apply_report(report(web, mac)), counts(created=1, adopted=1))
        row = self.named("Gizmo")
        self.assertEqual(keys(row), [mac["key"], web["key"]])
        self.assertEqual([link["kind"] for link in row["links"]], ["local", "web"])
        self.notify.assert_called_once_with(["Gizmo"])
        before = self.projects.snapshot()
        with mock.patch(WRITER) as write:
            self.assertEqual(self.projects.apply_report(report(mac, web, scanned_at=LATER)), counts())
            write.assert_not_called()
        self.assertEqual(self.projects.snapshot(), before)

    def test_same_name_on_mac_and_pi_shares_a_row(self):
        pi = listener(Host.PI, "notes.service", "Notes", 19005)
        mac = listener(Host.MAC, "com.jacobr.notes", "Notes", 4500)
        self.projects.apply_report(report(pi))
        self.assertEqual(self.projects.apply_report(report(mac)), counts(adopted=1))
        self.assertEqual(keys(self.named("Notes")), [mac["key"], pi["key"]])

    def test_unreviewed_duplicate_folds_in_but_a_kept_one_stays_separate(self):
        web = web_listener("gadget", 4400)
        mac = listener(Host.MAC, "com.jacobr.gizmo", "Gizmo", 4400)
        for status in (None, Status.HIDDEN, Status.LISTED):
            with self.subTest(status=status):
                self.state.data["hosted_projects"] = copy.deepcopy(self.initial)
                self.projects.apply_report(report(web))
                if status:
                    self.projects.update(web["key"], {"status": status})
                self.projects.apply_report(report(mac, hosts=[Host.MAC]))
                result = self.projects.apply_report(report(mac, web, scanned_at=LATER))
                names = {entry["name"] for entry in self.projects.snapshot()}
                if status == Status.LISTED:
                    self.assertEqual(result, counts())
                    self.assertTrue({"Gadget", "Gizmo"} <= names)
                else:
                    self.assertEqual(result, counts(merged=1))
                    self.assertNotIn("Gadget", names)
                    self.assertEqual(keys(self.named("Gizmo")), [mac["key"], web["key"]])

    def test_schema_2_duplicates_fold_into_their_original_rows_on_the_next_scans(self):
        legacy = [
            v2_entry("seed-telemetry", "Vehicle Telemetry", Host.PI,
                     "pi:van-telemetry-web-tailscale.service", pi_links(8765)),
            v2_entry("pi:van-telemetry-web.service", "Van Telemetry Web", Host.PI,
                     "pi:van-telemetry-web.service", pi_links(8765), Status.NEW, Source.SCANNED),
            v2_entry("seed-visual-guides", "Visual Guides", Host.PI, "pi:visual-guides.service",
                     [link("local", "http://127.0.0.1:8792/"), *pi_links(8791)]),
            v2_entry("mac:com.jacobr.visual-guides", "Visual Guides (This Mac)", Host.MAC,
                     "mac:com.jacobr.visual-guides", [link("local", "http://127.0.0.1:8792/")],
                     Status.NEW, Source.SCANNED),
            v2_entry("seed-fieldwork", "Fieldwork", Host.MAC, "mac:com.jacobr.fieldwork",
                     [link("local", "http://127.0.0.1:4317/")]),
            v2_entry("web:fieldwork", "Fieldwork (lionfeather.dev)", Host.WEB, "web:fieldwork",
                     [link("web", "https://fieldwork.lionfeather.dev/")], Status.NEW, Source.SCANNED),
            v2_entry("custom-inventory", "Electronics inventory", Host.MAC,
                     "mac:com.jacobr.electronics-inventory", [link("local", "http://127.0.0.1:8767")]),
            v2_entry("web:inventory", "Inventory", Host.WEB, "web:inventory",
                     [link("web", "https://inventory.lionfeather.dev/")], Status.HIDDEN, Source.SCANNED),
            v2_entry("web:critter", "Critter", Host.WEB, "web:critter",
                     [link("web", "https://critter.lionfeather.dev/")], Status.NEW, Source.SCANNED),
            v2_entry("seed-always-remote", "Always Remote", Host.PI, None, pi_links(8777)),
        ]
        Path(self.path).write_text(json.dumps({
            "hosted_projects": legacy, "hosted_projects_schema": 2,
        }), encoding="utf-8")
        projects = HostedProjectStore(StateStore(self.path))
        self.assertEqual(keys(projects.snapshot()[0]), [])  # Always Remote, unclaimed
        pi_report = report(
            listener(Host.PI, "van-telemetry-web-tailscale.service", "Tailscale", 8765),
            listener(Host.PI, "van-telemetry-web.service", "Van Telemetry Web", 8765),
            listener(Host.PI, "visual-guides.service", "Visual Guides", 8791),
        )
        self.assertEqual(projects.apply_report(pi_report), counts(merged=1))
        mac_web_report = report(
            listener(Host.MAC, "com.jacobr.electronics-inventory", "Electronics Inventory", 8767),
            listener(Host.MAC, "com.jacobr.fieldwork", "Fieldwork", 4317),
            listener(Host.MAC, "com.jacobr.visual-guides", "Visual Guides", 8792),
            web_listener("critter", 4329), web_listener("fieldwork", 4317),
            web_listener("inventory", 8767),
        )
        self.assertEqual(projects.apply_report(mac_web_report), counts(merged=3))
        rows = {entry["name"]: entry for entry in projects.snapshot()}
        self.assertEqual(sorted(rows), [
            "Always Remote", "Critter", "Electronics inventory", "Fieldwork",
            "Vehicle Telemetry", "Visual Guides",
        ])
        self.assertEqual(keys(rows["Vehicle Telemetry"]), [
            "pi:van-telemetry-web-tailscale.service", "pi:van-telemetry-web.service",
        ])
        self.assertEqual(keys(rows["Visual Guides"]), [
            "mac:com.jacobr.visual-guides", "pi:visual-guides.service",
        ])
        self.assertEqual(keys(rows["Fieldwork"]), ["mac:com.jacobr.fieldwork", "web:fieldwork"])
        self.assertEqual(keys(rows["Electronics inventory"]), [
            "mac:com.jacobr.electronics-inventory", "web:inventory",
        ])
        self.assertEqual([link["url"] for link in rows["Electronics inventory"]["links"]], [
            "http://127.0.0.1:8767", "https://inventory.lionfeather.dev/",
        ])
        for name in ("Vehicle Telemetry", "Visual Guides", "Fieldwork", "Electronics inventory"):
            original = next(entry for entry in legacy if entry["name"] == name)
            for field in ("id", "description", "status", "source", "host"):
                self.assertEqual(rows[name][field], original[field], (name, field))
        self.assertEqual(rows["Critter"]["status"], Status.NEW)
        self.assertTrue(all(entry["live"] for entry in rows.values()))
        self.notify.assert_not_called()
        before = projects.snapshot()
        with mock.patch(WRITER) as write:
            projects.apply_report({**pi_report, "scanned_at": LATER})
            projects.apply_report({**mac_web_report, "scanned_at": LATER})
            write.assert_not_called()
        self.assertEqual(projects.snapshot(), before)


class HostedProjectRouteTests(HostedProjectFixture):
    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.object(runtime, "hosted_projects", self.projects))
        self.client = dashboard_app.test_client()
        self.url = "/api/hosted-projects"
        self.update_url = self.url + "/seed-fieldwork"
        self.scan_url = self.url + "/scan"

    def test_form_routes_validate_and_preserve_csrf_protection(self):
        self.assertEqual(self.client.get(self.url).get_json()["projects"], self.initial)
        self.assertEqual(self.client.post(self.url, json={"name": "Example"}).status_code, 400)
        self.assertEqual(self.client.post(self.url, data={
            "name": "Example", "web": "javascript:bad",
        }).status_code, 400)
        data = {"name": "Example", "web": "https://example.com"}
        self.assertEqual(self.client.post(self.url, data=data, headers={
            "Origin": "https://other.example", "X-Van-Dashboard": "1",
        }).status_code, 403)
        response = self.client.post(self.url, data=data, headers={
            "Origin": "http://localhost", "X-Van-Dashboard": "1",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(self.url).get_json()["projects"], response.get_json()["projects"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_update_all_editable_fields_replaces_links_and_returns_sorted_list(self):
        response = self.client.post(self.update_url, json={
            "name": "A renamed project", "description": "My edit", "status": Status.HIDDEN,
            "links": [{"kind": "web", "label": "Site", "url": "https://example.com/"}],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        saved = self.named("A renamed project")
        self.assertEqual(saved["status"], Status.HIDDEN)
        self.assertEqual(saved["description"], "My edit")
        self.assertEqual(saved["links"], [{"kind": "web", "label": "Site", "url": "https://example.com/"}])
        listed = response.get_json()["projects"]
        self.assertEqual(listed[0], saved)
        self.assertEqual(self.client.get(self.url).get_json()["projects"], listed)
        self.assertEqual(self.client.get(self.url).headers["Cache-Control"], "no-store")
        self.assertEqual(HostedProjectStore(StateStore(self.path)).snapshot(), listed)

    def test_update_rejects_unknown_fields_invalid_values_and_unknown_ids(self):
        invalid = [{"status": Status.NEW}, {"status": []}, {"host": Host.PI}, {"key": "pi:fake"},
                   {"name": ""}, {"name": "x" * 81}, {"name": 1}, {"name": "bad\x00name"},
                   {"description": "x" * 201}, {"description": False}, {"links": []},
                   {"links": [{}]}, {"links": [{"kind": "web", "url": "file:///etc/passwd"}]},
                   {"links": [{"kind": "unknown", "url": "https://example.com"}]},
                   {"links": [{"kind": "web", "url": "https://user:secret@example.com"}]},
                   {"links": [{"kind": "web", "url": "https://example.com:99999"}]},
                   {"links": [{"kind": "web", "url": "https://example.com", "extra": True}]},
                   {"links": [{"kind": "web", "url": "https://example.com", "label": []}]},
                   {"links": [{"kind": "web", "url": "https://example.com"}] * 5}]
        for payload in invalid:
            with self.subTest(payload=payload):
                response = self.client.post(self.update_url, json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.get_json()["ok"])
                self.assertEqual(self.projects.snapshot(), self.initial)
        self.assertEqual(self.client.post(self.url + "/missing", json={"status": Status.LISTED}).status_code, 404)
        conflict = self.client.post(self.update_url, json={"name": "audiobooks"})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(self.projects.snapshot(), self.initial)

    def test_json_endpoints_reject_non_json_invalid_json_and_non_object_bodies(self):
        for url in (self.update_url, self.scan_url):
            for body, content_type in (("{}", "text/plain"), ("status=hidden", "application/x-www-form-urlencoded"),
                                       ("{broken", "application/json"), ("null", "application/json"),
                                       ("[]", "application/json")):
                with self.subTest(url=url, body=body):
                    self.assertEqual(self.client.post(url, data=body, content_type=content_type).status_code, 400)
        self.assertEqual(self.projects.snapshot(), self.initial)

    def test_json_csrf_checks_origin_and_custom_header_and_allows_cli(self):
        for url, payload in ((self.update_url, {"status": Status.HIDDEN}),
                             (self.scan_url, report(hosts=[Host.PI]))):
            for headers in ({"Origin": "https://other.example", "X-Van-Dashboard": "1"},
                            {"Origin": "http://localhost"}, {"Referer": "https://other.example/page"}):
                with self.subTest(url=url, headers=headers):
                    self.assertEqual(self.client.post(url, json=payload, headers=headers).status_code, 403)
            response = self.client.post(url, json=payload, headers={
                "Origin": "http://localhost", "X-Van-Dashboard": "1",
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.client.post(url, json=payload).status_code, 200)

    def test_scan_returns_counts_and_get_includes_new_hidden_and_listed(self):
        response = self.client.post(self.scan_url, json=report(listener()))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True, **counts(created=1)})
        self.client.post(self.update_url, json={"status": Status.HIDDEN})
        projects = self.client.get(self.url).get_json()["projects"]
        self.assertEqual({entry["status"] for entry in projects}, set(Status))
        names = [entry["name"] for entry in projects]
        self.assertEqual(names, sorted(names, key=str.casefold))

    def test_scan_rejects_one_bad_listener_without_changing_state(self):
        invalid = listener(unit="invalid.service", name="Bad", port=19002)
        invalid["description"] = "x" * 201
        self.assertEqual(self.client.post(self.scan_url, json=report(listener(), invalid)).status_code, 400)
        self.assertEqual(self.projects.snapshot(), self.initial)

    def test_save_errors_return_503_and_leave_directory_unchanged(self):
        requests = [(self.url, {"data": {"name": "Example", "web": "https://example.com"}}),
                    (self.update_url, {"json": {"status": Status.HIDDEN}}),
                    (self.scan_url, {"json": report(listener())})]
        for url, kwargs in requests:
            with self.subTest(url=url), mock.patch(WRITER, side_effect=OSError("full")):
                self.assertEqual(self.client.post(url, **kwargs).status_code, 503)
                self.assertEqual(self.projects.snapshot(), self.initial)
        self.notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
