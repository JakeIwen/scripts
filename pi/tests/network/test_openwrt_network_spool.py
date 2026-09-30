"""Bounded raw-spool rotation and static receiver privacy/compatibility checks."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/openwrt-logging/rotate_network_log.py"
spec = importlib.util.spec_from_file_location("rotate_network_log", SCRIPT)
rotation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rotation)


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


if __name__ == "__main__":
    unittest.main()
