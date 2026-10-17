import io
import json
import os
import plistlib
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from pi.apps.van_dashboard import hosted_project_scan as scan


PI_UNITS = """\
audiobooks.service                  loaded active running Audiobook server (Sonos playback + resume + web UI on :8787)
home-assistant.service              loaded active running Home Assistant
loopback-demo.service               loaded active running Loopback demo
multi-port.service                  loaded active running Multiple ports
ssh.service                         loaded active running OpenBSD Secure Shell server
"""

PI_SHOW = """\
MainPID=1211
Id=audiobooks.service
Description=Audiobook server (Sonos playback + resume + web UI on :8787)

MainPID=1221
Id=home-assistant.service
Description=Home Assistant

MainPID=4242
Id=loopback-demo.service
Description=Loopback-only demo

MainPID=5252
Id=multi-port.service
Description=Multiple ports

MainPID=971
Id=ssh.service
Description=OpenBSD Secure Shell server
"""

PI_SS = """\
LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:((\"sshd\",pid=971,fd=3))
LISTEN 0 128 127.0.0.1:3210 0.0.0.0:* users:((\"python3\",pid=4242,fd=3))
LISTEN 0 128 127.0.0.1:1000 0.0.0.0:* users:((\"python3\",pid=5252,fd=3))
LISTEN 0 128 0.0.0.0:9000 0.0.0.0:* users:((\"python3\",pid=5252,fd=4))
LISTEN 0 128 0.0.0.0:8000 0.0.0.0:* users:((\"python3\",pid=5252,fd=5))
LISTEN 0 128 0.0.0.0:8787 0.0.0.0:* users:((\"python3\",pid=1211,fd=5),(\"python3\",pid=1211,fd=3))
LISTEN 0 128 192.168.6.103:1400 0.0.0.0:* users:((\"hass\",pid=1221,fd=28))
LISTEN 0 128 0.0.0.0:8123 0.0.0.0:* users:((\"hass\",pid=1221,fd=11))
"""

MAC_LAUNCHCTL = """\
system/com.jacobr.visual-guides = {
    active count = 1
    state = running
    pid = 777
    last exit code = (never exited)
}
"""

MAC_LSOF = """\
p777
f19
n127.0.0.1:8792
"""


class PiParsingTests(unittest.TestCase):
    def setUp(self):
        self.listeners = scan.build_pi_listeners(
            PI_UNITS, PI_SHOW, PI_SS, "100.82.91.76\n"
        )
        self.by_key = {listener["key"]: listener for listener in self.listeners}

    def test_matches_other_user_service_by_systemd_main_pid(self):
        home_assistant = self.by_key["pi:home-assistant.service"]
        self.assertEqual(home_assistant["name"], "Home Assistant")
        self.assertEqual(home_assistant["description"], "Home Assistant")
        self.assertEqual(home_assistant["links"], [
            {"kind": "lan", "url": "http://vanpi.lan:8123/"},
            {"kind": "ts", "url": "http://100.82.91.76:8123/"},
        ])

    def test_excludes_loopback_only_and_denylisted_ports(self):
        self.assertNotIn("pi:loopback-demo.service", self.by_key)
        self.assertNotIn("pi:ssh.service", self.by_key)

    def test_chooses_lowest_qualifying_port(self):
        listener = self.by_key["pi:multi-port.service"]
        self.assertEqual(listener["links"][0]["url"], "http://vanpi.lan:8000/")

    def test_real_audiobook_sample_is_parsed(self):
        listener = self.by_key["pi:audiobooks.service"]
        self.assertEqual(listener["name"], "Audiobooks")
        self.assertEqual(listener["links"][0]["url"], "http://vanpi.lan:8787/")

    def test_tailscale_fallback_handles_command_failure_output(self):
        listeners = scan.build_pi_listeners(PI_UNITS, PI_SHOW, PI_SS, "not available")
        audiobook = next(item for item in listeners if item["key"] == "pi:audiobooks.service")
        self.assertEqual(
            audiobook["links"][1]["url"],
            "http://{}:8787/".format(scan.DEFAULT_TAILSCALE_IP),
        )


class MacParsingTests(unittest.TestCase):
    def test_plist_label_name_and_package_description(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = os.path.join(folder, "visual-guides")
            os.mkdir(workspace)
            with open(os.path.join(workspace, "package.json"), "w", encoding="utf-8") as handle:
                json.dump({"description": "Interactive visual wiring guides.\n"}, handle)
            plist_path = os.path.join(folder, "com.jacobr.visual-guides.plist")
            with open(plist_path, "wb") as handle:
                plistlib.dump({
                    "Label": "com.jacobr.visual-guides",
                    "WorkingDirectory": workspace,
                }, handle)
            jobs = scan.read_launchd_jobs(
                daemon_pattern=os.path.join(folder, "*.plist"),
                agent_pattern=os.path.join(folder, "missing", "*.plist"),
                uid=501,
            )
        listeners = scan.build_mac_listeners(
            jobs,
            {"com.jacobr.visual-guides": MAC_LAUNCHCTL},
            {777: MAC_LSOF},
        )
        self.assertEqual(listeners, [{
            "key": "mac:com.jacobr.visual-guides",
            "host": "mac",
            "name": "Visual Guides",
            "description": "Interactive visual wiring guides.",
            "links": [{"kind": "local", "url": "http://127.0.0.1:8792/"}],
        }])

    def test_job_without_listener_is_skipped(self):
        jobs = [{"label": "com.jacobr.no-port", "domain": "system", "description": ""}]
        output = "system/com.jacobr.no-port = {\n    pid = 99\n}\n"
        self.assertEqual(scan.build_mac_listeners(jobs, {"com.jacobr.no-port": output}, {}), [])


class WebParsingTests(unittest.TestCase):
    def test_maps_every_edge_app_without_name_deduplication(self):
        config = json.dumps({"apps": [
            {"name": "critter", "note": "Critter app"},
            {"name": "fieldwork", "note": "Cloud authority"},
            {"name": "inventory", "note": "Public inventory"},
        ]})
        listeners = scan.build_web_listeners(config)
        self.assertEqual(
            [listener["key"] for listener in listeners],
            ["web:critter", "web:fieldwork", "web:inventory"],
        )
        self.assertEqual(listeners[1], {
            "key": "web:fieldwork",
            "host": "web",
            "name": "Fieldwork",
            "description": "Cloud authority",
            "links": [{"kind": "web", "url": "https://fieldwork.lionfeather.dev/"}],
        })


    def test_reports_the_explicit_upstream_port_of_each_edge_app(self):
        config = json.dumps({"apps": [
            {"name": "critter", "service": "http://127.0.0.1:4329"},
            {"name": "default-port", "service": "http://127.0.0.1"},
            {"name": "bad-port", "service": "http://127.0.0.1:99999"},
            {"name": "no-service"},
        ]})
        ports = {listener["key"]: listener.get("service_port")
                 for listener in scan.build_web_listeners(config)}
        self.assertEqual(ports, {
            "web:bad-port": None, "web:critter": 4329, "web:default-port": None, "web:no-service": None,
        })


class ProbeTests(unittest.TestCase):
    def recording_probe(self, accepted):
        asked = []

        def probe(url):
            asked.append(url)
            return url in accepted
        return probe, asked

    def test_pi_unit_uses_its_first_port_serving_a_page_at_the_bound_address(self):
        probe, asked = self.recording_probe({"http://127.0.0.1:9000/", "http://192.168.6.103:1400/"})
        listeners = {item["key"]: item for item in scan.build_pi_listeners(
            PI_UNITS, PI_SHOW, PI_SS, "100.82.91.76\n", probe,
        )}
        self.assertEqual(sorted(listeners), ["pi:home-assistant.service", "pi:multi-port.service"])
        self.assertEqual(listeners["pi:home-assistant.service"]["links"][0]["url"], "http://vanpi.lan:1400/")
        self.assertEqual(listeners["pi:multi-port.service"]["links"][0]["url"], "http://vanpi.lan:9000/")
        self.assertEqual(asked, [
            "http://127.0.0.1:8787/",
            "http://127.0.0.1:8123/", "http://192.168.6.103:1400/",
            "http://127.0.0.1:8000/", "http://127.0.0.1:9000/",
        ])

    def test_mac_job_without_a_page_is_skipped_and_probes_reach_its_bind_address(self):
        jobs = [{"label": "com.jacobr.visual-guides", "domain": "system", "description": ""}]
        lsof = "p777\nf19\nn*:8792\nf20\nn[::1]:8793\n"
        probe, asked = self.recording_probe({"http://[::1]:8793/"})
        listeners = scan.build_mac_listeners(jobs, {"com.jacobr.visual-guides": MAC_LAUNCHCTL}, {777: lsof}, probe)
        self.assertEqual(asked, ["http://127.0.0.1:8792/", "http://[::1]:8793/"])
        self.assertEqual(listeners[0]["links"], [{"kind": "local", "url": "http://127.0.0.1:8793/"}])
        probe, _ = self.recording_probe(set())
        self.assertEqual(scan.build_mac_listeners(
            jobs, {"com.jacobr.visual-guides": MAC_LAUNCHCTL}, {777: lsof}, probe,
        ), [])

    def test_edge_app_without_a_page_is_skipped(self):
        config = json.dumps({"apps": [{"name": "critter"}, {"name": "fieldwork"}]})
        probe, asked = self.recording_probe({"https://fieldwork.lionfeather.dev/"})
        listeners = scan.build_web_listeners(config, probe)
        self.assertEqual([listener["key"] for listener in listeners], ["web:fieldwork"])
        self.assertEqual(asked, ["https://critter.lionfeather.dev/", "https://fieldwork.lionfeather.dev/"])

    def test_only_a_final_200_counts_as_a_web_page(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                status, location = {
                    "/page": (200, None), "/moved": (302, "/page"),
                    "/missing": (404, None), "/forbidden": (403, None), "/empty": (204, None),
                }[self.path]
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:{}".format(server.server_address[1])
        results = {path: scan.serves_web_page(base + path, 2)
                   for path in ("/page", "/moved", "/missing", "/forbidden", "/empty")}
        self.assertEqual(results, {
            "/page": True, "/moved": True, "/missing": False, "/forbidden": False, "/empty": False,
        })
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            closed_port = unused.getsockname()[1]
        self.assertFalse(scan.serves_web_page("http://127.0.0.1:{}/".format(closed_port), 2))
        self.assertEqual(scan.probe_host("0.0.0.0"), "127.0.0.1")
        self.assertEqual(scan.probe_host("::"), "[::1]")
        self.assertEqual(scan.probe_host("fe80::1%eth0"), "[fe80::1]")


class ReportTests(unittest.TestCase):
    def test_report_shape_matches_contract(self):
        listener = {
            "key": "mac:com.jacobr.fieldwork",
            "host": "mac",
            "name": "Fieldwork",
            "description": "Job-search workspace.",
            "links": [{"kind": "local", "url": "http://127.0.0.1:4317/"}],
        }
        report = scan.build_report(
            ["web", "mac", "mac"], [listener], "2026-10-16T18:15:00Z"
        )
        self.assertEqual(report, {
            "hosts": ["mac", "web"],
            "scanned_at": "2026-10-16T18:15:00Z",
            "listeners": [listener],
        })

    def test_same_inputs_produce_identical_json(self):
        listeners = scan.build_web_listeners(json.dumps({
            "apps": [{"name": "critter", "note": "Critter app"}]
        }))
        first = scan.encode_report(scan.build_report(
            ["web"], listeners, "2026-10-16T18:15:00Z"
        ))
        second = scan.encode_report(scan.build_report(
            ["web"], listeners, "2026-10-16T18:15:00Z"
        ))
        self.assertEqual(first, second)


class StubResponse:
    def __init__(self, status, body):
        self.status = status
        self.body = body
        self.closed = False

    def read(self):
        return self.body

    def close(self):
        self.closed = True


class MainExitCodeTests(unittest.TestCase):
    def run_main(self, opener):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(scan, "scan_hosts", return_value=[]):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = scan.main(
                    ["--host", "web", "--post", "http://dashboard.example"],
                    opener=opener,
                )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_post_success_exits_zero(self):
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return StubResponse(200, b'{"ok":true,"created":0}')

        code, stdout, stderr = self.run_main(opener)
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr.count("\n"), 1)
        self.assertIn("result=posted", stderr)
        self.assertTrue(requests[0][0].full_url.endswith("/api/hosted-projects/scan"))
        self.assertEqual(requests[0][1], 10.0)

    def test_post_refusal_exits_two(self):
        def opener(_request, timeout):
            self.assertEqual(timeout, 10.0)
            return StubResponse(200, b'{"ok":false}')

        code, _stdout, stderr = self.run_main(opener)
        self.assertEqual(code, 2)
        self.assertIn("result=post-failed", stderr)

    def test_post_http_refusal_exits_two(self):
        def opener(_request, timeout):
            self.assertEqual(timeout, 10.0)
            return StubResponse(503, b'{"ok":true}')

        code, _stdout, stderr = self.run_main(opener)
        self.assertEqual(code, 2)
        self.assertIn("result=post-failed", stderr)

    def test_post_unreachable_exits_two(self):
        def opener(_request, timeout):
            self.assertEqual(timeout, 10.0)
            raise urllib.error.URLError("offline")

        code, _stdout, stderr = self.run_main(opener)
        self.assertEqual(code, 2)
        self.assertIn("result=post-failed", stderr)

    def test_scan_failure_exits_one(self):
        stderr = io.StringIO()
        with mock.patch.object(scan, "scan_hosts", side_effect=scan.ScanError("broken")):
            with redirect_stderr(stderr):
                code = scan.main(["--host", "pi", "--print"])
        self.assertEqual(code, 1)
        self.assertIn("result=scan-failed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
