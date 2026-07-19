import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import unittest


POLICYCTL = Path(__file__).resolve().parents[1] / "pi" / "scripts" / "policyctl"


class PolicyctlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.policy = self.root / "config" / "policy.json"
        self.mconf = self.root / "mconf"
        self.mconf_last = self.root / "mconf_last"
        self.starconf = self.root / "starconf"
        self.mountinfo = self.root / "mountinfo"
        self.mountinfo.write_text(
            "24 1 179:2 / / rw,relatime - ext4 /dev/mmcblk0p2 rw\n",
            encoding="utf-8",
        )
        self.pgrep = self.root / "pgrep"
        self.pgrep.write_text(
            "#!/bin/bash\n"
            '[[ "$#" == 2 && "$1" == -x && "$2" == qbittorrent-nox ]] || exit 2\n'
            'exit "${TEST_QBIT_EXIT:-1}"\n',
            encoding="utf-8",
        )
        self.pgrep.chmod(0o700)
        self.environment = {
            **os.environ,
            "VANPI_POLICY_PATH": str(self.policy),
            "VANPI_POLICY_LOCK_PATH": str(self.root / "config" / "policy.lock"),
            "VANPI_POLICY_MCONF": str(self.mconf),
            "VANPI_POLICY_MCONF_LAST": str(self.mconf_last),
            "VANPI_POLICY_STARCONF": str(self.starconf),
            "VANPI_POLICY_MOUNTINFO_PATH": str(self.mountinfo),
            "VANPI_POLICY_PGREP": str(self.pgrep),
        }

    def tearDown(self):
        self.temporary.cleanup()

    def run_policyctl(self, *arguments, check=True, environment=None):
        result = subprocess.run(
            [str(POLICYCTL), *arguments],
            capture_output=True,
            text=True,
            env=environment or self.environment,
            check=False,
        )
        if check and result.returncode:
            self.fail(f"policyctl failed: {result.stderr or result.stdout}")
        return result

    def read_policy(self):
        return json.loads(self.policy.read_text(encoding="utf-8"))

    def expected_runtime(self, mounted=(), running=False):
        return {
            "disks_mounted": bool(mounted),
            "mounted_disk_labels": list(mounted),
            "qbittorrent_running": running,
        }

    def test_migrate_uses_new_always_available_defaults(self):
        result = self.run_policyctl("--no-reconcile", "--json", "migrate")
        self.assertEqual(
            json.loads(result.stdout),
            {
                "version": 1,
                "disks_enabled": True,
                "torrents_enabled": True,
                "allow_starlink_torrents": False,
                "runtime": self.expected_runtime(),
            },
        )
        self.assertEqual(stat.S_IMODE(self.policy.stat().st_mode), 0o600)
        self.assertEqual(
            self.run_policyctl("read").stdout.strip(),
            "1 1 0",
        )
        self.assertNotIn(
            "runtime", json.loads(self.run_policyctl("--json", "read").stdout)
        )

    def test_migrate_prefers_requested_state_saved_in_mconf_last(self):
        self.mconf.mkdir()
        (self.mconf / "nodisk").touch()
        self.mconf_last.mkdir()
        (self.mconf_last / "notorrent").touch()
        (self.mconf_last / "startor").touch()

        self.run_policyctl("--no-reconcile", "migrate")

        self.assertEqual(
            self.read_policy(),
            {
                "version": 1,
                "disks_enabled": True,
                "torrents_enabled": False,
                "allow_starlink_torrents": True,
            },
        )

    def test_migrate_understands_legacy_starconf_permission(self):
        self.starconf.mkdir()
        self.run_policyctl("--no-reconcile", "migrate")
        self.assertTrue(self.read_policy()["allow_starlink_torrents"])

        self.policy.unlink()
        (self.starconf / "notor").touch()
        self.run_policyctl("--no-reconcile", "migrate")
        self.assertFalse(self.read_policy()["allow_starlink_torrents"])

    def test_mconf_startor_convention_wins_over_stale_starconf(self):
        self.mconf.mkdir()
        self.starconf.mkdir()

        self.run_policyctl("--no-reconcile", "migrate")

        self.assertFalse(self.read_policy()["allow_starlink_torrents"])

    def test_update_changes_one_field_and_requests_reconciliation(self):
        self.run_policyctl("--no-reconcile", "migrate")
        request_marker = self.root / "requested"
        request_command = self.root / "request-policy"
        request_command.write_text(
            f"#!/bin/bash\ntouch {request_marker}\n", encoding="utf-8"
        )
        request_command.chmod(0o700)
        environment = {
            **self.environment,
            "VANPI_POLICY_REQUEST_COMMAND": str(request_command),
        }

        result = self.run_policyctl(
            "--json", "torrents", "off", environment=environment
        )

        self.assertTrue(request_marker.exists())
        value = json.loads(result.stdout)
        self.assertTrue(value["disks_enabled"])
        self.assertFalse(value["torrents_enabled"])
        self.assertFalse(value["allow_starlink_torrents"])
        self.assertEqual(value["runtime"], self.expected_runtime())

        request_marker.unlink()
        self.run_policyctl("reconcile", environment=environment)
        self.assertTrue(request_marker.exists())

    def test_status_separates_requested_permission_from_runtime(self):
        self.run_policyctl("--no-reconcile", "migrate")
        self.run_policyctl("--no-reconcile", "torrents", "on")
        self.mountinfo.write_text(
            "24 1 179:2 / / rw,relatime - ext4 /dev/mmcblk0p2 rw\n"
            "30 24 8:1 / /mnt/EXFAT512 rw - exfat /dev/sda rw\n"
            "31 24 8:97 / /mnt/movingparts rw - ext4 /dev/sdg1 rw\n"
            "32 24 8:49 / /mnt/mbp2tbkup rw - ext4 /dev/sdd1 rw\n",
            encoding="utf-8",
        )
        environment = {**self.environment, "TEST_QBIT_EXIT": "0"}

        status = json.loads(
            self.run_policyctl("--json", "status", environment=environment).stdout
        )

        self.assertTrue(status["torrents_enabled"])
        self.assertEqual(
            status["runtime"],
            self.expected_runtime(("movingparts", "mbp2tbkup"), running=True),
        )

    def test_runtime_managed_labels_match_disk_lifecycle_policy(self):
        disk_policy = (
            POLICYCTL.parent / "disk_policy.sh"
        ).read_text(encoding="utf-8")
        match = re.search(r"HDD_LABELS=\(\s*(.*?)\s*\)", disk_policy, re.DOTALL)
        self.assertIsNotNone(match)
        labels = tuple(match.group(1).split())
        mount_lines = [
            "24 1 179:2 / / rw,relatime - ext4 /dev/mmcblk0p2 rw"
        ]
        mount_lines.extend(
            f"{index} 24 8:{index} / /mnt/{label} rw - ext4 /dev/sd{index} rw"
            for index, label in enumerate(labels, start=30)
        )
        self.mountinfo.write_text("\n".join(mount_lines) + "\n", encoding="utf-8")
        self.run_policyctl("--no-reconcile", "migrate")

        status = json.loads(self.run_policyctl("--json", "status").stdout)

        self.assertEqual(status["runtime"]["mounted_disk_labels"], list(labels))

    def test_runtime_probe_errors_fail_instead_of_reporting_false_state(self):
        self.run_policyctl("--no-reconcile", "migrate")
        environment = {**self.environment, "TEST_QBIT_EXIT": "2"}

        result = self.run_policyctl("--json", "status", check=False, environment=environment)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot inspect qBittorrent runtime state", result.stderr)

    def test_invalid_policy_fails_without_guessing(self):
        self.policy.parent.mkdir(parents=True)
        self.policy.write_text('{"disks_enabled": true}\n', encoding="utf-8")

        result = self.run_policyctl("read", check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing policy fields", result.stderr)
        self.assertEqual(
            self.policy.read_text(encoding="utf-8"), '{"disks_enabled": true}\n'
        )


if __name__ == "__main__":
    unittest.main()
