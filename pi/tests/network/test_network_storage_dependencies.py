"""Storage prerequisites pin sysmon without taking ownership of its files."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

from . import test_network_storage_deployment as fixtures

deploy = fixtures.deploy


class MonitorPinChecks:
    def test_every_dependency_mismatch_blocks_check_and_apply_before_mutation(self):
        for destination in self.plan['monitor_dependencies']:
            path = Path(destination)
            before = path.read_bytes()
            path.write_bytes(b'operator edit')
            calls = list(self.calls)
            with self.subTest(path=destination):
                for operation in (lambda: deploy.inspect(copy.deepcopy(self.plan)),
                                  lambda: deploy.apply(self.plan, self.stage)):
                    with self.assertRaisesRegex(ValueError, 'system monitor dependency differs'):
                        operation()
                self.assertEqual(self.calls, calls)
                self.assertFalse(deploy.BACKUPS.exists())
                self.assertEqual(path.read_bytes(), b'operator edit')
            path.write_bytes(before)

    def test_missing_and_symlink_dependencies_fail_closed(self):
        path = Path(next(iter(self.plan['monitor_dependencies'])))
        before = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'system monitor dependency differs'):
            deploy.inspect(copy.deepcopy(self.plan))
        other = self.root / 'outside-monitor'; other.write_bytes(before)
        path.symlink_to(other)
        with self.assertRaisesRegex(ValueError, 'non-regular'):
            deploy.apply(self.plan, self.stage)
        self.assertFalse(deploy.BACKUPS.exists())
        self.assertEqual(other.read_bytes(), before)

    def test_malformed_pins_and_unchecked_plans_refuse(self):
        pins = self.plan['monitor_dependencies']
        first = next(iter(pins))
        for malformed in (None, {}, {k: v for k, v in pins.items() if k != first},
                          {**pins, '/not-allowed': 'a' * 64}, {**pins, first: 'g' * 64}):
            for historical in (False, True):
                with self.subTest(pins=malformed, historical=historical):
                    plan = {**self.plan, 'monitor_dependencies': malformed}
                    with self.assertRaisesRegex(ValueError, 'system monitor dependency'):
                        deploy.validate_plan(plan, historical=historical)
        unchecked = copy.deepcopy(self.plan)
        del unchecked['monitor_dependencies']
        for operation in (lambda: deploy.inspect(unchecked),
                          lambda: deploy.apply(unchecked, self.stage)):
            with self.assertRaisesRegex(ValueError, 'dependency allowlist'):
                operation()
        deploy.validate_plan(unchecked, historical=True)

    def test_old_manifest_lineage_updates_and_rolls_back_without_pins(self):
        before = {path: Path(path).read_bytes() for path in self.plan['monitor_dependencies']}
        deploy.apply(self.plan, self.stage)
        manifest = deploy.BACKUPS / self.plan['release'] / 'manifest.json'
        historical = json.loads(manifest.read_text())
        del historical['monitor_dependencies']
        manifest.write_text(json.dumps(historical))
        update = copy.deepcopy(self.plan)
        update['release'] += '-new'
        deploy.inspect(update)
        deploy.apply(update, self.stage)
        deploy.rollback(update['release'])
        deploy.rollback(self.plan['release'])
        for path, contents in before.items():
            self.assertEqual(Path(path).read_bytes(), contents)
        self.assertEqual(self.core.read_bytes(), b'old=True\n')

    def test_new_manifest_rollback_validates_allowlist_not_live_dependency_hash(self):
        deploy.apply(self.plan, self.stage)
        dependency = Path(next(iter(self.plan['monitor_dependencies'])))
        dependency.write_bytes(b'new broad sync version')
        mapping = dict(deploy.dependencies.MONITOR_DEPENDENCIES)
        mapping['pi/scripts/system_monitor/new.py'] = '/home/pi/scripts/system_monitor/new.py'
        calls = list(self.calls)
        with mock.patch.object(deploy.dependencies, 'MONITOR_DEPENDENCIES', mapping):
            with self.assertRaisesRegex(ValueError, 'dependency allowlist'):
                deploy.rollback(self.plan['release'])
        self.assertEqual(self.calls, calls)
        deploy.rollback(self.plan['release'])
        self.assertEqual(dependency.read_bytes(), b'new broad sync version')

    def test_local_dependency_change_blocks_apply_before_ssh_staging(self):
        plan = {**self.plan, 'target': 'fixture'}
        path = self.root / 'checked-plan.json'
        path.write_text(json.dumps(plan))
        current = copy.deepcopy(plan)
        first = next(iter(current['monitor_dependencies']))
        current['monitor_dependencies'][first] = '0' * 64
        args = ['installer', '--target', 'fixture', 'apply', '--plan', str(path)]
        if plan.get('recorder_only'):
            args.append('--recorder-only')
        with mock.patch.object(sys, 'argv', args), mock.patch.object(
                deploy, 'make_plan', return_value=current), mock.patch.object(
                deploy.base, 'command') as command, mock.patch.object(deploy, 'remote') as remote:
            with self.assertRaisesRegex(ValueError, 'local system monitor dependency changed'):
                deploy.main()
        command.assert_not_called()
        remote.assert_not_called()


class RecorderMonitorPinTests(MonitorPinChecks, unittest.TestCase):
    setUp = fixtures.RecorderOnlyUpdateTests.setUp


class FullMonitorPinTests(MonitorPinChecks, unittest.TestCase):
    setUp = fixtures.FullStorageUpdateTests.setUp


class PinSourceTests(unittest.TestCase):
    def test_real_plan_pins_shim_and_complete_package_as_unmanaged_prerequisites(self):
        plan = deploy.make_plan('fixture', recorder_only=True)
        sources = {'pi/scripts/system_event_monitor.py'} | {
            str(path.relative_to(deploy.REPO))
            for path in (deploy.REPO / 'pi/scripts/system_monitor').glob('*.py')}
        self.assertEqual(set(deploy.dependencies.MONITOR_DEPENDENCIES), sources)
        expected = {destination: deploy.base.digest(deploy.REPO / source)
                    for source, destination in deploy.dependencies.MONITOR_DEPENDENCIES.items()}
        self.assertEqual(plan['monitor_dependencies'], expected)
        self.assertTrue(set(expected).isdisjoint(row['destination'] for row in plan['files']))

    def test_planning_refuses_missing_or_symlinked_dependency(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            targets = {'monitor.py': '/home/pi/scripts/system_event_monitor.py'}
            with mock.patch.object(deploy.dependencies, 'MONITOR_DEPENDENCIES', targets):
                with self.assertRaisesRegex(ValueError, 'missing regular system monitor'):
                    deploy.dependencies.make_pins(root)
                (root / 'real.py').write_text('# reviewed\n')
                (root / 'monitor.py').symlink_to(root / 'real.py')
                with self.assertRaisesRegex(ValueError, 'non-regular'):
                    deploy.dependencies.make_pins(root)


if __name__ == '__main__':
    unittest.main()
