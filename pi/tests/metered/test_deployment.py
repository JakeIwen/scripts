"""Cold installation, interrupted registration and installed-package imports."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from pi import deploy_python as deployment
from pi.tests import test_python_deployment as fixtures


UNIT = 'vanpi-metered.service'


class MeteredDeploymentTests(unittest.TestCase):
    def test_first_activation_and_retry_need_no_fabricated_legacy_unit(self):
        with fixtures.PythonDeploymentTests.fixture() as repo, tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'packages'
            services = fixtures.FakeSystemServices(active=set())
            plan = deployment.build_plan(repo, selected=[UNIT])
            original = services.install_unit
            def interrupted(unit, source):
                original(unit, source)
                raise RuntimeError('reload interrupted')
            services.install_unit = interrupted
            with self.assertRaises(RuntimeError):
                fixtures.PythonDeploymentTests._install(plan, repo, root, 'activate', services, [UNIT])
            self.assertFalse((root / 'pre-package-units').exists())
            services.install_unit = original
            fixtures.PythonDeploymentTests._install(plan, repo, root, 'activate', services, [UNIT])
            self.assertTrue((root / 'service-state' / (UNIT + '.json')).exists())
            self.assertFalse((root / 'pending-restarts' / (UNIT + '.json')).exists())
            self.assertEqual(services.restart_calls, [])

    def test_a_vendor_or_runtime_override_is_not_treated_as_absence(self):
        with fixtures.PythonDeploymentTests.fixture() as repo, tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'packages'
            services = fixtures.FakeSystemServices(active=set())
            services.dropins_by_unit[UNIT] = {'foreign.conf': b'[Service]\nUser=other\n'}
            plan = deployment.build_plan(repo, selected=[UNIT])
            with self.assertRaises(ValueError):
                fixtures.PythonDeploymentTests._install(plan, repo, root, 'activate', services, [UNIT])
            self.assertFalse((root / 'current').exists())

    def test_shared_rules_and_registry_trigger_both_consuming_service_digests(self):
        with fixtures.PythonDeploymentTests.fixture() as repo:
            for relative in ('shared/metered/rules.py', 'shared/metered/consumers.json'):
                before = deployment.build_plan(repo)['manifest']['services']
                path = repo / relative
                path.write_text(path.read_text() + '\n')
                after = deployment.build_plan(repo)['manifest']['services']
                self.assertEqual({key for key in before if before[key] != after[key]},
                                 {UNIT, deployment.DASHBOARD})

    def test_actual_archive_imports_shared_contracts_from_an_unrelated_directory(self):
        root = Path(__file__).resolve().parents[3]
        plan = deployment.build_plan(root)
        output = io.BytesIO(); deployment.make_archive(plan, root, output); output.seek(0)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'release'; destination.mkdir()
            deployment.unpack_archive(output, destination)
            environment = dict(os.environ, PYTHONPATH=str(destination),
                               VANPI_METERED_HOME=str(Path(directory) / 'home'),
                               PYTHONDONTWRITEBYTECODE='1')
            result = subprocess.run([sys.executable, '-P', '-B', '-m', 'pi.apps.metered', 'status'],
                cwd=directory, env=environment, text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload['config']['metered_ssids'], ['denlink'])
            self.assertIn('pi.torrents', [row['id'] for row in payload['registry']])
            self.assertEqual(list(destination.rglob('__pycache__')), [])


if __name__ == '__main__':
    unittest.main()
