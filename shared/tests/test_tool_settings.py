"""Behavior tests for shared tool declarations and private value storage."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import tool_settings


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "shared/python/tool_settings.py"
REAL_DECLARATIONS = ROOT / "shared/settings"


def sample_declaration():
    return {
        "tool": "sample",
        "title": "Sample tool",
        "description": "Settings used by the tests.",
        "settings": [
            {"name": "count", "label": "Item count", "type": "integer", "default": 2,
             "minimum": 1, "maximum": 10, "description": "Item count."},
            {"name": "ratio", "type": "number", "default": 1.5, "minimum": 0,
             "maximum": 5, "step": 0.25, "unit": "x", "description": "Scale."},
            {"name": "enabled", "type": "boolean", "default": False,
             "description": "Whether it is enabled."},
            {"name": "mode", "type": "choice", "default": "fast",
             "choices": ["fast", "slow"], "description": "Operating mode."},
        ],
    }


class ToolSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.declarations = self.root / "declarations"
        self.config = self.root / "config"
        self.declarations.mkdir()
        self.declaration_path = self.declarations / "sample.json"
        self.write_declaration(sample_declaration())
        self.environment = mock.patch.dict(os.environ, {
            "JACOBR_SETTINGS_DIR": str(self.declarations),
            "JACOBR_CONFIG_DIR": str(self.config),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def write_declaration(self, declaration):
        self.declaration_path.write_text(json.dumps(declaration), encoding="utf-8")

    def test_declaration_loads_in_order_and_serializes_all_fields(self):
        declaration = tool_settings.load_declaration("sample")
        self.assertEqual(tool_settings.declaration_tools(), ["sample"])
        self.assertEqual([setting.name for setting in declaration.settings],
                         ["count", "ratio", "enabled", "mode"])
        self.assertEqual(declaration.defaults(), {
            "count": 2, "ratio": 1.5, "enabled": False, "mode": "fast",
        })
        serialized = declaration.as_dict()
        self.assertIsInstance(serialized["settings"], list)
        self.assertEqual(set(serialized["settings"][0]), {
            "name", "type", "default", "description", "minimum", "maximum",
            "choices", "unit", "step", "label",
        })
        self.assertEqual(declaration.settings[0].label, "Item count")
        self.assertIsNone(declaration.settings[1].label)

    def test_declaration_rejects_schema_problems_and_names_the_file(self):
        cases = []
        mismatched = sample_declaration()
        mismatched["tool"] = "other"
        cases.append((mismatched, "tool must equal file stem"))
        unknown_type = sample_declaration()
        unknown_type["settings"][0]["type"] = "decimal"
        cases.append((unknown_type, "type must be one of"))
        duplicate = sample_declaration()
        duplicate["settings"][1]["name"] = "count"
        cases.append((duplicate, "duplicate setting names count"))
        bad_default = sample_declaration()
        bad_default["settings"][0]["default"] = False
        cases.append((bad_default, "default is invalid"))
        no_choices = sample_declaration()
        no_choices["settings"][3]["choices"] = []
        cases.append((no_choices, "choices must be a non-empty list"))
        reversed_range = sample_declaration()
        reversed_range["settings"][0]["minimum"] = 11
        cases.append((reversed_range, "minimum must not exceed maximum"))
        blank_label = sample_declaration()
        blank_label["settings"][0]["label"] = " "
        cases.append((blank_label, "label must be a non-empty string"))
        for declaration, expected in cases:
            with self.subTest(expected=expected):
                self.write_declaration(declaration)
                with self.assertRaisesRegex(tool_settings.SettingsError, expected) as caught:
                    tool_settings.load_declaration("sample")
                self.assertIn(str(self.declaration_path), str(caught.exception))

    def test_validation_rejects_unknown_wrong_bool_number_and_range(self):
        declaration = tool_settings.load_declaration("sample")
        with self.assertRaises(tool_settings.SettingsError) as caught:
            tool_settings.validate(declaration, {
                "mystery": 1,
                "enabled": "yes",
                "ratio": True,
                "count": 0,
            })
        message = str(caught.exception)
        self.assertIn("unknown settings mystery", message)
        self.assertIn("allowed settings: count, ratio, enabled, mode", message)
        self.assertIn("enabled must be a boolean", message)
        self.assertIn("ratio must be a number", message)
        self.assertIn("count must be between 1 and 10 (got 0)", message)
        self.assertEqual(message.count("; "), 3)

    def test_validation_normalizes_integral_integer_and_rejects_fraction(self):
        declaration = tool_settings.load_declaration("sample")
        self.assertEqual(tool_settings.validate(declaration, {"count": 3.0}), {"count": 3})
        with self.assertRaisesRegex(tool_settings.SettingsError, "must be an integer"):
            tool_settings.validate(declaration, {"count": 3.5})
        with self.assertRaisesRegex(tool_settings.SettingsError, "JSON object"):
            tool_settings.validate(declaration, [1, 2])

    def test_missing_values_file_returns_defaults(self):
        state = tool_settings.read_state("sample")
        self.assertEqual(state.values, tool_settings.load_declaration("sample").defaults())
        self.assertEqual(state.overrides, {})
        self.assertEqual(state.revision, "missing")
        self.assertIsNone(state.error)

    def test_save_is_atomic_private_and_stores_only_non_defaults(self):
        self.config.mkdir(mode=0o755)
        with mock.patch.object(tool_settings.os, "replace", wraps=os.replace) as replace:
            state = tool_settings.save("sample", {"count": 4, "ratio": 1.5})
        path = tool_settings.values_path("sample")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"count": 4})
        self.assertEqual(state.values["count"], 4)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o700)
        replace.assert_called_once()
        source, destination = replace.call_args.args
        self.assertEqual(Path(source).parent, path.parent)
        self.assertEqual(Path(destination), path)
        self.assertEqual(list(self.config.glob(".sample.json.*")), [])

    def test_partial_updates_merge_and_setting_default_removes_override(self):
        tool_settings.save("sample", {"count": 4, "mode": "slow"})
        state = tool_settings.save("sample", {"ratio": 2.5})
        self.assertEqual(state.overrides, {"count": 4, "ratio": 2.5, "mode": "slow"})
        state = tool_settings.save("sample", {"count": 2})
        self.assertEqual(state.overrides, {"ratio": 2.5, "mode": "slow"})

    def test_stale_revision_raises_conflict(self):
        first = tool_settings.save("sample", {"count": 3})
        tool_settings.save("sample", {"count": 4}, expected_revision=first.revision)
        with self.assertRaises(tool_settings.SettingsConflict):
            tool_settings.save("sample", {"count": 5}, expected_revision=first.revision)

    def test_invalid_file_is_reported_while_load_raises(self):
        self.config.mkdir()
        path = tool_settings.values_path("sample")
        path.write_text('{"count": 99, "other": true}', encoding="utf-8")
        state = tool_settings.read_state("sample")
        self.assertEqual(state.values, tool_settings.load_declaration("sample").defaults())
        self.assertEqual(state.overrides, {})
        self.assertIn("unknown settings other", state.error)
        self.assertIn("count must be between 1 and 10", state.error)
        self.assertNotEqual(state.revision, "missing")
        with self.assertRaises(tool_settings.SettingsError):
            tool_settings.load("sample")

    def test_reloader_detects_changes_and_keeps_last_good_values(self):
        reloader = tool_settings.Reloader("sample")
        self.assertFalse(reloader.poll().changed)
        tool_settings.save("sample", {"count": 5})
        changed = reloader.poll()
        self.assertTrue(changed.changed)
        self.assertEqual(changed.values["count"], 5)
        self.assertIsNone(changed.error)

        path = tool_settings.values_path("sample")
        path.write_text('{"count": 50}', encoding="utf-8")
        invalid = reloader.poll()
        self.assertFalse(invalid.changed)
        self.assertEqual(invalid.values["count"], 5)
        self.assertIn("count must be between 1 and 10", invalid.error)

        path.unlink()
        deleted = reloader.poll()
        self.assertTrue(deleted.changed)
        self.assertEqual(deleted.values["count"], 2)
        self.assertIsNone(deleted.error)

    def test_reloader_keeps_values_when_the_declaration_becomes_unreadable(self):
        tool_settings.save("sample", {"count": 5})
        reloader = tool_settings.Reloader("sample")
        broken = sample_declaration()
        broken["settings"][0]["surprise"] = True
        self.write_declaration(broken)
        tool_settings.values_path("sample").write_text('{"count": 6}', encoding="utf-8")
        result = reloader.poll()
        self.assertFalse(result.changed)
        self.assertEqual(result.values["count"], 5)
        self.assertIn("unknown fields surprise", result.error)

    def test_real_iterm_declaration_loads(self):
        with mock.patch.dict(os.environ, {"JACOBR_SETTINGS_DIR": str(REAL_DECLARATIONS)}):
            declaration = tool_settings.load_declaration("iterm-screen-fonts")
        self.assertEqual([setting.name for setting in declaration.settings],
                         ["built_in", "external", "poll_seconds", "settle_polls"])
        self.assertEqual(declaration.defaults()["external"], 15)

    def private_declaration(self, tool, declaration=None):
        """Declare a tool in the private directory through a symlink, as an installer would."""
        declaration = declaration or dict(sample_declaration(), tool=tool)
        source = self.root / "elsewhere" / (tool + ".json")
        source.parent.mkdir(exist_ok=True)
        source.write_text(json.dumps(declaration), encoding="utf-8")
        link = self.config / "declarations" / (tool + ".json")
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(source)
        return link

    def run_cli(self, *arguments, check=True):
        # -I -B as the panel and LaunchAgents run it: environment overrides still reach
        # the module because it reads JACOBR_* itself, not PYTHON* variables.
        return subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), *arguments],
                              check=check, capture_output=True, text=True, env=dict(os.environ))

    def test_tool_list_merges_shared_and_private_declarations(self):
        self.private_declaration("outside")
        (self.config / "declarations" / "notes.txt").write_text("not a declaration", encoding="utf-8")
        self.assertEqual(tool_settings.declaration_tools(), ["outside", "sample"])
        self.assertEqual(self.run_cli("list").stdout.split(), ["outside", "sample"])

    def test_missing_private_directory_lists_only_shared_tools(self):
        self.assertFalse(self.config.exists())
        self.assertEqual(tool_settings.declaration_tools(), ["sample"])
        self.config.mkdir()
        self.assertEqual(tool_settings.declaration_tools(), ["sample"])

    def test_private_only_tool_loads_saves_and_reloads_through_a_symlink(self):
        self.private_declaration("outside")
        declaration = tool_settings.load_declaration("outside")
        self.assertEqual(declaration.tool, "outside")
        self.assertEqual(tool_settings.read_state("outside").values, declaration.defaults())
        reloader = tool_settings.Reloader("outside")
        state = tool_settings.save("outside", {"count": 7})
        values = self.config / "outside.json"
        self.assertEqual(state.path, str(values))
        self.assertEqual(json.loads(values.read_text(encoding="utf-8")), {"count": 7})
        self.assertEqual(tool_settings.load("outside")["count"], 7)
        reloaded = reloader.poll()
        self.assertTrue(reloaded.changed)
        self.assertEqual(reloaded.values["count"], 7)

    def test_cli_shows_and_sets_a_private_only_tool(self):
        self.private_declaration("outside")
        set_result = self.run_cli("set", "outside", "mode=slow", "enabled=true")
        self.assertEqual(json.loads(set_result.stdout)["overrides"], {"enabled": True, "mode": "slow"})
        shown = json.loads(self.run_cli("show", "outside").stdout)
        self.assertEqual(shown["values"]["mode"], "slow")
        self.assertEqual(shown["path"], str(self.config / "outside.json"))

    def test_tool_declared_in_both_directories_is_an_error_naming_both(self):
        link = self.private_declaration("sample")
        self.assertEqual(tool_settings.declaration_tools(), ["sample"])
        for attempt in (lambda: tool_settings.load_declaration("sample"),
                        lambda: tool_settings.read_state("sample"),
                        lambda: tool_settings.save("sample", {"count": 3})):
            with self.assertRaises(tool_settings.SettingsError) as caught:
                attempt()
            self.assertIn(str(self.declaration_path), str(caught.exception))
            self.assertIn(str(link), str(caught.exception))
        result = self.run_cli("show", "sample", check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn(str(link), result.stderr)
        self.assertFalse((self.config / "sample.json").exists())

    def test_dangling_private_link_is_listed_and_names_link_and_target_when_loaded(self):
        link = self.private_declaration("outside")
        target = self.root / "elsewhere" / "outside.json"
        target.unlink()
        self.assertEqual(tool_settings.declaration_tools(), ["outside", "sample"])
        self.assertEqual(self.run_cli("list").stdout.split(), ["outside", "sample"])
        for attempt in (lambda: tool_settings.load_declaration("outside"),
                        lambda: tool_settings.read_state("outside"),
                        lambda: tool_settings.save("outside", {"count": 3})):
            with self.assertRaises(tool_settings.SettingsError) as caught:
                attempt()
            self.assertIn(str(link), str(caught.exception))
            self.assertIn("broken symlink", str(caught.exception))
            self.assertIn(str(target), str(caught.exception))
        result = self.run_cli("show", "outside", check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn(str(target), result.stderr)
        self.assertFalse((self.config / "outside.json").exists())

    def test_dangling_shared_link_is_listed_and_names_its_relative_target(self):
        (self.declarations / "moved.json").symlink_to(Path("gone") / "moved.json")
        self.assertEqual(tool_settings.declaration_tools(), ["moved", "sample"])
        with self.assertRaises(tool_settings.SettingsError) as caught:
            tool_settings.load_declaration("moved")
        self.assertIn(str(self.declarations / "moved.json"), str(caught.exception))
        self.assertIn(str(self.declarations / "gone" / "moved.json"), str(caught.exception))

    def test_cli_set_and_show_round_trip(self):
        environment = dict(os.environ)
        set_result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "set", "sample",
             "count=4", "ratio=2.5", "enabled=true", "mode=slow"],
            check=True, capture_output=True, text=True, env=environment)
        self.assertEqual(json.loads(set_result.stdout)["values"]["mode"], "slow")
        show_result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "show", "sample"],
            check=True, capture_output=True, text=True, env=environment)
        shown = json.loads(show_result.stdout)
        self.assertEqual(shown["values"], {
            "count": 4, "ratio": 2.5, "enabled": True, "mode": "slow",
        })
        self.assertEqual(shown["overrides"], shown["values"])


if __name__ == "__main__":
    unittest.main()
