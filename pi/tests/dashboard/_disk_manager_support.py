"""Disk manager fixture shared by the dashboard disk tests.

Holds the managed-label policy, a two-disk lsblk tree and the boot ids so the
disk tests in test_van_dashboard.py and the sibling event-ordering module build
the same DiskManager without each carrying the tree.
"""
import os

from pi.apps.van_dashboard import van_dashboard_disks as dashboard_disks


class DiskManagerFixture:
    CURRENT_BOOT_ID = "b" * 32
    PREVIOUS_BOOT_ID = "a" * 32
    CONFIG = """\
MOUNT_LABELS=(
  movingparts
  EXFAT512
)
ALWAYS_MOUNT_LABELS=(
  EXFAT512
)
MANUAL_MOUNT_LABELS=(
  bigboi
)
HDD_LABELS=(
  movingparts
  bigboi
)
"""

    @staticmethod
    def lsblk(movingparts_mounted=True):
        return {
            "blockdevices": [
                {
                    "name": "sda",
                    "path": "/dev/sda",
                    "type": "disk",
                    "tran": "usb",
                    "size": 5_000_000_000_000,
                    "mountpoints": [None],
                    "children": [
                        {
                            "name": "sda1",
                            "path": "/dev/sda1",
                            "pkname": "sda",
                            "type": "part",
                            "label": "movingparts",
                            "partlabel": None,
                            "fstype": "ext4",
                            "size": 4_999_000_000_000,
                            "mountpoints": (
                                ["/mnt/movingparts"] if movingparts_mounted else [None]
                            ),
                        }
                    ],
                },
                {
                    "name": "sdb",
                    "path": "/dev/sdb",
                    "type": "disk",
                    "tran": "usb",
                    "size": 6_000_000_000_000,
                    "mountpoints": [None],
                    "children": [
                        {
                            "name": "sdb1",
                            "path": "/dev/sdb1",
                            "pkname": "sdb",
                            "type": "part",
                            "label": "bigboi",
                            "partlabel": None,
                            "fstype": "ext4",
                            "size": 5_999_000_000_000,
                            "mountpoints": [None],
                        }
                    ],
                },
            ]
        }

    def manager(self, tempdir, command, **kwargs):
        config = os.path.join(tempdir, "disk_policy.sh")
        boot_id = os.path.join(tempdir, "boot_id")
        kwargs.setdefault("health_dir", os.path.join(tempdir, "health"))
        kwargs.setdefault("event_database", os.path.join(tempdir, "events.sqlite3"))
        with open(config, "w", encoding="utf-8") as handle:
            handle.write(self.CONFIG)
        with open(boot_id, "w", encoding="ascii") as handle:
            value = self.CURRENT_BOOT_ID
            handle.write(
                f"{value[:8]}-{value[8:12]}-{value[12:16]}-"
                f"{value[16:20]}-{value[20:]}\n"
            )
        return dashboard_disks.DiskManager(
            config=config,
            control="/test/diskctl",
            hold_dir=os.path.join(tempdir, "holds"),
            boot_id_path=boot_id,
            command=command,
            wall_clock=lambda: 1_000,
            **kwargs,
        )
