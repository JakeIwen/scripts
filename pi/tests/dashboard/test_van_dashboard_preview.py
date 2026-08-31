import ast
import http.client
import json
import os
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pi.apps.van_dashboard import react_dashboard_preview as preview


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class RecordingBackendHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._respond()

    def do_HEAD(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._respond(send_body=False)

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._respond()

    def do_PUT(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._respond()

    def do_PATCH(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._respond()

    def do_DELETE(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._respond()

    def log_message(self, format_string, *args):
        return

    def _respond(self, *, send_body=True):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        self.server.requests.append(  # type: ignore[attr-defined]
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers.items()),
                "body": body,
            }
        )

        if self.path == "/api/teapot":
            status = 418
            payload = b'{"ok":false,"message":"short and stout"}'
            response_headers = (
                ("Cache-Control", "no-store"),
                ("Connection", "X-Upstream-Hop"),
                ("X-Upstream-Hop", "remove-me"),
                ("X-End-To-End", "preserved"),
            )
        else:
            status = 200
            payload = json.dumps(
                {"ok": True, "method": self.command, "path": self.path},
                separators=(",", ":"),
            ).encode("utf-8")
            response_headers = (("Cache-Control", "no-store"),)

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for name, value in response_headers:
            self.send_header(name, value)
        self.end_headers()
        if send_body:
            self.wfile.write(payload)


class RecordingBackend(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        self.requests = []
        super().__init__(("127.0.0.1", 0), RecordingBackendHandler)


class ServerThread:
    def __init__(self, server):
        self.server = server
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self.server

    def __exit__(self, exc_type, exc_value, traceback):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class PreviewServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.build_root = Path(self.temporary_directory.name) / "dist"
        (self.build_root / "assets").mkdir(parents=True)
        (self.build_root / "index.html").write_text(
            "<!doctype html><main>React preview</main>", encoding="utf-8"
        )
        (self.build_root / "assets" / "app-deadbeef.js").write_text(
            "window.previewLoaded = true;", encoding="utf-8"
        )

    def start_preview(self, backend_port, *, allowed_mutations=frozenset()):
        config = preview.PreviewConfig(
            build_root=self.build_root,
            backend_port=backend_port,
            backend_timeout=1,
            allowed_mutations=allowed_mutations,
            log_requests=False,
        )
        return preview.create_server(("127.0.0.1", 0), config)

    def request(self, server, method, path, *, body=None, headers=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=3
        )
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            payload = response.read()
            return response.status, dict(response.getheaders()), payload
        finally:
            connection.close()

    def test_static_build_spa_health_and_cache_policy(self):
        backend = RecordingBackend()
        preview_server = self.start_preview(backend.server_port)

        with ServerThread(preview_server):
            status, headers, body = self.request(preview_server, "GET", "/")
            self.assertEqual(status, 200)
            self.assertEqual(body, b"<!doctype html><main>React preview</main>")
            self.assertEqual(headers["Cache-Control"], "no-store")

            status, headers, body = self.request(
                preview_server, "GET", "/assets/app-deadbeef.js"
            )
            self.assertEqual(status, 200)
            self.assertEqual(body, b"window.previewLoaded = true;")
            self.assertEqual(
                headers["Cache-Control"],
                "public, max-age=31536000, immutable",
            )
            self.assertTrue(headers["Content-Type"].startswith("text/javascript"))

            status, headers, body = self.request(
                preview_server, "HEAD", "/settings/network"
            )
            self.assertEqual(status, 200)
            self.assertEqual(body, b"")
            self.assertEqual(headers["Cache-Control"], "no-store")

            status, _, body = self.request(preview_server, "GET", "/healthz")
            self.assertEqual(status, 200)
            self.assertEqual(
                json.loads(body),
                {"ok": True, "service": "van-dashboard-preview"},
            )

            status, _, body = self.request(
                preview_server, "GET", "/assets/missing.js"
            )
            self.assertEqual(status, 404)
            self.assertFalse(json.loads(body)["ok"])

        self.assertEqual(backend.requests, [])
        backend.server_close()

    def test_get_api_preserves_browser_origin_headers_and_query(self):
        backend = RecordingBackend()
        preview_server = self.start_preview(backend.server_port)
        headers = {
            "Host": "vanpi.lan:8790",
            "Origin": "http://vanpi.lan:8790",
            "Referer": "http://vanpi.lan:8790/",
            "X-Van-Dashboard": "1",
            "Connection": "X-Remove-Me",
            "X-Remove-Me": "not-end-to-end",
        }

        with ServerThread(backend), ServerThread(preview_server):
            status, response_headers, body = self.request(
                preview_server,
                "GET",
                "/api/status?active=1",
                headers=headers,
            )

        self.assertEqual(status, 200)
        self.assertEqual(response_headers["Cache-Control"], "no-store")
        self.assertEqual(
            json.loads(body),
            {"ok": True, "method": "GET", "path": "/api/status?active=1"},
        )
        self.assertEqual(len(backend.requests), 1)
        recorded = backend.requests[0]
        self.assertEqual(recorded["path"], "/api/status?active=1")
        self.assertEqual(recorded["headers"]["Host"], "vanpi.lan:8790")
        self.assertEqual(
            recorded["headers"]["Origin"], "http://vanpi.lan:8790"
        )
        self.assertEqual(
            recorded["headers"]["Referer"], "http://vanpi.lan:8790/"
        )
        self.assertEqual(recorded["headers"]["X-Van-Dashboard"], "1")
        self.assertNotIn("X-Remove-Me", recorded["headers"])

    def test_api_prefix_is_exact_and_unreviewed_mutations_stay_disabled(self):
        backend = RecordingBackend()
        preview_server = self.start_preview(backend.server_port)

        with ServerThread(backend), ServerThread(preview_server):
            status, _, body = self.request(preview_server, "GET", "/apiary")
            self.assertEqual(status, 200)
            self.assertIn(b"React preview", body)

            status, headers, body = self.request(
                preview_server,
                "POST",
                "/api/system-power",
                body="action=reboot&confirmation=reboot",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Van-Dashboard": "1",
                },
            )
            self.assertEqual(status, 405)
            self.assertEqual(headers["Allow"], "GET, HEAD")
            self.assertIn("not enabled", json.loads(body)["message"])

        self.assertEqual(backend.requests, [])

    def test_exact_mutation_allowlist_forwards_body_but_no_neighbor_route(self):
        backend = RecordingBackend()
        preview_server = self.start_preview(
            backend.server_port,
            allowed_mutations=frozenset({("POST", "/api/lights/power")}),
        )
        body = b"target=cab&value=true"
        headers = {
            "Host": "vanpi.lan:8790",
            "Origin": "http://vanpi.lan:8790",
            "Referer": "http://vanpi.lan:8790/",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Van-Dashboard": "1",
        }

        with ServerThread(backend), ServerThread(preview_server):
            status, _, _ = self.request(
                preview_server,
                "POST",
                "/api/lights/power?refresh=1",
                body=body,
                headers=headers,
            )
            self.assertEqual(status, 200)

            status, _, response_body = self.request(
                preview_server,
                "POST",
                "/api/lights/brightness",
                body=b"entity=light.test&brightness=10",
                headers=headers,
            )
            self.assertEqual(status, 405)
            self.assertIn("not enabled", json.loads(response_body)["message"])

        self.assertEqual(len(backend.requests), 1)
        recorded = backend.requests[0]
        self.assertEqual(recorded["method"], "POST")
        self.assertEqual(recorded["path"], "/api/lights/power?refresh=1")
        self.assertEqual(recorded["body"], body)
        self.assertEqual(recorded["headers"]["Host"], "vanpi.lan:8790")
        self.assertEqual(recorded["headers"]["X-Van-Dashboard"], "1")

    def test_allowlisted_mutation_still_requires_dashboard_control_header(self):
        backend = RecordingBackend()
        preview_server = self.start_preview(
            backend.server_port,
            allowed_mutations=frozenset({("POST", "/api/lights/power")}),
        )

        with ServerThread(backend), ServerThread(preview_server):
            status, _, body = self.request(
                preview_server,
                "POST",
                "/api/lights/power",
                body=b"target=cab&value=true",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["message"], "dashboard control header missing")
        self.assertEqual(backend.requests, [])

    def test_cross_origin_preflight_is_not_proxied_or_granted_cors(self):
        backend = RecordingBackend()
        preview_server = self.start_preview(
            backend.server_port,
            allowed_mutations=frozenset({("POST", "/api/lights/power")}),
        )

        with ServerThread(backend), ServerThread(preview_server):
            status, headers, body = self.request(
                preview_server,
                "OPTIONS",
                "/api/lights/power",
                headers={
                    "Origin": "https://example.invalid",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "X-Van-Dashboard",
                },
            )

        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "GET, HEAD")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertFalse(json.loads(body)["ok"])
        self.assertEqual(backend.requests, [])

    def test_upstream_status_and_end_to_end_headers_are_preserved(self):
        backend = RecordingBackend()
        preview_server = self.start_preview(backend.server_port)

        with ServerThread(backend), ServerThread(preview_server):
            status, headers, body = self.request(
                preview_server, "GET", "/api/teapot"
            )

        self.assertEqual(status, 418)
        self.assertEqual(json.loads(body)["message"], "short and stout")
        self.assertEqual(headers["X-End-To-End"], "preserved")
        self.assertNotIn("X-Upstream-Hop", headers)

    def test_backend_failure_is_a_no_store_json_502(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        unused_port = probe.getsockname()[1]
        probe.close()
        preview_server = self.start_preview(unused_port)

        with ServerThread(preview_server):
            status, headers, body = self.request(
                preview_server, "GET", "/api/status"
            )

        self.assertEqual(status, 502)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertFalse(json.loads(body)["ok"])

    def test_symlink_outside_build_root_is_never_served(self):
        secret = Path(self.temporary_directory.name) / "secret.txt"
        secret.write_text("not part of the preview", encoding="utf-8")
        os.symlink(secret, self.build_root / "assets" / "escape.txt")
        backend = RecordingBackend()
        preview_server = self.start_preview(backend.server_port)

        with ServerThread(preview_server):
            status, _, body = self.request(
                preview_server, "GET", "/assets/escape.txt"
            )

        self.assertEqual(status, 404)
        self.assertNotIn(b"not part of the preview", body)
        backend.server_close()

    def test_mutation_allowlist_rejects_wildcards_queries_and_safe_methods(self):
        invalid_entries = (
            frozenset({("POST", "/not-api")}),
            frozenset({("POST", "/api/lights?all=1")}),
            frozenset({("GET", "/api/status")}),
        )
        for entries in invalid_entries:
            with self.subTest(entries=entries):
                with self.assertRaises(ValueError):
                    preview.PreviewConfig(
                        build_root=self.build_root,
                        allowed_mutations=entries,
                    )


class PreviewDeploymentContractTests(unittest.TestCase):
    def test_preview_server_imports_no_dashboard_controller(self):
        path = (
            REPOSITORY_ROOT
            / "pi"
            / "apps"
            / "van_dashboard"
            / "react_dashboard_preview.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)

        self.assertFalse(
            any(
                module.startswith("pi.apps.van_dashboard")
                or module.startswith("van_dashboard_")
                for module in imported_modules
            ),
            imported_modules,
        )
        self.assertEqual(
            preview.ALLOWED_MUTATIONS,
            frozenset(
                {
                    ("POST", "/api/speakers/select"),
                    ("POST", "/api/speakers/group"),
                    ("POST", "/api/speakers/volume"),
                    ("POST", "/api/speakers/mute"),
                    ("POST", "/api/speakers/group-volume"),
                    ("POST", "/api/speakers/group-mute"),
                    ("POST", "/api/speakers/transport"),
                    ("POST", "/api/lights/power"),
                    ("POST", "/api/lights/brightness"),
                    ("POST", "/api/lights/hue"),
                    ("POST", "/api/lights/color-temperature"),
                    ("POST", "/api/telemetry-service"),
                    ("POST", "/api/telemetry-voltage-check"),
                    ("POST", "/api/speedtest"),
                    ("POST", "/api/price-checks/add"),
                    ("POST", "/api/price-checks/edit"),
                    ("POST", "/api/price-checks/remove"),
                    ("POST", "/api/price-checks/mute"),
                    ("POST", "/api/price-checks/check"),
                    ("POST", "/api/price-checks/schedule"),
                    ("POST", "/api/price-checks/schedule/parse"),
                    ("POST", "/api/price-checks/searches/add"),
                    ("POST", "/api/price-checks/searches/remove"),
                    ("POST", "/api/price-checks/searches/dismiss"),
                    ("POST", "/api/price-checks/searches/check"),
                    ("POST", "/api/ignition-monitor/disable"),
                    ("POST", "/api/ignition-monitor/enable"),
                    ("POST", "/api/storage-policy"),
                    ("POST", "/api/disks/action"),
                    ("POST", "/api/usb-ports/discover"),
                    ("POST", "/api/usb-ports/action"),
                    ("POST", "/api/usb-ports/recover"),
                    ("POST", "/api/backups/clone"),
                    ("POST", "/api/backups/borg"),
                    ("POST", "/api/backups/exfat"),
                    ("POST", "/api/backups/borg/stop"),
                    ("POST", "/api/backups/exfat/stop"),
                    ("POST", "/api/ubnt-wifi/scan"),
                    ("POST", "/api/ubnt-wifi/connect"),
                    ("POST", "/api/ubnt-wifi/provision"),
                    ("POST", "/api/ubnt-wifi/resume"),
                    ("POST", "/api/ubnt-wifi/profile"),
                    ("POST", "/api/cop-alert"),
                    ("POST", "/api/starlink"),
                    ("POST", "/api/dashboard-service/restart"),
                    ("POST", "/api/system-power"),
                    ("POST", "/api/system-monitor/crash-analysis"),
                    ("POST", "/api/vonstar"),
                    ("POST", "/api/vonstar/access-state"),
                }
            ),
        )

    def test_service_is_independent_and_points_only_to_loopback_backend(self):
        unit = (
            REPOSITORY_ROOT / "pi" / "services" / "van-dashboard-preview.service"
        ).read_text(encoding="utf-8")

        self.assertIn("--port 8790", unit)
        self.assertIn("--backend-host 127.0.0.1 --backend-port 8788", unit)
        self.assertIn("User=pi", unit)
        self.assertIn(
            "ConditionPathExists=/home/pi/scripts/van-dashboard-preview/current/index.html",
            unit,
        )
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("ProtectHome=read-only", unit)
        self.assertNotIn("RuntimeDirectory=van-dashboard", unit)
        self.assertNotIn("PYTHONPATH", unit)
        self.assertNotIn("van_dashboard.py", unit)

    def test_deployer_is_clone_scoped_versioned_and_does_not_run_global_sync(self):
        deployer_path = (
            REPOSITORY_ROOT / "pi" / "deploy_van_dashboard_preview.sh"
        )
        deployer = deployer_path.read_text(encoding="utf-8")

        self.assertTrue(os.access(deployer_path, os.X_OK))
        self.assertIn('repo_root="$(cd -- "$script_dir/.." && pwd -P)"', deployer)
        self.assertIn("npm run build", deployer)
        self.assertIn("releases/$release_id", deployer)
        self.assertIn('replace_link "releases/$release_id" "$live_root/current"', deployer)
        self.assertIn('replace_link "$current_target" "$previous_link"', deployer)
        self.assertIn("--rollback", deployer)
        self.assertIn("http://127.0.0.1:8790/healthz", deployer)
        self.assertIn("http://127.0.0.1:8790/api/status", deployer)
        self.assertNotIn("sync_scripts.sh", deployer)
        self.assertNotIn("update_services.sh", deployer)


if __name__ == "__main__":
    unittest.main()
