import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class UsbTileLayoutStaticTests(unittest.TestCase):
    def test_connected_storage_devices_wrap_below_heading(self):
        template = (
            REPOSITORY_ROOT
            / "pi/apps/van_dashboard/templates/van_dashboard.html"
        ).read_text(encoding="utf-8")
        stylesheet = (
            REPOSITORY_ROOT
            / "pi/apps/van_dashboard/static/van_dashboard.css"
        ).read_text(encoding="utf-8")

        heading = template.index('class="usb-storage-heading"')
        devices = template.index('class="usb-storage-devices"')
        self.assertLess(heading, devices)
        self.assertIn("Connected storage", template)
        self.assertIn("white-space: normal", stylesheet)
        self.assertIn("overflow-wrap: anywhere", stylesheet)
        self.assertIn(".usb-storage-devices", stylesheet)

    def test_unavailable_room_controls_rely_on_grey_status(self):
        template = (
            REPOSITORY_ROOT
            / "pi/apps/van_dashboard/templates/van_dashboard.html"
        ).read_text(encoding="utf-8")
        for room in ("cab", "rear", "kitchen"):
            self.assertIn(f'id="lighting-room-{room}-state"></strong>', template)


if __name__ == "__main__":
    unittest.main()
