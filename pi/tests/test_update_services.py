import os
import random
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

UPDATE_SERVICES = Path(__file__).resolve().parents[1] / "scripts" / "update_services.sh"

FAKE_SUDO = """#!/bin/sh
exec "$@"
"""
# Logs every call; reports units active so changed units are restarted.
FAKE_SYSTEMCTL = """#!/bin/sh
echo "systemctl $*" >> "$FAKE_LOG"
exit 0
"""
FAKE_TMPFILES = """#!/bin/sh
echo "systemd-tmpfiles $*" >> "$FAKE_LOG"
"""


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


class UpdateServicesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(self.cleanup_root)
        # The updater only accepts this exact staging-path shape.
        while True:
            self.stage = Path(f"/tmp/systemd-tmp.{random.randrange(10**8, 10**9)}")
            if not self.stage.exists():
                break
        self.addCleanup(self.cleanup_stage)

        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for name, body in (("sudo", FAKE_SUDO), ("systemctl", FAKE_SYSTEMCTL),
                           ("systemd-tmpfiles", FAKE_TMPFILES)):
            path = bin_dir / name
            path.write_text(body)
            path.chmod(0o755)
        self.log = self.root / "calls.log"
        self.live = {name: self.root / "live" / name for name in ("services", "scripts", "tmpfiles")}
        for path in self.live.values():
            path.mkdir(parents=True)
        self.env = dict(
            os.environ,
            PATH=f"{bin_dir}:/usr/bin:/bin",
            FAKE_LOG=str(self.log),
            UPDATE_SERVICES_LIVE_SERVICES=str(self.live["services"]),
            UPDATE_SERVICES_LIVE_SCRIPTS=str(self.live["scripts"]),
            UPDATE_SERVICES_LIVE_TMPFILES=str(self.live["tmpfiles"]),
        )

        # Staged tree. One new and one changed unit keep both unit arrays
        # non-empty, so the script also runs under macOS's bash 3.2.
        (self.stage / "services").mkdir(parents=True)
        (self.stage / "scripts" / "lib").mkdir(parents=True)
        (self.stage / "tmpfiles.d").mkdir()
        (self.stage / "services" / "new.service").write_text("[Service]\nExecStart=/bin/true\n")
        (self.stage / "services" / "changed.service").write_text(
            "[Service]\nExecStart=/home/pi/scripts/tool.sh\n")
        (self.stage / "tmpfiles.d" / "x.conf").write_text("f /run/lock/x 0660 root pi -\n")
        for name in ("tool.sh", "helper.py"):
            staged = self.stage / "scripts" / name
            staged.write_text(f"# new {name}\n")
            staged.chmod(0o644)  # repository mode, as the sync stages it
        nested = self.stage / "scripts" / "lib" / "module.py"
        nested.write_text("# nested\n")
        nested.chmod(0o644)

        # Live tree from an earlier successful deployment.
        (self.live["services"] / "changed.service").write_text("[Service]\nExecStart=/bin/false\n")
        for name in ("tool.sh", "helper.py"):
            live = self.live["scripts"] / name
            live.write_text(f"# old {name}\n")
            live.chmod(0o770)
        self.unrelated = self.live["scripts"] / "unrelated.sh"
        self.unrelated.write_text("# not staged\n")
        self.unrelated.chmod(0o640)

    def cleanup_root(self):
        for path in self.root.rglob("*"):
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o755)
        subprocess.run(["/bin/rm", "-rf", "--", str(self.root)], check=False)

    def cleanup_stage(self):
        if self.stage.exists():
            subprocess.run(["/bin/rm", "-rf", "--", str(self.stage)], check=False)

    def run_updater(self):
        return subprocess.run(["/bin/bash", str(UPDATE_SERVICES), str(self.stage)],
                              env=self.env, capture_output=True, text=True, timeout=60)

    def test_successful_update_makes_only_staged_top_level_scripts_executable(self):
        result = self.run_updater()
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("tool.sh", "helper.py"):
            live = self.live["scripts"] / name
            self.assertEqual(live.read_text(), f"# new {name}\n")
            self.assertEqual(mode(live), 0o770, name)
        self.assertEqual(mode(self.live["scripts"] / "lib" / "module.py"), 0o644)
        self.assertEqual(mode(self.unrelated), 0o640)
        calls = self.log.read_text()
        self.assertIn("systemctl enable --now new.service", calls)
        self.assertIn("systemctl restart changed.service", calls)
        self.assertFalse(self.stage.exists(), "staging directory is cleaned up")

    def test_failed_copy_never_leaves_a_live_script_non_executable(self):
        # A live directory the deploying user cannot write makes cp -a fail
        # partway, as a root-owned directory does on the Pi.
        locked_stage = self.stage / "scripts" / "locked"
        locked_stage.mkdir()
        (locked_stage / "file.txt").write_text("new\n")
        locked_live = self.live["scripts"] / "locked"
        locked_live.mkdir()
        locked_live.chmod(0o555)

        result = self.run_updater()
        self.assertNotEqual(result.returncode, 0, "the copy failure must abort the update")
        self.assertNotIn("daemon-reload", self.log.read_text() if self.log.exists() else "")
        for name in ("tool.sh", "helper.py"):
            live = self.live["scripts"] / name
            self.assertTrue(mode(live) & stat.S_IXUSR,
                            f"{name} lost its exec bit: {oct(mode(live))}")


if __name__ == "__main__":
    unittest.main()
