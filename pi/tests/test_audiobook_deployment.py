"""Audiobook package resources are explicit release and digest inputs."""
import unittest

from pi import deploy_python


ASSET = "pi/apps/audiobooks/templates/audiobooks.html"


class AudiobookDeploymentTests(unittest.TestCase):
    def test_page_asset_is_allowlisted_and_changes_only_audiobooks(self):
        self.assertIn(ASSET, deploy_python.ASSETS)
        self.assertIn(ASSET, deploy_python.source_files(deploy_python.ROOT))
        plan = deploy_python.build_plan(deploy_python.ROOT)["manifest"]
        files = plan["files"]
        audiobook_files = {
            path: sha for path, sha in files.items()
            if path.startswith("pi/apps/audiobooks/")
            or path in ("pi/__init__.py", "pi/apps/__init__.py", "pi/package_runtime.py")
        }
        self.assertIn(ASSET, audiobook_files)
        self.assertEqual(plan["services"]["audiobooks.service"],
                         deploy_python.digest(deploy_python.encoded(audiobook_files)))
        for unit, spec in deploy_python.SERVICES.items():
            if unit != "audiobooks.service":
                self.assertFalse(ASSET.startswith(f"pi/apps/{spec['app']}/"))


if __name__ == "__main__":
    unittest.main()
