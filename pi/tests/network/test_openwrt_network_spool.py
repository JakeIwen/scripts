"""Bounded raw-spool rotation and static receiver privacy/compatibility checks."""
import importlib.util
import grp
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
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

    def test_legacy_receiver_format_is_preserved(self):
        config = (ROOT / "scripts/openwrt-logging/30-openwrt-dendelion.conf").read_text()
        self.assertIn('%timegenerated:::date-rfc3339% %fromhost-ip% %hostname% %syslogtag%%msg%', config)
        self.assertIn('name="timereported"', config)
        self.assertIn('name="timegenerated"', config)
        self.assertEqual(config.count('rotation.sizeLimit="1048576"'), 2)
        self.assertNotIn('file="/var/log/', config)

    def test_tmpfiles_inherits_adm_without_unsafe_file_ownership_transition(self):
        config = (ROOT / "tmpfiles.d/vanpi-network.conf").read_text()
        rows = [line.split() for line in config.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        directory = next(row for row in rows if row[0] == "d" and row[1] == "/run/vanpi-network/spool")
        self.assertTrue(int(directory[2], 8) & stat.S_ISGID)
        self.assertEqual(directory[3:5], ["root", "adm"])
        # The Pi-owned parent/root-owned spool transition makes tmpfiles refuse
        # z on the files. A guarded installer operation repairs the exact ring.
        self.assertTrue(all(row[0] == "d" for row in rows))

    def test_checker_preserves_receiver_identity_instead_of_replacing_with_reader(self):
        source = (ROOT / "scripts/openwrt-logging/30-openwrt-dendelion.conf").read_text()
        config = checker.receiver_config(source, Path("/fixture"), 45678)
        self.assertEqual(config.count('fileOwner="root"'), 2)
        self.assertEqual(config.count('fileGroup="adm"'), 2)
        self.assertIn("$FileCreateMode 0640", config)
        self.assertIn("$FileGroup adm", config)
        self.assertEqual(config.count('rotation.sizeLimit="4096"'), 2)
        self.assertIn('address="127.0.0.1" port="45678"', config)
        self.assertNotIn('file="/run/vanpi-network/spool/', config)
        self.assertIn('rotation.sizeLimitCommand="/fixture/rotate.py json"', config)

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

    def test_checker_reads_as_separate_pi_identity_with_adm_supplement(self):
        reader = SimpleNamespace(pw_uid=1000, pw_gid=1000)
        result = subprocess.CompletedProcess([], 0, json.dumps({"uid": 1000, "gid": 1000, "groups": [4], "files": []}), "")
        with mock.patch.object(checker.subprocess, "run", return_value=result) as run:
            snapshot = checker.reader_snapshot([Path("/fixture/network.jsonl")], reader, 4)
        self.assertEqual(snapshot["uid"], 1000)
        self.assertEqual(run.call_args.kwargs["user"], 1000)
        self.assertEqual(run.call_args.kwargs["group"], 1000)
        self.assertEqual(run.call_args.kwargs["extra_groups"], [4])

    def test_checker_rejects_same_user_nonroot_execution(self):
        with mock.patch.object(checker.sys, "platform", "linux"), \
                mock.patch.object(checker.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(RuntimeError, "sudo"):
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
