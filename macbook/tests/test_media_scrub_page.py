"""The slider page is one inline document; the BTT lifecycle hooks drive its controller."""
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from macbook.bettertouchtool.media_scrub_control.page import ASSETS, build_page

CLI = "/Users/x/it's dir/media_scrub.py"


class Parser(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.external, self.scripts, self.current = [], [], None
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "src" in attrs or "href" in attrs:
            self.external.append((tag, attrs))
        if tag == "script":
            self.current = {"attrs": attrs, "source": ""}
            self.scripts.append(self.current)

    def handle_data(self, data):
        if self.current is not None:
            self.current["source"] += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.current = None


class PageTests(unittest.TestCase):
    def test_self_contained_with_bootstrap_hooks_and_assets_inlined(self):
        output = build_page(CLI)
        parsed = Parser(output)
        self.assertEqual(parsed.external, [])
        for name in ("bridge.js", "controller.js", "view.js", "control.css"):
            self.assertIn((ASSETS / name).read_text(), output)
        for hook in ("BTTInitialize", "BTTWindowWillBecomeVisible", "BTTWillHideWindow", "BTTWillCloseWindow",
                     "visibilitychange"):
            self.assertIn(hook, output)
        config = next(s for s in parsed.scripts if s["attrs"].get("id") == "scrub-config")
        self.assertEqual(json.loads(config["source"]), {"cli_path": CLI})

    def test_hostile_path_cannot_close_the_config_script(self):
        hostile = '/x/</script><script>alert(1)</script>/media_scrub.py'
        parsed = Parser(build_page(hostile))
        config = next(s for s in parsed.scripts if s["attrs"].get("id") == "scrub-config")
        self.assertNotIn("</", config["source"])
        self.assertEqual(json.loads(config["source"])["cli_path"], hostile)
        self.assertEqual(len(parsed.scripts), 5)

    def test_invalid_paths_are_rejected(self):
        for bad in ("relative.py", "/bad\npath", "/bad\0path", None):
            with self.subTest(path=bad), self.assertRaises(ValueError):
                build_page(bad)

    def test_page_stays_inside_the_bar_row(self):
        css = (ASSETS / "control.css").read_text()
        self.assertIn("height: 40px", css)

    @unittest.skipUnless(shutil.which("node"), "node is needed to run the bootstrap")
    def test_bootstrap_starts_and_stops_polling_from_each_lifecycle_hook(self):
        # Runs the real page script against a stub DOM: hooks must start and stop the poller.
        harness = Path(__file__).with_name("media_scrub_page_harness.cjs")
        with tempfile.TemporaryDirectory() as directory:
            page = Path(directory) / "page.html"
            page.write_text(build_page(CLI), encoding="utf-8")
            result = subprocess.run(["node", str(harness), str(page)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
