"""Stable launch paths and dependency direction for the display dimmer."""
import ast
import json
from pathlib import Path
import plistlib
import unittest


ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "macbook/scripts/display_cab_dimmer.py"
FEATURE = ROOT / "macbook/scripts/cab_dimmer"


def top_level_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


class DisplayCabDimmerArchitectureTests(unittest.TestCase):
    def test_pure_model_and_lazy_display_adapter_keep_effects_out(self):
        self.assertTrue({"dataclasses", "enum", "re"}.issuperset(
            top_level_imports(FEATURE / "model.py")))
        display_imports = top_level_imports(FEATURE / "displays.py")
        self.assertNotIn("Quartz", display_imports)
        self.assertNotIn("AppKit", display_imports)
        self.assertFalse({"urllib", "subprocess", "socket", "fcntl"} &
                         top_level_imports(FEATURE / "model.py"))

    def test_external_callers_keep_the_stable_entry(self):
        with (ROOT / "macbook/launchagents/com.jacobr.display-cab-dimmer.plist").open("rb") as source:
            plist = plistlib.load(source)
        self.assertEqual(plist["ProgramArguments"][1],
                         "/Users/jacobr/dev/scripts/macbook/scripts/display_cab_dimmer.py")
        declaration = json.loads(
            (ROOT / "shared/automations/display-cab-dimmer.json").read_text(encoding="utf-8"))
        commands = [declaration["status"]["argv"],
                    *(action["argv"] for action in declaration["actions"])]
        self.assertTrue(all("{repo}/macbook/scripts/display_cab_dimmer.py" in argv
                            for argv in commands))
        entry = ENTRY.read_text(encoding="utf-8")
        for owner in ("cab_dimmer.model", "cab_dimmer.state_store", "cab_dimmer.displays",
                      "cab_dimmer.dashboard", "cab_dimmer.runtime"):
            self.assertIn(owner, entry)


if __name__ == "__main__":
    unittest.main()
