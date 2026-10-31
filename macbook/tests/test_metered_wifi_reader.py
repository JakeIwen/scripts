"""Exercise the public reader against the helper's JSON/process boundary, without live Wi-Fi."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from macbook.metered.wifi_helper import reader


class WiFiReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.app = Path(self.temporary.name) / "MeteredWiFi.app"
        executable = self.app / "Contents/MacOS/MeteredWiFi"
        executable.parent.mkdir(parents=True)
        executable.touch()
        self.record = {"interface": "en7", "ssid": " MiXeD Café ",
                       "bssid": "ab:12:cd:34:ef:56", "status": "ok", "detail": "authorized"}

    def read_output(self, data):
        def launch(argv, **kwargs):
            self.assertEqual(argv[:5], ["/usr/bin/open", "-g", "-j", "-n", "-W"])
            self.assertEqual(argv[5:8], [str(self.app), "--args", "--output"])
            self.assertNotIn("--request-authorization", argv)
            self.assertTrue(kwargs["check"])
            self.assertEqual(kwargs["timeout"], 10.0)
            Path(argv[-1]).write_bytes(data)
            return subprocess.CompletedProcess(argv, 0)
        with patch.object(reader.subprocess, "run", side_effect=launch):
            return reader.read_wifi(app_path=self.app)

    def read_record(self, **changes):
        return self.read_output(json.dumps(dict(self.record, **changes)).encode())

    def test_fresh_identity_preserves_exact_ssid_and_discovered_interface(self):
        self.assertEqual(self.read_record(), self.record)

    def test_all_non_success_statuses_clear_any_stale_identity(self):
        for status in ("not_authorized", "no_wifi", "error"):
            with self.subTest(status=status):
                result = self.read_record(status=status, detail=status)
                self.assertEqual(result, {"interface": "en7", "ssid": None, "bssid": None,
                                          "status": status, "detail": status})

    def test_no_wifi_can_have_no_interface(self):
        result = self.read_record(status="no_wifi", interface=None)
        self.assertEqual(result["status"], "no_wifi")
        self.assertIsNone(result["interface"])

    def test_redacted_identity_never_counts_as_success(self):
        for change in ({"ssid": "<redacted>"}, {"bssid": "<REDACTED>"},
                       {"bssid": "00:00:00:00:00:00"}):
            with self.subTest(change=change):
                result = self.read_record(**change)
                self.assertEqual(result["status"], "not_authorized")
                self.assertIsNone(result["ssid"])
                self.assertIsNone(result["bssid"])

    def test_malformed_or_incomplete_fields_are_errors(self):
        changes = ({"status": "maybe"}, {"status": []}, {"interface": None},
                   {"interface": ""}, {"interface": 7}, {"ssid": None}, {"ssid": ""},
                   {"ssid": True}, {"bssid": "not-a-mac"}, {"bssid": None},
                   {"bssid": 123}, {"detail": None}, {"detail": ""})
        for change in changes:
            with self.subTest(change=change):
                result = self.read_record(**change)
                self.assertEqual(result["status"], "error")
                self.assertIsNone(result["ssid"])

    def test_missing_fields_are_errors(self):
        for field in self.record:
            with self.subTest(field=field):
                record = dict(self.record)
                del record[field]
                self.assertEqual(self.read_output(json.dumps(record).encode())["status"], "error")

    def test_invalid_json_and_oversized_outputs_are_errors(self):
        for data in (b"{", b"[]", b"null", b"\xff", b" " * (reader.MAX_OUTPUT_BYTES + 1)):
            with self.subTest(data=data[:20]):
                self.assertEqual(self.read_output(data)["status"], "error")

    def test_missing_bundle_does_not_launch_anything(self):
        with patch.object(reader.subprocess, "run") as launch:
            result = reader.read_wifi(app_path=self.app.parent / "missing.app")
        launch.assert_not_called()
        self.assertEqual(result["detail"], "helper_not_built")

    def test_launch_failures_and_timeouts_are_errors(self):
        errors = ((subprocess.TimeoutExpired("open", 2), "helper_timeout"),
                  (subprocess.CalledProcessError(1, "open"), "helper_launch_failed"),
                  (OSError("cannot launch"), "helper_output_unavailable:OSError"))
        for error, detail in errors:
            with self.subTest(detail=detail):
                with patch.object(reader.subprocess, "run", side_effect=error):
                    result = reader.read_wifi(app_path=self.app, timeout=2)
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["detail"], detail)

    def test_successful_launch_without_output_is_not_success(self):
        with patch.object(reader.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            result = reader.read_wifi(app_path=self.app)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["detail"], "helper_output_unavailable:FileNotFoundError")


if __name__ == "__main__":
    unittest.main()
