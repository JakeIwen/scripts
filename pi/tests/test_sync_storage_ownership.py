import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PI))
import deploy_network_storage as storage

EXCLUDES = PI / "sync_storage_managed.exclude"
RSYNC = "/usr/bin/rsync"


def broad_sync_destination(source):
    """Where pi/sync_scripts.sh plus update_services.sh install a repository file."""
    path = Path(source)
    if source.startswith("pi/scripts/"):
        return "/home/pi/scripts/" + path.relative_to("pi/scripts").as_posix()
    if source.startswith("pi/services/") and path.suffix in (".service", ".path", ".timer"):
        return "/etc/systemd/system/" + path.name
    if source.startswith("pi/tmpfiles.d/") and path.suffix == ".conf":
        return "/etc/tmpfiles.d/" + path.name
    return None


class SyncStorageOwnershipTests(unittest.TestCase):
    def stage(self, root):
        """Stage the three trees exactly as pi/sync_scripts.sh does."""
        exclude = f"--exclude-from={EXCLUDES}"
        trees = (
            ([RSYNC, "-a", "--exclude", "__pycache__/", "--exclude", "*.pyc", exclude],
             PI / "scripts", root / "scripts"),
            ([RSYNC, "-a", "--exclude", "van-dashboard.service", exclude],
             PI / "services", root / "services"),
            ([RSYNC, "-a", exclude], PI / "tmpfiles.d", root / "tmpfiles.d"),
        )
        for command, source, destination in trees:
            subprocess.run([*command, f"{source}/", f"{destination}/"], check=True)

    def test_broad_sync_never_stages_storage_installer_files(self):
        shared = {
            source for source, destination in storage.TARGETS.items()
            if broad_sync_destination(source) == destination
        }
        # Guard against a vacuous pass if the installer's targets move.
        self.assertIn("pi/scripts/network_recorder/collector.py", shared)
        self.assertIn("pi/services/network-flight-recorder.service", shared)
        self.assertIn("pi/tmpfiles.d/vanpi-network.conf", shared)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.stage(root)
            for source in sorted(shared):
                with self.subTest(source=source):
                    staged = root / Path(source).relative_to("pi")
                    self.assertFalse(staged.exists(), staged)
            # The root-owned directory itself must not be staged, or cp -a fails
            # to preserve its timestamp on the Pi.
            self.assertFalse((root / "scripts" / "network_recorder").exists())
            # deploy_python.py installs the dashboard unit from its release.
            self.assertFalse((root / "services" / "van-dashboard.service").exists())

            # Everything else still deploys, including the setup script's
            # pi-owned openwrt-logging sources.
            for kept in (
                "scripts/policyctl",
                "scripts/update_services.sh",
                "scripts/openwrt-logging/30-openwrt-dendelion.conf",
                "services/system-event-monitor.service",
                "services/video-library.service",
                "tmpfiles.d/vanpi-backup.conf",
            ):
                with self.subTest(kept=kept):
                    self.assertTrue((root / kept).is_file(), kept)

    def test_sync_applies_the_exclusions_to_every_staged_tree(self):
        sync = (PI / "sync_scripts.sh").read_text(encoding="utf-8")
        self.assertIn('storage_managed="$dsc/pi/sync_storage_managed.exclude"', sync)
        self.assertEqual(sync.count('--exclude-from="$storage_managed"'), 3)


if __name__ == "__main__":
    unittest.main()
