"""The BTT page is a safe inline bundle; preview never needs the shell."""
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from macbook.bettertouchtool.lighting_control.page import ASSETS, build_page


class PageParser(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.external = []
        self.scripts = []
        self.current = None
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


class LightingPageTests(unittest.TestCase):
    def test_self_contained_and_vendor_bytes_inlined(self):
        output = build_page([{"entity_id": "light.one", "label": "One", "group_label": "Room"}], "/some cli.py")
        parsed = PageParser(output)
        self.assertEqual(parsed.external, [])
        vendor = (ASSETS / "vendor/iro-5.5.2.min.js").read_bytes()
        self.assertEqual(hashlib.sha256(vendor).hexdigest(),
                         "5d08eedbac9af7212f5fdf7e336aeb2da87ac47b2364818ad4bbd7fcbdd18d0d")
        self.assertIn(vendor.decode("utf-8"), output)
        self.assertIn((ASSETS / "control.css").read_text(), output)
        self.assertIn("BTTInitialize", output)
        self.assertIn("BTTWindowWillBecomeVisible", output)
        self.assertNotIn("LightingPreview.create", output)
        config = next(script for script in parsed.scripts if script["attrs"].get("id") == "lighting-config")
        self.assertEqual(json.loads(config["source"])["cli_path"], "/some cli.py")

    def test_config_cannot_close_script_even_with_hostile_label_and_path(self):
        hostile = '</script><script>alert("x")</script> &'
        lights = [{"entity_id": "light.one", "label": hostile, "group_label": hostile}]
        output = build_page(lights, "/cli/" + hostile)
        parsed = PageParser(output)
        self.assertEqual(len(parsed.scripts), 6)
        config = next(script for script in parsed.scripts if script["attrs"].get("id") == "lighting-config")
        self.assertNotIn("</", config["source"])
        self.assertEqual(json.loads(config["source"]), {"lights": lights, "cli_path": "/cli/" + hostile})
        self.assertEqual(parsed.external, [])

    def test_invalid_config_is_rejected_at_build_boundary(self):
        for entity in ["light.one;bad", "switch.one", "light.one\n", "", None]:
            with self.subTest(entity=entity), self.assertRaises(ValueError):
                build_page([{"entity_id": entity}], "/cli")
        for cli in ["relative", "/cli\npath", "/cli\0path", None]:
            with self.subTest(cli=cli), self.assertRaises(ValueError):
                build_page([], cli)
        with self.assertRaises(ValueError):
            build_page([{"entity_id": "light.one"}, {"entity_id": "light.one"}], "/cli")

    def test_system_python_preview_cli_writes_fixture_bundle_and_help(self):
        script = ASSETS / "page.py"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "preview.html"
            result = subprocess.run(["/usr/bin/python3", "-I", "-B", str(script), "--preview", str(output)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            source = output.read_text()
            self.assertIn("LightingPreview.create(config.lights)", source)
            self.assertIn("light.preview_dresser", source)
            self.assertEqual(PageParser(source).external, [])
        help_result = subprocess.run(["/usr/bin/python3", "-I", "-B", str(script), "--help"],
                                     capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("--preview OUT.html", help_result.stdout)


if __name__ == "__main__":
    unittest.main()
