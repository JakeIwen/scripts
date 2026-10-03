import os
import tempfile
import threading
import unittest
from unittest import mock

from pi.apps.van_dashboard.van_dashboard_common import StateStore
from pi.apps.van_dashboard.van_dashboard_projects import HostedProjectStore, HostedProjectConflict


class HostedProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, "state.json")
        self.state = StateStore(self.path)
        self.projects = HostedProjectStore(self.state)

    def test_all_url_types_survive_restart_without_changing_other_state(self):
        self.state.set("sonos_device", "Front")
        fields = {"name": "Example", "local": "http://127.0.0.1:1234/",
                  "lan": "http://example.lan/", "ts": "http://100.64.0.2:1234/",
                  "web": "https://example.com/"}
        saved = self.projects.add(fields)
        self.assertEqual([x["kind"] for x in saved[0]["links"]], ["local", "lan", "ts", "web"])
        reloaded = StateStore(self.path)
        self.assertEqual(HostedProjectStore(reloaded).snapshot(), saved)
        self.assertEqual(reloaded.get("sonos_device"), "Front")
        saved.clear()
        self.assertEqual(len(self.projects.snapshot()), 1)

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
        self.assertEqual(self.projects.snapshot(), [])

    def test_same_submission_is_idempotent_but_conflicting_name_is_rejected(self):
        fields = {"name": "Example", "web": "https://example.com/"}
        self.assertEqual(self.projects.add(fields), self.projects.add(fields))
        with self.assertRaises(HostedProjectConflict):
            self.projects.add({"name": "EXAMPLE", "web": "https://other.example/"})
        self.assertEqual(len(self.projects.snapshot()), 1)

    def test_failed_disk_write_does_not_leave_a_phantom_project(self):
        with mock.patch("pi.apps.van_dashboard.van_dashboard_common.atomic_json_write", side_effect=OSError("full")):
            with self.assertRaises(OSError):
                self.projects.add({"name": "Example", "web": "https://example.com"})
        self.assertEqual(self.projects.snapshot(), [])

    def test_concurrent_additions_do_not_lose_entries(self):
        threads = [threading.Thread(target=self.projects.add, args=({"name": f"Project {i}", "web": f"https://example.com/{i}"},)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
        self.assertEqual(len(HostedProjectStore(StateStore(self.path)).snapshot()), 4)


class HostedProjectRouteTests(unittest.TestCase):
    def test_routes_validate_and_preserve_csrf_protection(self):
        from pi.apps.van_dashboard import van_dashboard as dashboard
        with tempfile.TemporaryDirectory() as folder:
            projects = HostedProjectStore(StateStore(os.path.join(folder, "state.json")))
            with mock.patch.object(dashboard, "hosted_projects", projects):
                client = dashboard.app.test_client()
                url = "/api/hosted-projects"
                self.assertEqual(client.get(url).get_json()["projects"], [])
                self.assertEqual(client.post(url, json={"name": "Example"}).status_code, 400)
                self.assertEqual(client.post(url, data={"name": "Example", "web": "javascript:bad"}).status_code, 400)
                data = {"name": "Example", "web": "https://example.com"}
                self.assertEqual(client.post(url, data=data, headers={"Origin": "https://other.example", "X-Van-Dashboard": "1"}).status_code, 403)
                response = client.post(url, data=data, headers={"Origin": "http://localhost", "X-Van-Dashboard": "1"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(client.get(url).get_json()["projects"], response.get_json()["projects"])
                self.assertEqual(response.headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
