"""Isolated install/rollback tests; no SSH, services, or live paths are changed."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import types
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[2] / 'deploy_network_flight_recorder.py'
spec = importlib.util.spec_from_file_location('network_deploy', SCRIPT)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='network-deploy-test-', dir='/private/tmp' if Path('/private/tmp').is_dir() else '/tmp')
        self.root = Path(self.directory.name).resolve()
        self.activation = self.root / 'activated.json'
        self.frontend = self.root / 'frontend'
        for name in ('old', 'older'):
            release = self.frontend / 'releases' / name
            release.mkdir(parents=True)
            (release / 'index.html').write_text(name)
            (release / 'react_dashboard_preview.py').write_text('existing server')
        (self.frontend / 'current').symlink_to('releases/old')
        (self.frontend / 'previous').symlink_to('releases/older')
        self.old = self.root / 'managed.py'
        self.old.write_text('value = "old"\n')
        self.receiver = self.root / '30-openwrt-dendelion.conf'
        self.receiver.write_text('old receiver')
        self.new = self.root / 'new.py'
        self.database = self.root / 'events.sqlite3'
        self.database.write_bytes(b'untouched database sentinel')
        self.targets = {'old.py':str(self.old), 'new.py':str(self.new), 'receiver.conf':str(self.receiver)}
        self.contents = {'old.py':b'value = "new"\n', 'new.py':b'new = True\n', 'receiver.conf':b'new receiver'}
        self.states = {name:dict(enabled='enabled',active=True) for name in deploy.SERVICES}
        self.states['network-flight-recorder'] = dict(enabled='not-found',active=False)
        self.calls = []
        original_info = deploy.regular_info
        def info(path):
            if str(path) == '/home/pi/scripts/system_event_monitor.py':
                return dict(sha256='dependency',mode=0o644,uid=0,gid=0)
            return original_info(path)
        def run(args, check=True):
            self.calls.append(args)
            if args[0] == '/usr/bin/systemctl' and args[1] in ('restart','stop','enable','disable'):
                name = args[-1]
                if args[1] in ('restart','stop'):
                    self.states[name]['active'] = args[1] == 'restart'
                else:
                    self.states[name]['enabled'] = 'enabled' if args[1] == 'enable' else 'disabled'
            return subprocess.CompletedProcess(args,0,'{"ok":true}','')
        patches = [mock.patch.object(deploy,'TARGETS',self.targets),
                   mock.patch.object(deploy,'FRONTEND',self.frontend),
                   mock.patch.object(deploy,'STORAGE_CONFIG',self.root / 'storage.json'),
                   mock.patch.object(deploy,'PACKAGE_ACTIVATION',self.root / 'activated.json'),
                   mock.patch.object(deploy,'DEPLOY_ROOT',self.root / 'backups'),
                   mock.patch.object(deploy,'regular_info',side_effect=info),
                   mock.patch.object(deploy,'command',side_effect=run),
                   mock.patch.object(deploy,'service_state',side_effect=lambda name:copy.deepcopy(self.states[name])),
                   mock.patch('pwd.getpwnam',return_value=types.SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())),
                   mock.patch.object(deploy.os,'chown')]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.directory.cleanup)
        self.plan = dict(schema_version=1,release='network-test',monitor_dependency='dependency',target='fixture',files=[],
                         frontend_files=[dict(relative='index.html',sha256=deploy.sha(b'new frontend'))])
        for source,destination in self.targets.items():
            before = original_info(destination)
            self.plan['files'].append(dict(source=source,destination=destination,sha256=deploy.sha(self.contents[source]),
                                           baseline_sha256=before['sha256'] if before else None))

    def stage(self, malformed=False):
        directory = tempfile.TemporaryDirectory(prefix='network-recorder-stage.', dir='/tmp')
        self.addCleanup(directory.cleanup)
        path = Path(directory.name)
        with tarfile.open(path / 'payload.tar', 'w') as archive:
            for index, entry in enumerate(self.plan['files']):
                source = self.root / ('payload-' + str(index))
                source.write_bytes(self.contents[entry['source']])
                archive.add(source,arcname='files/' + str(index))
            source = self.root / 'index.html'
            source.write_bytes(b'new frontend')
            archive.add(source,arcname='frontend/index.html')
            if malformed:
                archive.add(source,arcname='../escape')
        return path

    def test_install_and_exact_rollback_preserve_database_and_unmanaged_files(self):
        deploy.inspect_remote(self.plan)
        original = copy.deepcopy(self.plan['services_before'])
        result = deploy.apply_remote(self.plan,self.stage())
        self.assertTrue(result['ok'])
        self.assertEqual(self.old.read_text(),'value = "new"\n')
        self.assertEqual(os.readlink(self.frontend / 'current'),'releases/network-test')
        self.assertTrue(self.states['network-flight-recorder']['active'])
        self.assertEqual(self.database.read_bytes(),b'untouched database sentinel')
        deploy.rollback_remote('network-test')
        self.assertEqual(self.old.read_text(),'value = "old"\n')
        self.assertEqual(self.receiver.read_text(),'old receiver')
        self.assertFalse(self.new.exists())
        self.assertEqual(os.readlink(self.frontend / 'current'),'releases/old')
        self.assertEqual(os.readlink(self.frontend / 'previous'),'releases/older')
        for name in deploy.SERVICES:
            self.assertEqual(self.states[name]['active'],original[name]['active'])
        self.assertEqual(self.database.read_bytes(),b'untouched database sentinel')

    def test_remote_difference_fails_before_staging_or_service_changes(self):
        self.old.write_text('user edit')
        with self.assertRaisesRegex(ValueError,'live files differ'):
            deploy.inspect_remote(self.plan)
        self.assertEqual(self.calls,[])

    def test_legacy_deployer_refuses_after_storage_migration(self):
        deploy.STORAGE_CONFIG.write_text('{}')
        with self.assertRaisesRegex(ValueError,'storage has migrated'):
            deploy.inspect_remote(self.plan)
        with self.assertRaisesRegex(ValueError,'storage has migrated'):
            deploy.apply_remote(self.plan,self.stage())
        with self.assertRaisesRegex(ValueError,'storage has migrated'):
            deploy.rollback_remote('network-test')
        self.assertEqual(self.calls,[])

    def test_package_activation_refuses_all_protected_legacy_entrypoints(self):
        self.activation.write_text('not json')
        with self.assertRaisesRegex(ValueError, 'deploy_network_storage.py.*deploy_python.py --update'):
            deploy.inspect_remote(self.plan)
        with self.assertRaisesRegex(ValueError, 'deploy_network_storage.py.*deploy_python.py --update'):
            deploy.apply_remote(self.plan, self.stage())
        with self.assertRaisesRegex(ValueError, 'deploy_network_storage.py.*deploy_python.py --update'):
            deploy.rollback_remote('network-test')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.old.read_text(), 'value = "old"\n')

        self.activation.unlink()
        deploy.inspect_remote(self.plan)
        self.calls.clear()
        self.activation.write_text('still not json')
        with self.assertRaisesRegex(ValueError, 'deploy_network_storage.py.*deploy_python.py --update'):
            deploy.verify_remote(self.plan)
        self.assertEqual(self.calls, [])

    def test_storage_guard_remains_in_verify(self):
        deploy.inspect_remote(self.plan)
        self.calls.clear()
        deploy.STORAGE_CONFIG.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'storage has migrated'):
            deploy.verify_remote(self.plan)
        self.assertEqual(self.calls, [])

    def test_missing_activation_marker_allows_legacy_behavior(self):
        self.assertFalse(self.activation.exists())
        self.assertIs(deploy.inspect_remote(self.plan), self.plan)

    def test_activation_after_plan_check_is_refused_before_apply(self):
        deploy.inspect_remote(self.plan)
        self.calls.clear()
        self.activation.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'deploy_network_storage.py.*deploy_python.py --update'):
            deploy.apply_remote(self.plan, self.stage())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.old.read_text(), 'value = "old"\n')

    def test_activation_before_second_verify_prevents_service_mutations(self):
        deploy.inspect_remote(self.plan)
        self.calls.clear()
        original_verify = deploy.verify_remote
        verify_count = 0

        def verify(plan):
            nonlocal verify_count
            verify_count += 1
            result = original_verify(plan)
            if verify_count == 1:
                self.activation.write_text('{}')
            return result

        with mock.patch.object(deploy, 'verify_remote', side_effect=verify):
            with self.assertRaisesRegex(ValueError, 'deploy_network_storage.py.*deploy_python.py --update'):
                deploy.apply_remote(self.plan, self.stage())
        self.assertEqual(verify_count, 2)
        self.assertFalse(any(args and args[0] == '/usr/bin/systemctl' for args in self.calls))
        self.assertEqual(self.old.read_text(), 'value = "old"\n')

    def test_legacy_rollback_refuses_after_activation(self):
        deploy.inspect_remote(self.plan)
        deploy.apply_remote(self.plan, self.stage())
        self.activation.write_text('{}')
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, 'deploy_network_storage.py.*deploy_python.py --update'):
            deploy.rollback_remote('network-test')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.old.read_text(), 'value = "new"\n')
        self.assertEqual(os.readlink(self.frontend / 'current'), 'releases/network-test')

    def test_automatic_rollback_cannot_overwrite_activated_host(self):
        deploy.inspect_remote(self.plan)
        actual_systemctl = deploy.systemctl

        def fail_after_activation(*args):
            if args == ('restart', 'rsyslog'):
                self.activation.write_text('{}')
                raise RuntimeError('fixture activation failure')
            return actual_systemctl(*args)

        with mock.patch.object(deploy, 'systemctl', side_effect=fail_after_activation):
            with self.assertRaisesRegex(ValueError, 'deploy_network_storage.py.*deploy_python.py --update'):
                deploy.apply_remote(self.plan, self.stage())
        self.assertEqual(self.old.read_text(), 'value = "new"\n')
        self.assertEqual(self.receiver.read_text(), 'new receiver')
        self.assertEqual(os.readlink(self.frontend / 'current'), 'releases/network-test')

    def test_marker_presence_refuses_malformed_directory_and_dangling_symlink(self):
        for kind in ('malformed', 'directory', 'dangling symlink'):
            with self.subTest(kind=kind):
                if kind == 'malformed':
                    self.activation.write_text('{not json')
                elif kind == 'directory':
                    self.activation.mkdir()
                else:
                    self.activation.symlink_to(self.root / 'missing-target')
                with self.assertRaisesRegex(ValueError, 'deploy_network_storage.py.*deploy_python.py --update'):
                    deploy.inspect_remote(self.plan)
                if self.activation.is_dir():
                    self.activation.rmdir()
                else:
                    self.activation.unlink()

    def test_marker_lstat_errors_propagate(self):
        marker = mock.Mock()
        marker.lstat.side_effect = PermissionError('marker unavailable')
        with mock.patch.object(deploy, 'PACKAGE_ACTIVATION', marker):
            with self.assertRaisesRegex(PermissionError, 'marker unavailable'):
                deploy.inspect_remote(self.plan)

    def test_changed_file_after_review_is_not_overwritten(self):
        deploy.inspect_remote(self.plan)
        self.old.write_text('later edit')
        with self.assertRaisesRegex(ValueError,'changed after check'):
            deploy.apply_remote(self.plan,self.stage())
        self.assertEqual(self.old.read_text(),'later edit')
        self.assertEqual(self.calls,[])

    def test_archive_path_escape_rejected_before_any_install(self):
        deploy.inspect_remote(self.plan)
        with self.assertRaisesRegex(ValueError,'unexpected files'):
            deploy.apply_remote(self.plan,self.stage(malformed=True))
        self.assertEqual(self.old.read_text(),'value = "old"\n')
        self.assertEqual(self.calls,[])

    def test_rollback_preserves_changes_made_after_deployment(self):
        deploy.inspect_remote(self.plan)
        deploy.apply_remote(self.plan,self.stage())
        self.old.write_text('operator change')
        with self.assertRaisesRegex(ValueError,'changed since'):
            deploy.rollback_remote('network-test')
        self.assertEqual(self.old.read_text(),'operator change')

    def test_service_failure_automatically_restores_prior_files(self):
        deploy.inspect_remote(self.plan)
        actual = deploy.systemctl
        def fail_restart(*args):
            if args == ('restart','network-flight-recorder'):
                raise RuntimeError('fixture start failure')
            return actual(*args)
        with mock.patch.object(deploy,'systemctl',side_effect=fail_restart):
            with self.assertRaisesRegex(RuntimeError,'fixture start failure'):
                deploy.apply_remote(self.plan,self.stage())
        self.assertEqual(self.old.read_text(),'value = "old"\n')
        self.assertFalse(self.new.exists())
        self.assertEqual(os.readlink(self.frontend / 'current'),'releases/old')
        self.assertEqual(self.database.read_bytes(),b'untouched database sentinel')

    def test_update_accepts_only_proven_prior_deployment(self):
        deploy.inspect_remote(self.plan)
        deploy.apply_remote(self.plan,self.stage())
        update = copy.deepcopy(self.plan)
        update['release'] = 'network-next'
        update['files'][0]['sha256'] = deploy.sha(b'value = "next"\n')
        deploy.inspect_remote(update)
        self.assertEqual(update['files'][0]['before']['sha256'],deploy.sha(b'value = "new"\n'))
        self.old.write_text('unrelated deployed modification')
        with self.assertRaisesRegex(ValueError,'live files differ'):
            deploy.inspect_remote(update)

    def test_cleanup_refuses_symlink_without_deleting_payload(self):
        stage = self.stage()
        (stage / 'link').symlink_to(self.root)
        with self.assertRaisesRegex(ValueError,'unexpected staging contents'):
            deploy.cleanup_stage(stage)
        self.assertTrue((stage / 'payload.tar').is_file())


if __name__ == '__main__':
    unittest.main()
