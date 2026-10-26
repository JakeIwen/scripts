import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import firefox_quiet_prefs as fqp


def make_firefox_dir(root: Path) -> Path:
    (root / "Profiles/a.dev-edition-default").mkdir(parents=True)
    (root / "Profiles/a.dev-edition-default/prefs.js").write_text("")
    (root / "Profiles/b.default-release").mkdir(parents=True)
    (root / "Profiles/b.default-release/prefs.js").write_text("")
    (root / "Profiles/c.never-run").mkdir(parents=True)
    (root / "profiles.ini").write_text(
        "[Profile0]\nName=dev\nIsRelative=1\nPath=Profiles/a.dev-edition-default\n\n"
        "[Profile1]\nName=release\nIsRelative=1\nPath=Profiles/b.default-release\n\n"
        "[Profile2]\nName=unused\nIsRelative=1\nPath=Profiles/c.never-run\n\n"
        "[General]\nVersion=2\n"
    )
    return root


class FirefoxQuietPrefsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = make_firefox_dir(Path(self.tmp.name))
        self.dev_user_js = self.dir / "Profiles/a.dev-edition-default/user.js"

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()):
            return fqp.main(list(argv), firefox_dir=self.dir)

    def test_writes_only_profiles_firefox_has_run(self):
        self.assertEqual(self.run_main(), 0)
        self.assertTrue(self.dev_user_js.exists())
        self.assertTrue((self.dir / "Profiles/b.default-release/user.js").exists())
        self.assertFalse((self.dir / "Profiles/c.never-run/user.js").exists())
        text = self.dev_user_js.read_text()
        self.assertIn('user_pref("browser.urlbar.suggest.quicksuggest.sponsored", false);', text)

    def test_rerun_is_idempotent_and_keeps_existing_lines(self):
        self.dev_user_js.write_text('user_pref("my.own.pref", 1);\n')
        self.run_main()
        first = self.dev_user_js.read_text()
        self.run_main()
        self.assertEqual(self.dev_user_js.read_text(), first)
        self.assertTrue(first.startswith('user_pref("my.own.pref", 1);\n'))
        self.assertEqual(first.count(fqp.BEGIN), 1)

    def test_remove_restores_original_content(self):
        self.dev_user_js.write_text('user_pref("my.own.pref", 1);\n')
        self.run_main()
        self.run_main("--remove")
        self.assertEqual(self.dev_user_js.read_text(), 'user_pref("my.own.pref", 1);\n')

    def test_dry_run_writes_nothing(self):
        self.run_main("--dry-run")
        self.assertFalse(self.dev_user_js.exists())


if __name__ == "__main__":
    unittest.main()
