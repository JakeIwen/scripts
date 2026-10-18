"""Bounded raw-spool rotation and static receiver privacy/compatibility checks."""
import importlib.util
import grp
import os
from pathlib import Path
import pwd
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/openwrt-logging/rotate_network_log.py"
spec = importlib.util.spec_from_file_location("rotate_network_log", SCRIPT)
rotation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rotation)
checker_spec = importlib.util.spec_from_file_location(
    "check_openwrt_network_spool", Path(__file__).with_name("check_openwrt_network_spool.py"))
checker = importlib.util.module_from_spec(checker_spec)
checker_spec.loader.exec_module(checker)


def isolated_receiver_available():
    if sys.platform != "linux" or os.geteuid() != 0 or not Path("/usr/sbin/rsyslogd").exists():
        return False
    try:
        return pwd.getpwnam("pi").pw_uid != 0 and grp.getgrnam("adm").gr_gid != 0
    except KeyError:
        return False


class NetworkSpoolTests(unittest.TestCase):
    def test_burst_retains_three_previous_generations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "network.jsonl"
            for number in range(12):
                path.write_text(str(number))
                rotation.rotate(path)
            path.write_text("active")
            self.assertEqual(len(list(Path(directory).iterdir())), 4)
            self.assertEqual([(Path(directory) / f"network.jsonl.{i}").read_text()
                              for i in range(1, 4)], ["11", "10", "9"])

    def test_symlink_or_directory_destination_fails_before_mutation(self):
        for directory_target in (False, True):
            with self.subTest(directory_target=directory_target), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "network.jsonl"
                path.write_text("original")
                bad = Path(directory) / "network.jsonl.3"
                if directory_target:
                    bad.mkdir()
                else:
                    bad.symlink_to(path)
                with self.assertRaises(ValueError):
                    rotation.rotate(path)
                self.assertEqual(path.read_text(), "original")

    def test_checker_refuses_unisolated_helper_or_receiver_paths(self):
        for quote in ("'", '"'):
            source = f"RAM_SPOOL = Path({quote}/run/vanpi-network/spool{quote})\n"
            rendered = checker.isolated_rotation_helper(source, Path("/fixture/spool"))
            self.assertNotIn("/run/vanpi-network/spool", rendered)
            self.assertIn("/fixture/spool", rendered)
        with self.assertRaises(ValueError):
            checker.isolated_rotation_helper("RAM_SPOOL = Path('/unexpected/live/path')\n", Path("/fixture/spool"))
        source = (ROOT / "scripts/openwrt-logging/30-openwrt-dendelion.conf").read_text()
        with self.assertRaises(ValueError):
            checker.receiver_config(source.replace('file="/run/vanpi-network/spool/dendelion.log"',
                                                   'file="/unexpected/live/path"'), Path("/fixture"), 45678)

    def test_checker_rejects_same_user_nonroot_execution(self):
        with mock.patch.object(checker.sys, "platform", "linux"), \
                mock.patch.object(checker.os, "geteuid", return_value=1000):
            with self.assertRaises(RuntimeError):
                checker.check("unused-config", "unused-helper")

    @unittest.skipUnless(isolated_receiver_available(), "requires root Linux rsyslog with separate pi/adm reader")
    def test_linux_receiver_rotation_permissions_and_bounded_installer_repair(self):
        config = ROOT / "scripts/openwrt-logging/30-openwrt-dendelion.conf"
        helper = ROOT / "scripts/openwrt-logging/rotate_network_log.py"
        old = checker.check(config, helper, spool_mode=0o750, expect_readable=False,
                            tmpfiles_path=ROOT / "tmpfiles.d/vanpi-network.conf")
        fixed = checker.check(config, helper, spool_mode=0o2750, expect_readable=True)
        self.assertFalse(old["readable_after_rotation"])
        self.assertTrue(fixed["readable_after_rotation"])
        self.assertEqual(old["existing_file_repair"]["repaired_generations"], 8)
        self.assertTrue(old["existing_file_repair"]["contents_unchanged"])
        self.assertTrue(old["existing_file_repair"]["generations_outside_ring_untouched"])


if __name__ == "__main__":
    unittest.main()
