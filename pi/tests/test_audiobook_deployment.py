"""Audiobook package resources are explicit release and digest inputs."""
import unittest

from pi import deploy_python
from pi.tests import test_python_deployment as deployment_fixtures


ASSET = "pi/apps/audiobooks/templates/audiobooks.html"


class AudiobookDeploymentTests(unittest.TestCase):
    def test_page_asset_is_allowlisted_and_changes_only_audiobooks(self):
        self.assertIn(ASSET, deploy_python.ASSETS)
        self.assertIn(ASSET, deploy_python.source_files(deploy_python.ROOT))
        plan = deploy_python.build_plan(deploy_python.ROOT)["manifest"]
        self.assertIn(ASSET, plan["files"])
        with deployment_fixtures.PythonDeploymentTests.fixture() as repo:
            before = deploy_python.build_plan(repo)["manifest"]["services"]
            asset = repo / ASSET
            asset.write_text(asset.read_text() + "<!-- changed page -->\n")
            after = deploy_python.build_plan(repo)["manifest"]["services"]
            affected = {unit for unit in before if before[unit] != after[unit]}
            self.assertEqual(affected, {"audiobooks.service"})


if __name__ == "__main__":
    unittest.main()
