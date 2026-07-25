import pathlib
import unittest


class DesktopSheetLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = pathlib.Path(__file__).resolve().parents[3]
        cls.stylesheet = (
            root
            / "pi"
            / "apps"
            / "van_dashboard"
            / "static"
            / "van_dashboard.css"
        ).read_text()

    def test_mobile_sheets_remain_bottom_aligned_and_viewport_bounded(self):
        self.assertIn(".speaker-backdrop {", self.stylesheet)
        self.assertIn("align-items: flex-end", self.stylesheet)
        self.assertIn("max-height: min(78vh, 680px)", self.stylesheet)

    def test_desktop_sheets_are_centered_scrollable_dialogs(self):
        desktop = self.stylesheet.split(
            "@media (min-width: 700px) and (min-height: 520px)",
            1,
        )[1]
        self.assertIn("align-items: center", desktop)
        self.assertIn("padding: min(3vh, 24px) 18px", desktop)
        self.assertIn("overscroll-behavior: contain", desktop)
        self.assertIn("scrollbar-gutter: stable", desktop)
        self.assertIn("border-bottom: 1px solid #38505e", desktop)
        self.assertIn("border-radius: 22px", desktop)
        self.assertIn(".speaker-backdrop.open .speaker-sheet", desktop)
        self.assertIn("transform: translateY(0) scale(1)", desktop)
        self.assertIn(".sheet-grabber", desktop)
        self.assertIn("display: none", desktop)


if __name__ == "__main__":
    unittest.main()
