"""LaunchAgent lifecycle tests for the finite BTT Guard monitor job."""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import pwd
import stat
import subprocess
import tempfile
import time
from types import SimpleNamespace
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
        self.installed = (
            self.root
            / "home"
            / "Library"
            / "LaunchAgents"
            / f"{service.LABEL}.plist"
        )
        self.default_installed = service.INSTALLED
        installed_patch = patch.object(service, "INSTALLED", self.installed)
        installed_patch.start()
        self.addCleanup(installed_patch.stop)

    def heartbeat(self, status="healthy", completed_at=None):
        return {
            "schema_version": service.SCHEMA_VERSION,
            "run_id": str(uuid.uuid4()),
            "pid": 1234,
            "uid": os.getuid(),
            "root": str(self.root),
            "completed_at": time.time() if completed_at is None else completed_at,
            "audit_status": status,
        }

    def write_installed(self, config=None, mode=0o644):
        self.installed.parent.mkdir(parents=True, exist_ok=True)
        configuration = config or service.service_configuration(self.state, self.root)
        self.installed.write_bytes(plistlib.dumps(configuration))
        self.installed.chmod(mode)

    def assert_no_sudo(self, calls):
        for item in calls:
            command = item.args[0]
            self.assertNotEqual(command[0], "/usr/bin/sudo")
            self.assertNotIn("/usr/bin/sudo", command)

    def test_configuration_is_periodic_gui_agent_not_persistent_server(self):
        config = service.service_configuration(self.state, self.root)
        self.assertEqual(config["Label"], "com.jacobr.btt-guard")
        self.assertNotIn("UserName", config)
        self.assertEqual(config["LimitLoadToSessionType"], "Aqua")
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
        self.assertEqual(
            config["EnvironmentVariables"]["HOME"],
            pwd.getpwuid(os.getuid()).pw_dir,
        )
        self.assertGreater(config["StartInterval"], 0)
        self.assertTrue(config["RunAtLoad"])
        self.assertNotIn("KeepAlive", config)
        self.assertEqual(config["Umask"], 0o077)

    def test_target_and_default_path_use_login_owner(self):
        uid = os.getuid()
        expected_home = Path(pwd.getpwuid(uid).pw_dir)
        self.assertEqual(service.DOMAIN, f"gui/{uid}")
        self.assertEqual(service.TARGET, f"gui/{uid}/{service.LABEL}")
        self.assertEqual(
            self.default_installed,
            expected_home / "Library" / "LaunchAgents" / f"{service.LABEL}.plist",
        )

    @patch("macbook.bettertouchtool.btt_guard.service.subprocess.run")
    def test_prepare_writes_private_linted_payload_without_live_change(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        result = service.run_service("prepare", self.state, self.root)
        payload = Path(result["payload"])
        config = plistlib.loads(payload.read_bytes())

        self.assertTrue(result["prepared"])
        self.assertFalse(result["installed"])
        self.assertEqual(result["service"], service.TARGET)
        self.assertEqual(config["LimitLoadToSessionType"], "Aqua")
        self.assertNotIn("UserName", config)
        self.assertEqual(stat.S_IMODE(payload.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        for name in service.LOG_NAMES:
            self.assertEqual(stat.S_IMODE((self.state / name).stat().st_mode), 0o600)
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["/usr/bin/plutil", "-lint"])
        self.assertFalse(any("launchctl" in str(item) for item in command))
        self.assertNotIn("/usr/bin/sudo", command)

    @patch("macbook.bettertouchtool.btt_guard.service.subprocess.run")
    def test_symlinked_installed_plist_is_rejected_before_control(self, run):
        self.installed.parent.mkdir(parents=True)
        target = self.installed.parent / "foreign.plist"
        target.write_bytes(
            plistlib.dumps(service.service_configuration(self.state, self.root))
        )
        self.installed.symlink_to(target)

        with self.assertRaisesRegex(service.ServiceError, "symlinked"):
            service.run_service("stop", self.state, self.root)
        run.assert_not_called()

    @patch("macbook.bettertouchtool.btt_guard.service.subprocess.run")
    def test_wrong_installed_plist_mode_is_rejected_before_control(self, run):
        self.write_installed(mode=0o600)

        with self.assertRaisesRegex(service.ServiceError, "mode 0644"):
            service.run_service("stop", self.state, self.root)
        run.assert_not_called()

    @patch("macbook.bettertouchtool.btt_guard.service.subprocess.run")
    def test_foreign_installed_plist_owner_is_rejected_before_control(self, run):
        self.write_installed()
        fake_info = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o644,
            st_uid=os.getuid() + 1,
        )

        with patch.object(Path, "lstat", return_value=fake_info):
            with self.assertRaisesRegex(service.ServiceError, "invoking user"):
                service.run_service("stop", self.state, self.root)
        run.assert_not_called()

    @patch("macbook.bettertouchtool.btt_guard.service.subprocess.run")
    def test_workspace_mismatch_is_rejected_before_control(self, run):
        config = service.service_configuration(self.state, self.root)
        config["WorkingDirectory"] = "/private/tmp/another-workspace"
        self.write_installed(config)

        with self.assertRaisesRegex(service.ServiceError, "another workspace or user"):
            service.run_service("stop", self.state, self.root)
        run.assert_not_called()

    def test_owned_regular_mode_0644_registration_is_accepted(self):
        self.write_installed()
        with (
            patch.object(service, "_job", return_value={"loaded": False, "state": None}),
            patch.object(service, "_enabled", return_value=True),
        ):
            result = service.run_service("status", self.state, self.root)

        self.assertTrue(result["installed"])
        self.assertTrue(result["configuration_ok"])
        self.assertTrue(result["ownership_ok"])

    def test_status_accepts_idle_job_and_separates_audit_outcome(self):
        private_dir(self.state)
        config = service.service_configuration(self.state, self.root)
        expected_keys = {
            "service",
            "installed",
            "configuration_ok",
            "ownership_ok",
            "startup_enabled",
            "job",
            "heartbeat",
            "audit_status",
            "supervised",
            "interval_seconds",
            "logs",
        }
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
                self.assertEqual(set(result), expected_keys)
                self.assertEqual(result["service"], service.TARGET)
                self.assertTrue(result["supervised"])
                self.assertEqual(result["audit_status"], audit_status)
                self.assertEqual(result["job"]["state"], "not running")

    def test_status_inspects_gui_domain_and_target(self):
        config = service.service_configuration(self.state, self.root)
        commands = []

        def fake_run(command, **kwargs):
            commands.append(command)
            if command == ["/bin/launchctl", "print", service.TARGET]:
                return subprocess.CompletedProcess(command, 0, "state = not running\n", "")
            if command == ["/bin/launchctl", "print-disabled", service.DOMAIN]:
                return subprocess.CompletedProcess(
                    command, 0, "disabled services = {\n}\n", ""
                )
            raise AssertionError(f"unexpected command: {command}")

        with (
            patch.object(service, "_read_installed", return_value=config),
            patch.object(
                service,
                "_heartbeat",
                return_value={"fresh": False, "audit_status": None},
            ),
            patch.object(service.subprocess, "run", side_effect=fake_run),
        ):
            result = service.run_service("status", self.state, self.root)

        self.assertTrue(result["job"]["loaded"])
        self.assertEqual(
            commands,
            [
                ["/bin/launchctl", "print", service.TARGET],
                ["/bin/launchctl", "print-disabled", service.DOMAIN],
            ],
        )

    def test_enabled_parses_boolean_and_word_styles(self):
        config = service.service_configuration(self.state, self.root)
        cases = {
            "true": False,
            "disabled": False,
            "false": True,
            "enabled": True,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                output = f'disabled services = {{\n\t"{service.LABEL}" => {value}\n}}\n'
                with (
                    patch.object(service, "_read_installed", return_value=config),
                    patch.object(
                        service, "_job", return_value={"loaded": False, "state": None}
                    ),
                    patch.object(
                        service,
                        "_heartbeat",
                        return_value={"fresh": False, "audit_status": None},
                    ),
                    patch.object(service, "_run", return_value=output) as run,
                ):
                    result = service.run_service("status", self.state, self.root)
                self.assertEqual(result["startup_enabled"], expected)
                run.assert_called_once_with(
                    ["/bin/launchctl", "print-disabled", service.DOMAIN]
                )

    def test_enabled_treats_absent_label_as_enabled(self):
        config = service.service_configuration(self.state, self.root)
        with (
            patch.object(service, "_read_installed", return_value=config),
            patch.object(service, "_job", return_value={"loaded": False, "state": None}),
            patch.object(
                service,
                "_heartbeat",
                return_value={"fresh": False, "audit_status": None},
            ),
            patch.object(service, "_run", return_value="disabled services = {\n}\n"),
        ):
            result = service.run_service("status", self.state, self.root)
        self.assertTrue(result["startup_enabled"])

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
            with self.assertRaises(service.ServiceError):
                service.run_service("install", self.state, self.root)
        run.assert_not_called()

    def test_install_validates_registration_before_loading_gui_agent(self):
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
        self.assertEqual(
            run.call_args_list,
            [
                call(
                    [
                        "/usr/bin/install",
                        "-m",
                        "644",
                        str(payload),
                        str(self.installed),
                    ]
                ),
                call(["/bin/launchctl", "enable", service.TARGET]),
                call(
                    [
                        "/bin/launchctl",
                        "bootstrap",
                        service.DOMAIN,
                        str(self.installed),
                    ]
                ),
            ],
        )
        self.assert_no_sudo(run.call_args_list)

    def test_start_and_stop_use_gui_target_without_sudo(self):
        config = service.service_configuration(self.state, self.root)
        with (
            patch.object(service, "_read_installed", return_value=config),
            patch.object(service, "_job", return_value={"loaded": False, "state": None}),
            patch.object(service, "_run") as run,
        ):
            service.run_service("start", self.state, self.root)
        self.assertEqual(
            run.call_args_list,
            [
                call(["/bin/launchctl", "enable", service.TARGET]),
                call(
                    [
                        "/bin/launchctl",
                        "bootstrap",
                        service.DOMAIN,
                        str(self.installed),
                    ]
                ),
            ],
        )
        self.assert_no_sudo(run.call_args_list)

        with (
            patch.object(service, "_read_installed", return_value=config),
            patch.object(service, "_job", return_value={"loaded": True, "state": None}),
            patch.object(service, "_run") as run,
        ):
            service.run_service("stop", self.state, self.root)
        self.assertEqual(
            run.call_args_list,
            [
                call(["/bin/launchctl", "disable", service.TARGET]),
                call(["/bin/launchctl", "bootout", service.TARGET]),
            ],
        )
        self.assert_no_sudo(run.call_args_list)

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
        run.assert_called_once_with(["/bin/launchctl", "kickstart", service.TARGET])
        self.assertNotIn("-k", run.call_args.args[0])
        self.assert_no_sudo(run.call_args_list)

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
                call(["/bin/launchctl", "disable", service.TARGET]),
                call(["/bin/launchctl", "bootout", service.TARGET]),
                call(["/bin/rm", "--", str(self.installed)]),
            ],
        )
        self.assert_no_sudo(run.call_args_list)


if __name__ == "__main__":
    unittest.main()
