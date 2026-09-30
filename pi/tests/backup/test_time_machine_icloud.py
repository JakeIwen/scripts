#!/usr/bin/env python3
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'pi/scripts/backup'))
import time_machine_store as store
import time_machine_icloud as job
import icloud_backup as cloud

spec = importlib.util.spec_from_file_location('coordinator', REPO / 'macbook/scripts/time_machine_offsite_coordinator.py')
coordinator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coordinator)
GEN = 'tm-20260930T010203Z-12345678'


def source_fixture(root):
    source = root / store.BUNDLE
    source.mkdir()
    (source / 'bands').mkdir(); (source / 'mapped').mkdir()
    (source / 'bands/0').write_bytes(b'encrypted band one')
    (source / 'bands/1').write_bytes(b'encrypted band two')
    (source / 'mapped/0').write_bytes(b'encrypted bitmap')
    (source / 'token').write_bytes(b'encrcdsa' + b'encrypted key metadata')
    (source / 'lock').write_bytes(b'never copy runtime lock')
    (source / 'Info.plist').write_bytes(plistlib.dumps({'band-size': 67108864, 'bundle-backingstore-version': 2}))
    (source / 'com.apple.TimeMachine.SnapshotHistory.plist').write_bytes(plistlib.dumps({
        'Snapshots': [{'com.apple.backupd.SnapshotCompletionDate': dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)}]}))
    return source


class StoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = source_fixture(self.root)
        self.store = self.root / 'store'; self.store.mkdir()

    def freeze(self, **kwargs):
        return store.freeze(self.source, self.store, GEN, 72, 0, kwargs.get('check', lambda: None), lambda *a: None)

    def test_freeze_does_not_link_mutable_bands(self):
        manifest = self.freeze()
        self.assertNotIn('lock', manifest['files'])
        row = manifest['files']['bands/0']
        obj = self.store / 'objects' / row['sha256']
        self.assertNotEqual(obj.stat().st_ino, (self.source / 'bands/0').stat().st_ino)
        (self.source / 'bands/0').write_bytes(b'rewritten by Time Machine')
        self.assertEqual(obj.read_bytes(), b'encrypted band one')
        self.assertEqual(obj.stat().st_mode & 0o777, 0o400)
        self.assertEqual(store.validate_manifest(manifest)[row['sha256']], row)

    def test_unchanged_bands_are_reused_and_changed_files_copied(self):
        original = self.freeze()
        with mock.patch.object(store, 'copy_object', wraps=store.copy_object) as copy:
            self.freeze()
            self.assertEqual(copy.call_count, 0)
            (self.source / 'bands/0').write_bytes(b'new encrypted contents')
            new = self.freeze()
            self.assertEqual(copy.call_count, 1)
        self.assertNotEqual(original['files']['bands/0'], new['files']['bands/0'])
        self.assertEqual(original['files']['bands/1'], new['files']['bands/1'])

    def test_changed_source_is_not_published(self):
        original = store.copy_object
        def change_after_copy(source, objects, check):
            result = original(source, objects, check)
            if source.name == '1':
                (self.source / 'bands/0').write_bytes(b'changed after its copy')
            return result
        with mock.patch.object(store, 'copy_object', side_effect=change_after_copy):
            with self.assertRaisesRegex(RuntimeError, 'changed across'):
                self.freeze()
        self.assertFalse((self.store / 'pending.json').exists())

    def test_refuses_links_unencrypted_or_stale_images(self):
        (self.source / 'bands/evil').symlink_to(self.source / 'token')
        with self.assertRaises(ValueError): self.freeze()
        (self.source / 'bands/evil').unlink()
        (self.source / 'token').write_bytes(b'plaintext')
        with self.assertRaisesRegex(ValueError, 'encrypted'): self.freeze()
        (self.source / 'token').write_bytes(b'encrcdsa')
        with self.assertRaisesRegex(ValueError, 'stale'):
            store.source_evidence(self.source, 1, now=time.time() + 7200)

    def test_cancellation_leaves_no_partial_manifest_or_object(self):
        with self.assertRaises(cloud.Deferred):
            self.freeze(check=mock.Mock(side_effect=cloud.Deferred('ignition')))
        self.assertFalse((self.store / 'pending.json').exists())
        self.assertFalse(list((self.store / 'objects').glob('.capture-*')))

    def test_disk_headroom_stops_before_copy(self):
        with mock.patch.object(store.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 0})()):
            with self.assertRaisesRegex(RuntimeError, 'headroom'): self.freeze()

    def publish_fixture(self, manifest):
        gen = self.store / 'generations' / GEN; gen.mkdir(parents=True)
        store.atomic_json(gen / 'manifest.json', manifest)
        store.atomic_json(gen / '_COMPLETE.json', {'owner': store.OWNER, 'generation': GEN,
            'verification': 'sha256-download-compared',
            'manifest_sha256': hashlib.sha256((gen / 'manifest.json').read_bytes()).hexdigest()})

    def test_offline_restore_matches_every_source_file_and_is_independent(self):
        manifest = self.freeze(); self.publish_fixture(manifest)
        destination = self.root / 'restored.sparsebundle'
        store.restore(self.store, GEN, destination)
        for rel, entry in manifest['files'].items():
            self.assertEqual((destination / rel).read_bytes(), (self.source / rel).read_bytes())
            self.assertNotEqual((destination / rel).stat().st_ino, (self.store / 'objects' / entry['sha256']).stat().st_ino)
        with self.assertRaises(ValueError): store.restore(self.store, GEN, destination)

    def test_restore_detects_corrupt_cloud_objects(self):
        manifest = self.freeze(); self.publish_fixture(manifest)
        row = manifest['files']['bands/0']
        obj = self.store / 'objects' / row['sha256']; obj.chmod(0o600)
        obj.write_bytes(b'X' * row['bytes'])
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            store.restore(self.store, GEN, self.root / 'restored.sparsebundle')
        self.assertFalse((self.root / 'restored.sparsebundle').exists())

    def test_manifest_paths_cannot_escape_or_alias(self):
        manifest = self.freeze()
        for path in ('../outside', '/absolute', 'bands/../bad', 'bands//0', 'bands\\bad', 'bad\nname'):
            broken = {**manifest, 'files': {**manifest['files'], path: manifest['files']['token']}}
            with self.assertRaises(ValueError): store.validate_manifest(broken)
        with self.assertRaises(ValueError): store.validate_manifest({**manifest, 'generation': '../evil'})


class CaptureGateTests(unittest.TestCase):
    def test_missing_mac_helper_never_blocks_normal_time_machine(self):
        auth = self.root / 'auth'; auth.mkdir(); (auth / 'authenticated.json').touch()
        with mock.patch.object(job, 'AUTH', auth), mock.patch.object(job, 'ROOT', self.root), mock.patch.object(job, 'storage_root'), mock.patch.object(cloud, 'credentials_ready', return_value=True), mock.patch.object(cloud, 'guard'), mock.patch.object(job, 'quiesced') as gate:
            with self.assertRaisesRegex(cloud.Deferred, 'coordinator unavailable'):
                job.weekly({'interval_days':7}, {'pending':GEN})
            gate.assert_not_called()
            self.assertFalse(self.drain.exists())

    def test_mac_heartbeat_wakes_missing_helper_deferral_but_not_current_backup(self):
        store.atomic_json(self.state_dir / 'state.json', {'pending':GEN, 'last_attempt_at':time.time(), 'last_error':'Mac capture coordinator unavailable'})
        with mock.patch.object(job, 'config', return_value={'interval_days':7}), mock.patch.object(job.subprocess, 'run') as command:
            self.assertTrue(job.mac_present()['ok'])
            self.assertIn('--no-block', command.call_args.args[0])
            store.atomic_json(self.state_dir / 'state.json', {'last_success_at':time.time()})
            command.reset_mock(); job.mac_present(); command.assert_not_called()

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state_dir = self.root / 'state'; self.state_dir.mkdir()
        self.drain = self.root / 'gate'
        for name, value in (('STATE', self.state_dir), ('DRAIN', self.drain)):
            patch = mock.patch.object(job, name, value); patch.start(); self.addCleanup(patch.stop)
        self.cfg = {'capture_wait_seconds': 30, 'capture_max_seconds': 60, 'guard_interval_seconds': 5}
        self.state = {'pending': GEN}
        for name in ('exact_mount', 'check_work_allowed', 'record_progress'):
            patch = mock.patch.object(cloud, name); patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(job, 'worker_alive', return_value=False); patch.start(); self.addCleanup(patch.stop)

    def gate_context(self):
        # Test root may be owned by an unprivileged Mac runner.
        real_stat = Path.stat
        root = self.root
        def checked_stat(path, *args, **kwargs):
            s = real_stat(path, *args, **kwargs)
            if path == root:
                values = list(s); values[4] = 0
                return os.stat_result(values)
            return s
        return mock.patch.object(Path, 'stat', checked_stat)

    def ack_on_sleep(self, seconds):
        request = store.read_json(self.state_dir / 'capture.json', {})
        if request:
            store.atomic_json(self.state_dir / 'capture-ready.json', {'capture_id': request['capture_id'], 'generation': GEN})

    def test_gate_released_on_copy_failure_after_clean_ack(self):
        if not Path('/proc/self/stat').exists(): self.skipTest('Linux process evidence')
        with self.gate_context(), mock.patch.object(job.time, 'sleep', side_effect=self.ack_on_sleep), mock.patch.object(job, 'smb_state', side_effect=[(0, 1), (0, 0), (0, 0)]), mock.patch.object(cloud, 'capture') as command:
            with self.assertRaisesRegex(RuntimeError, 'test capture failure'):
                with job.quiesced(self.cfg, self.state):
                    self.assertTrue(self.drain.exists())
                    raise RuntimeError('test capture failure')
            command.assert_called_once_with(['/usr/bin/smbcontrol', 'smbd', 'close-share', 'mbp2tbkup'])
        self.assertFalse(self.drain.exists())

    def test_never_closes_share_with_open_image_handles(self):
        if not Path('/proc/self/stat').exists(): self.skipTest('Linux process evidence')
        self.cfg['smb_handle_drain_seconds'] = 0
        with self.gate_context(), mock.patch.object(job.time, 'sleep', side_effect=self.ack_on_sleep), mock.patch.object(job, 'smb_state', return_value=(3, 1)), mock.patch.object(cloud, 'capture') as command:
            with self.assertRaises(cloud.Deferred):
                with job.quiesced(self.cfg, self.state): self.fail('unsafe capture started')
            command.assert_not_called()
        self.assertFalse(self.drain.exists())

    def test_deferred_smb_closes_are_allowed_to_drain_without_forcing(self):
        self.drain.write_text('our gate')
        with mock.patch.object(job, 'smb_state', side_effect=[(5, 1), (2, 1), (0, 1)]), mock.patch.object(job.time, 'sleep') as sleep, mock.patch.object(cloud, 'capture') as command:
            job.wait_image_handles(self.cfg, self.state, 'our gate')
            self.assertEqual(sleep.call_count, 2)
            command.assert_not_called()
        self.assertTrue(self.drain.exists())

    def test_handle_drain_fails_closed_if_probe_fails_or_gate_disappears(self):
        self.drain.write_text('our gate')
        with mock.patch.object(job, 'smb_state', side_effect=RuntimeError('probe failed')):
            with self.assertRaises(RuntimeError): job.wait_image_handles(self.cfg, self.state, 'our gate')
        self.drain.unlink()
        with mock.patch.object(job, 'smb_state', return_value=(1, 1)):
            with self.assertRaises(cloud.Deferred): job.wait_image_handles(self.cfg, self.state, 'our gate')

    def test_timeout_releases_gate_without_smb_disconnect(self):
        if not Path('/proc/self/stat').exists(): self.skipTest('Linux process evidence')
        self.cfg['capture_wait_seconds'] = 0
        with self.gate_context(), mock.patch.object(cloud, 'capture') as command:
            with self.assertRaises(cloud.Deferred):
                with job.quiesced(self.cfg, self.state): self.fail('capture started without Mac')
            command.assert_not_called()
        self.assertFalse(self.drain.exists())

    def test_cleanup_preserves_unrelated_shutdown_marker(self):
        self.drain.write_text('disk shutdown')
        store.atomic_json(self.state_dir / 'capture.json', {'marker': 'our old marker'})
        job.release_capture()
        self.assertEqual(self.drain.read_text(), 'disk shutdown')

    def test_rejects_old_nonce_and_generation(self):
        with mock.patch.object(job, 'capture_request', return_value={'requested': True, 'generation': GEN, 'capture_id': 'a'*32}):
            self.assertFalse(job.acknowledge(GEN, 'b'*32)['accepted'])
            self.assertTrue(job.acknowledge(GEN, 'a'*32)['accepted'])


class VerificationTests(unittest.TestCase):
    def test_completion_publication_is_idempotent_after_readback_interruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = source_fixture(root)
            local = root / 'store'; local.mkdir(); (local / 'generations').mkdir()
            manifest = store.freeze(source, local, GEN, 72, 0, lambda: None, lambda *a: None)
            expected = store.validate_manifest(manifest)
            inventory = {k: {'IsDir': False, 'Size': v['bytes'], 'ModTime': 'now'} for k,v in expected.items()}
            remote_files = {}; interrupt = [True]
            def net(cfg, op, *args, **kwargs):
                if op == 'copy': return ''
                if op == 'copyto':
                    data = Path(args[0]).read_bytes(); destination = args[1]
                    if '--immutable' in args and destination in remote_files and remote_files[destination] != data:
                        raise RuntimeError('immutable metadata changed')
                    remote_files[destination] = data
                    return ''
                if op == 'cat':
                    if args[0].endswith('/_COMPLETE.json') and interrupt[0]:
                        interrupt[0] = False
                        raise cloud.Deferred('path changed just after marker upload')
                    return remote_files[args[0]].decode()
                self.fail('unexpected operation '+op)
            cfg = {'remote':'icloud:test', 'max_cloud_store_gib':900}
            state = {'pending':GEN}
            with mock.patch.object(job, 'ROOT', local), mock.patch.object(job, 'private_directory', side_effect=lambda p:p.mkdir(exist_ok=True)), mock.patch.object(job, 'ensure_remote'), mock.patch.object(job, 'verify_objects'), mock.patch.object(job, 'network', side_effect=net), mock.patch.object(cloud, 'remote_inventory', return_value=inventory), mock.patch.object(cloud, 'record_progress'):
                with self.assertRaises(cloud.Deferred): job.transfer(cfg, manifest, state)
                marker = remote_files['icloud:test/generations/'+GEN+'/_COMPLETE.json']
                job.transfer(cfg, manifest, state)
                self.assertEqual(marker, remote_files['icloud:test/generations/'+GEN+'/_COMPLETE.json'])
                self.assertIsNone(state['pending'])
                self.assertGreater(state['last_success_at'], 0)

    def test_store_budget_defers_without_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = source_fixture(root)
            local = root / 'store'; local.mkdir()
            manifest = store.freeze(source, local, GEN, 72, 0, lambda: None, lambda *a: None)
            with mock.patch.object(job, 'ROOT', local), mock.patch.object(job, 'ensure_remote'), mock.patch.object(cloud, 'remote_inventory', return_value={}), mock.patch.object(job, 'network') as network:
                with self.assertRaises(cloud.Deferred): job.transfer({'remote':'icloud:test','max_cloud_store_gib':0}, manifest, {})
                network.assert_not_called()

    def test_unrecognized_retirement_targets_never_deleted(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(job, 'ROOT', Path(tmp)), mock.patch.object(job, 'network', return_value=json.dumps([{'IsDir':True,'Name':'../outside'}])) as network:
            with self.assertRaises(RuntimeError): job.retention({'remote':'icloud:test','keep_generations':2}, GEN)
            self.assertEqual(network.call_count, 1)

    def test_retirement_resumes_after_old_metadata_was_already_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = source_fixture(root)
            local = root / 'store'; local.mkdir()
            manifest = store.freeze(source, local, GEN, 72, 0, lambda:None, lambda *a:None)
            retired = 'tm-20260901T010203Z-12345678'
            prior = 'tm-20260908T010203Z-12345678'
            store.atomic_json(local / 'retiring.json', [retired])
            documents = {}
            for name in (prior, GEN):
                data = json.dumps({**manifest, 'generation':name})
                documents[name] = {'manifest.json':data, '_COMPLETE.json':json.dumps({
                    'owner':store.OWNER,'generation':name,'verification':'sha256-download-compared',
                    'manifest_sha256':hashlib.sha256(data.encode()).hexdigest()})}
            calls = []
            def net(cfg, op, path, *args, **kwargs):
                calls.append((op,path))
                if op == 'lsjson' and path.endswith('/generations'):
                    return json.dumps([{'Name':name,'IsDir':True} for name in (retired,prior,GEN)])
                if op == 'lsjson':
                    name=path.rsplit('/',1)[1]
                    return json.dumps([{'Name':key} for key in documents.get(name,{})])
                if op == 'cat':
                    name,filename=path.rsplit('/',2)[-2:]
                    return documents[name][filename]
                if op == 'rmdir': return ''
                self.fail('unexpected cloud deletion: '+op+' '+path)
            inventory={k:{'IsDir':False,'Size':v['bytes'],'ModTime':'now'} for k,v in store.validate_manifest(manifest).items()}
            with mock.patch.object(job,'ROOT',local), mock.patch.object(job,'network',side_effect=net), mock.patch.object(cloud,'remote_inventory',return_value=inventory), mock.patch.object(cloud,'exact_mount'):
                job.retention({'remote':'icloud:test','keep_generations':2},GEN)
            self.assertIn(('rmdir','icloud:test/generations/'+retired),calls)
            self.assertEqual(store.read_json(local/'retiring.json'),[])
            self.assertFalse(any(op=='deletefile' for op,path in calls))

    def test_completed_object_checkpoints_survive_interruption(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(job, 'ROOT', Path(tmp)), mock.patch.object(cloud, 'record_progress'):
            data = [b'a', b'bb']
            expected = {hashlib.sha256(v).hexdigest(): {'bytes': len(v), 'sha256': hashlib.sha256(v).hexdigest()} for v in data}
            rows = {k: {'IsDir': False, 'Size': v['bytes'], 'ModTime': 'now', 'ID': k} for k, v in expected.items()}
            calls = []
            def network(cfg, operation, path, **kwargs):
                digest = path.rsplit('/', 1)[1]; calls.append(digest)
                if len(calls) == 2: raise cloud.Deferred('uplink changed')
                return expected[digest]
            cfg = {'remote': 'icloud:test'}
            with mock.patch.object(cloud, 'remote_inventory', return_value=rows), mock.patch.object(job, 'network', side_effect=network):
                with self.assertRaises(cloud.Deferred): job.verify_objects(cfg, expected, {})
            first = calls[0]; calls.clear()
            with mock.patch.object(cloud, 'remote_inventory', return_value=rows), mock.patch.object(job, 'network', side_effect=network):
                job.verify_objects(cfg, expected, {})
            self.assertNotIn(first, calls)
            self.assertEqual(len(calls), 1)
            with mock.patch.object(cloud, 'remote_inventory', return_value=rows), mock.patch.object(job, 'network') as net:
                job.verify_objects(cfg, expected, {})
                net.assert_not_called()

    def test_changed_remote_invalidates_verification(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(job, 'ROOT', Path(tmp)), mock.patch.object(cloud, 'record_progress'):
            digest = hashlib.sha256(b'a').hexdigest()
            expected = {digest: {'bytes': 1, 'sha256': digest}}
            before = {digest: {'IsDir': False, 'Size': 1, 'ModTime': 'one'}}
            after = {digest: {'IsDir': False, 'Size': 1, 'ModTime': 'two'}}
            with mock.patch.object(cloud, 'remote_inventory', side_effect=[before, after]), mock.patch.object(job, 'network', return_value=expected[digest]):
                with self.assertRaises(cloud.Deferred): job.verify_objects({'remote': 'icloud:test'}, expected, {})
            self.assertNotIn(digest, store.read_json(Path(tmp) / 'verified-objects.json'))


class CoordinatorTests(unittest.TestCase):
    def test_image_identity_requires_exact_host_share_and_bundle(self):
        for path in ('/Volumes/.timemachine/not-vanpi.local/id/mbp2tbkup/'+store.BUNDLE,
                     '/Volumes/.timemachine/vanpi.lan/id/other/'+store.BUNDLE,
                     '/Volumes/random/vanpi.lan/mbp2tbkup/'+store.BUNDLE):
            self.assertEqual(coordinator.matching_images({'images':[{'image-path':path}]}, store.BUNDLE), [])
    def test_active_backup_is_not_stopped_or_detached(self):
        request = {'requested': True, 'generation': GEN, 'capture_id': 'a'*32,
                   'expires_at': time.time()+60, 'bundle': store.BUNDLE}
        with mock.patch.object(coordinator, 'remote', return_value=request), mock.patch.object(coordinator, 'command', return_value=b'Running = 1;') as command:
            self.assertIn('active', coordinator.coordinate({'user': 'test'}))
            command.assert_called_once_with(['/usr/bin/tmutil', 'status'])

    def test_only_matching_encrypted_image_is_nonforce_detached(self):
        request = {'requested': True, 'generation': GEN, 'capture_id': 'a'*32,
                   'expires_at': time.time()+60, 'bundle': store.BUNDLE}
        image = {'image-path': '/Volumes/.timemachine/VANPI.local/id/mbp2tbkup/' + store.BUNDLE,
                 'image-encrypted': True, 'system-entities': [{'dev-entry': '/dev/disk44'}]}
        with mock.patch.object(coordinator, 'remote', side_effect=[{'ok':True}, request, {'accepted': True}]) as remote, mock.patch.object(coordinator, 'command', side_effect=[b'Running = 0;', plistlib.dumps({'images': [image]}), b'', plistlib.dumps({'images': []})]) as command:
            self.assertIn('cleanly detached', coordinator.coordinate({'user': 'test'}))
            self.assertEqual(command.call_args_list[2].args[0], ['/usr/bin/hdiutil', 'detach', '/dev/disk44'])
            self.assertEqual(remote.call_args.args, ('test', '--capture-ready', GEN, 'a'*32))

    def test_unreadable_mac_status_never_acknowledges_capture(self):
        request = {'requested': True, 'generation': GEN, 'capture_id': 'a'*32,
                   'expires_at': time.time()+60, 'bundle': store.BUNDLE}
        with mock.patch.object(coordinator, 'remote', return_value=request) as remote, mock.patch.object(coordinator, 'command', return_value=b'Unknown'):
            with self.assertRaises(RuntimeError): coordinator.coordinate({'user': 'test'})
            self.assertEqual(remote.call_count, 0)


if __name__ == '__main__':
    unittest.main()
