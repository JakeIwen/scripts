"""Launchd lifecycle tests for the finite BTT Guard monitor job."""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import stat
import subprocess
import tempfile
import time
import unittest
from unittest.mock import call, patch
import uuid

from macbook.bettertouchtool.btt_guard import service
from macbook.bettertouchtool.btt_guard.storage import private_dir, write_json


class GuardServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / ".local" / "btt-guard"

    def heartbeat(self, status="healthy", completed_at=None):
        return {
            "schema_version": 1,
            "run_id": str(uuid.uuid4()),
            "pid": 1234,
            "uid": os.getuid(),
            "root": str(self.root),
            "completed_at": time.time() if completed_at is None else completed_at,
            "audit_status": status,
        }

    def test_configuration_is_periodic_one_shot_not_persistent_server(self):
        config = service.service_configuration(self.state, self.root)
        self.assertEqual(config["Label"], "com.jacobr.btt-guard")
        self.assertEqual(
            config["ProgramArguments"],
            [
                "/usr/bin/python3",
                "-B",
                "-m",
                "macbook.bettertouchtool.btt_guard",
                "--state-dir",
                str(self.state),
                "monitor",
                "--managed",
            ],
        )
        self.assertEqual(config["WorkingDirectory"], str(self.root))
        self.assertEqual(config["StartInterval"], 60)
        self.assertTrue(config["RunAtLoad"])
        self.assertNotIn("KeepAlive", config)
        self.assertEqual(config["Umask"], 0o077)

    @patch("macbook.bettertouchtool.btt_guard.service.subprocess.run")
    def test_prepare_writes_private_linted_payload_without_live_change(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        result = service.run_service("prepare", self.state, self.root)
        payload = Path(result["payload"])
        config = plistlib.loads(payload.read_bytes())

        self.assertTrue(result["prepared"])
        self.assertFalse(result["installed"])
        self.assertEqual(stat.S_IMODE(payload.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        self.assertEqual(config["StartInterval"], 60)
        for name in service.LOG_NAMES:
            self.assertEqual(stat.S_IMODE((self.state / name).stat().st_mode), 0o600)
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["/usr/bin/plutil", "-lint"])
        self.assertFalse(any("launchctl" in str(item) for item in command))
        self.assertNotIn("/usr/bin/sudo", command)

    @patch("macbook.bettertouchtool.btt_guard.service.subprocess.run")
    def test_privileged_command_sudos_only_the_requested_binary(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        service._run(
            ["/bin/launchctl", "enable", service.TARGET], privileged=True
        )
        command = run.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/sudo")
        self.assertIn("/bin/launchctl", command)
        self.assertNotIn("python", " ".join(command).lower())

    @patch("macbook.bettertouchtool.btt_guard.service.subprocess.run")
    def test_foreign_installed_plist_is_rejected_before_control(self, run):
        installed = self.root / "foreign.plist"
        installed.write_bytes(
            plistlib.dumps(service.service_configuration(self.state, self.root))
        )
        with patch.object(service, "INSTALLED", installed):
            with self.assertRaisesRegex(service.ServiceError, "root:wheel"):
                service.run_service("stop", self.state, self.root)
        run.assert_not_called()

    def test_workspace_mismatch_is_rejected(self):
        installed = self.root / "installed.plist"
        config = service.service_configuration(self.state, self.root)
        config["WorkingDirectory"] = "/private/tmp/another-workspace"
        installed.write_bytes(plistlib.dumps(config))
        fake_info = type(
            "FileInfo",
            (),
            {"st_mode": stat.S_IFREG | 0o644, "st_uid": 0, "st_gid": 0},
        )()
        with (
            patch.object(service, "INSTALLED", installed),
            patch.object(Path, "lstat", return_value=fake_info),
            patch.object(service, "_wheel_gid", return_value=0),
        ):
            with self.assertRaisesRegex(service.ServiceError, "another workspace"):
                service._read_installed(self.state, self.root)

    def test_status_accepts_idle_job_and_separates_audit_outcome(self):
        private_dir(self.state)
        config = service.service_configuration(self.state, self.root)
        for audit_status in ("healthy", "drift", "unavailable"):
            with self.subTest(audit_status=audit_status):
                write_json(self.state / service.HEARTBEAT_NAME, self.heartbeat(audit_status))
                with (
                    patch.object(service, "_read_installed", return_value=config),
                    patch.object(
                        service, "_job", return_value={"loaded": True, "state": "not running"}
                    ),
                    patch.object(service, "_enabled", return_value=True),
                ):
                    result = service.run_service("status", self.state, self.root)
                self.assertTrue(result["supervised"])
                self.assertEqual(result["audit_status"], audit_status)
                self.assertEqual(result["job"]["state"], "not running")

    def test_stale_heartbeat_is_not_supervised(self):
        private_dir(self.state)
        old = time.time() - service.HEARTBEAT_FRESHNESS_SECONDS - 1
        write_json(self.state / service.HEARTBEAT_NAME, self.heartbeat(completed_at=old))
        config = service.service_configuration(self.state, self.root)
        with (
            patch.object(service, "_read_installed", return_value=config),
            patch.object(service, "_job", return_value={"loaded": True, "state": None}),
            patch.object(service, "_enabled", return_value=True),
        ):
            result = service.run_service("status", self.state, self.root)
        self.assertFalse(result["supervised"])
        self.assertEqual(result["heartbeat"]["state"], "stale")

    def test_invalid_heartbeat_identity_is_not_supervised(self):
        private_dir(self.state)
        heartbeat = self.heartbeat()
        heartbeat["root"] = "/private/tmp/foreign"
        write_json(self.state / service.HEARTBEAT_NAME, heartbeat)
        config = service.service_configuration(self.state, self.root)
        with (
            patch.object(service, "_read_installed", return_value=config),
            patch.object(service, "_job", return_value={"loaded": True, "state": None}),
            patch.object(service, "_enabled", return_value=True),
        ):
            result = service.run_service("status", self.state, self.root)
        self.assertFalse(result["supervised"])
        self.assertEqual(result["heartbeat"]["state"], "invalid")

    def test_install_refuses_loaded_job_without_matching_registration(self):
        prepared = {
            "payload": str(self.state / f"{service.LABEL}.plist"),
        }
        with (
            patch.object(service, "_prepare", return_value=prepared),
            patch.object(service, "_read_installed", return_value=None),
            patch.object(
                service, "_job", return_value={"loaded": True, "state": "not running"}
            ),
            patch.object(service, "_run") as run,
        ):
            with self.assertRaisesRegex(service.ServiceError, "loaded service"):
                service.run_service("install", self.state, self.root)
        run.assert_not_called()

    def test_install_validates_registration_before_loading_it(self):
        payload = self.state / f"{service.LABEL}.plist"
        prepared = {"payload": str(payload)}
        config = service.service_configuration(self.state, self.root)
        with (
            patch.object(service, "_prepare", return_value=prepared),
            patch.object(service, "_read_installed", side_effect=[None, config]),
            patch.object(service, "_job", return_value={"loaded": False, "state": None}),
            patch.object(service, "_run") as run,
        ):
            result = service.run_service("install", self.state, self.root)
        self.assertTrue(result["installed"])
        self.assertEqual(run.call_count, 3)
        self.assertEqual(run.call_args_list[0].args[0][0], "/usr/bin/install")
        self.assertEqual(
            run.call_args_list[-1],
            call(
                ["/bin/launchctl", "bootstrap", "system", str(service.INSTALLED)],
                privileged=True,
            ),
        )

    def test_verify_kickstarts_without_killing_and_waits_for_new_run(self):
        config = service.service_configuration(self.state, self.root)
        before = {"fresh": True, "run_id": str(uuid.uuid4())}
        after = {
            "fresh": True,
            "run_id": str(uuid.uuid4()),
            "completed_at": 101.0,
            "audit_status": "drift",
        }
        with (
            patch.object(service, "_read_installed", return_value=config),
            patch.object(service, "_job", return_value={"loaded": True, "state": None}),
            patch.object(service, "_enabled", return_value=True),
            patch.object(service, "_heartbeat", side_effect=[before, after]),
            patch.object(service, "_run") as run,
            patch.object(service.time, "time", return_value=100.0),
            patch.object(service.time, "monotonic", side_effect=[0.0, 1.0]),
        ):
            result = service.run_service("verify", self.state, self.root)
        self.assertTrue(result["verified"])
        self.assertEqual(result["audit_status"], "drift")
        run.assert_called_once_with(
            ["/bin/launchctl", "kickstart", service.TARGET], privileged=True
        )
        self.assertNotIn("-k", run.call_args.args[0])

    def test_uninstall_removes_only_registration(self):
        private_dir(self.state)
        marker = self.state / "retained.json"
        write_json(marker, {"retained": True})
        config = service.service_configuration(self.state, self.root)
        with (
            patch.object(service, "_read_installed", return_value=config),
            patch.object(service, "_job", return_value={"loaded": True, "state": None}),
            patch.object(service, "_run") as run,
        ):
            result = service.run_service("uninstall", self.state, self.root)
        self.assertTrue(marker.exists())
        self.assertEqual(result["state_retained"], str(self.state))
        self.assertEqual(
            run.call_args_list,
            [
                call(["/bin/launchctl", "disable", service.TARGET], privileged=True),
                call(["/bin/launchctl", "bootout", service.TARGET], privileged=True),
                call(["/bin/rm", "--", str(service.INSTALLED)], privileged=True),
            ],
        )


if __name__ == "__main__":
    unittest.main()
