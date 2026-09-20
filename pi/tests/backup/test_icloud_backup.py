import json
import os
import signal
import subprocess
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

# Tests live under pi/tests/backup; locate the Pi root independently of cwd.
PI = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PI / 'scripts' / 'backup'))
import icloud_backup as backup
from icloud_uplink import decide


def evidence():
    return {'checked_at': 1000, 'local_route': {'gateway': '192.168.6.1', 'dev': 'eth0', 'prefsrc': '192.168.6.103'},
            'router': {'reachable': True, 'default_policy': 'balanced', 'route_members': [{'name': 'wan', 'percent': 100}]},
            'rule_names': ['default_rule_v4'],
            'ubnt': {'reachable': True, 'connected': True, 'ssid': 'FriendsWifi'},
            'wireless_uplinks': {'clientwan': {'up': True, 'ssid': 'PhoneHotspot'}}}


class UplinkTests(unittest.TestCase):
    def check(self, value):
        return decide(value, ['denlink'], now=1000)[0]

    def test_allows_antenna_on_non_starlink(self):
        self.assertTrue(self.check(evidence()))

    def test_denlink_and_starlink_names_block(self):
        for ssid in ('denlink', 'DENLINK', 'Starlink', 'My Starlink Mini'):
            e = evidence(); e['ubnt']['ssid'] = ssid
            self.assertFalse(self.check(e))

    def test_hotspot_allowed_even_when_standby_antenna_is_starlink(self):
        e = evidence(); e['ubnt']['ssid'] = 'denlink'
        e['router']['route_members'] = [{'name': 'clientwan', 'percent': 100}]
        self.assertTrue(self.check(e))

    def test_starlink_hotspot_is_blocked(self):
        e = evidence(); e['router']['route_members'] = [{'name': 'clientwan', 'percent': 100}]
        e['wireless_uplinks']['clientwan']['ssid'] = 'denlink'
        self.assertFalse(self.check(e))

    def test_balanced_route_with_any_starlink_member_is_blocked(self):
        e = evidence(); e['ubnt']['ssid'] = 'denlink'
        e['router']['route_members'] = [{'name': 'wan', 'percent': 50}, {'name': 'clientwan', 'percent': 50}]
        self.assertFalse(self.check(e))

    def test_unknowns_and_stale_evidence_fail_closed(self):
        for mutate in (lambda e:e.update(checked_at=900), lambda e:e.update(checked_at=1010),
                       lambda e:e['ubnt'].update(connected=False), lambda e:e['ubnt'].update(ssid=None),
                       lambda e:e.update(rule_names=['default_rule_v4','custom_source_rule']),
                       lambda e:e['router'].update(route_members=[]), lambda e:e['router'].update(reachable=False),
                       lambda e:e['local_route'].update(dev='tailscale0'), lambda e:e['local_route'].update(gateway='10.0.0.1')):
            e = evidence(); mutate(e)
            self.assertFalse(self.check(e))
        self.assertFalse(self.check({}))


class SnapshotTests(unittest.TestCase):
    @unittest.skipUnless(Path('/usr/bin/borg').is_file(), 'Borg integration runs on the Pi')
    def test_real_encrypted_repository_roundtrip(self):
        cfg=json.loads((PI/'configs'/'icloud-backup.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); repo=root/'source'; stage=root/'stage'; stage.mkdir()
            (root/'fixture.txt').write_text('offsite restoration fixture\n')
            env={k:v for k,v in os.environ.items() if not k.startswith('BORG_')}
            env.update(BORG_PASSPHRASE='test-fixture-only', BORG_CACHE_DIR=str(root/'cache'), BORG_SECURITY_DIR=str(root/'security'))
            subprocess.run(['/usr/bin/borg','init','--encryption=repokey-blake2',str(repo)],env=env,check=True,capture_output=True)
            subprocess.run(['/usr/bin/borg','create',str(repo)+'::fixture','fixture.txt'],cwd=root,env=env,check=True,capture_output=True)
            name='vanpi-20260920T010203Z-12345678'
            with mock.patch.dict(os.environ,env,clear=True), mock.patch.object(backup,'check_parked'):
                backup.make_snapshot(cfg,stage,repo,name)
            copied=stage/name/'payload'/'repository'
            self.assertFalse((copied/'lock.exclusive').exists())
            # Model replacement hardware: use a fresh cache/security database,
            # not the source host's repository-location anti-tampering record.
            restore_env=dict(env,BORG_CACHE_DIR=str(root/'restore-cache'),BORG_SECURITY_DIR=str(root/'restore-security'))
            subprocess.run(['/usr/bin/borg','check','--verify-data',str(copied)],env=restore_env,check=True,capture_output=True)
            recovered=subprocess.run(['/usr/bin/borg','extract','--stdout',str(copied)+'::fixture','fixture.txt'],env=restore_env,capture_output=True)
            self.assertEqual(recovered.returncode,0,recovered.stderr.decode())
            self.assertEqual(recovered.stdout,b'offsite restoration fixture\n')

    def test_failed_repository_check_does_not_publish_manifest(self):
        cfg=json.loads((PI/'configs'/'icloud-backup.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); name='vanpi-20260920T010203Z-12345678'
            with mock.patch.object(backup,'run',side_effect=[None,RuntimeError('check failed')]):
                with self.assertRaises(RuntimeError):backup.make_snapshot(cfg,root,root/'source',name)
            self.assertFalse((root/name/'payload'/'manifest.json').exists())

    def test_route_becoming_starlink_stops_inflight_client(self):
        cfg=json.loads((PI/'configs'/'icloud-backup.json').read_text())
        class Client:
            pid=987654
            returncode=None
            def poll(self): return self.returncode
            def wait(self, timeout=None): self.returncode=-15; return -15
        child=Client()
        with mock.patch.object(backup,'guard',side_effect=[('192.168.6.103','wan'),backup.Deferred('Starlink')]), \
             mock.patch.object(backup,'check_parked'), mock.patch.object(backup.subprocess,'Popen',return_value=child), \
             mock.patch.object(backup.time,'sleep'), mock.patch.object(backup.time,'monotonic',side_effect=[0,10]), \
             mock.patch.object(backup.os,'kill') as kill, mock.patch.object(backup.os,'killpg') as killpg:
            with self.assertRaises(backup.Deferred): backup.run([backup.RCLONE,'copy','source','dest'],cfg,network=True)
            self.assertIn(mock.call(child.pid,signal.SIGSTOP),kill.call_args_list)
            self.assertIn(mock.call(child.pid,signal.SIGTERM),killpg.call_args_list)
            self.assertIsNone(backup.CHILD)

    def test_snapshot_copy_is_locked_and_checked_before_manifest(self):
        cfg=json.loads((PI/'configs'/'icloud-backup.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); repo=root/'source'; repo.mkdir()
            stage=root/'stage'; stage.mkdir()
            name='vanpi-20260920T010203Z-12345678'
            def child(args,cfg):
                dest=stage/name/'payload'/'repository'
                if args[1]=='with-lock':
                    self.assertIn('--exclude=/lock.exclusive',args)
                    self.assertIn('--exclude=/lock.roster',args)
                    (dest/'config').write_text('encrypted-key')
                    (dest/'data').mkdir(); (dest/'data'/'0').write_bytes(b'encrypted')
                elif args[1]=='check':
                    self.assertFalse((dest.parent/'manifest.json').exists())
            with mock.patch.object(backup,'run',side_effect=child) as run, mock.patch.object(backup,'capture',return_value='borg 1.2.4'):
                backup.make_snapshot(cfg,stage,repo,name)
                self.assertEqual([c.args[0][1] for c in run.call_args_list],['with-lock','check'])
                self.assertTrue((stage/name/'payload'/'manifest.json').is_file())

    def test_manifest_hashes_and_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'config').write_text('abc')
            self.assertEqual(backup.file_manifest(root)['config']['sha256'], 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
            (root/'link').symlink_to(root/'config')
            with self.assertRaises(RuntimeError): backup.file_manifest(root)

    def test_rejects_path_traversal_and_generation_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('../other', '/home/pi', 'vanpi-20260920', 'arbitrary'):
                with self.assertRaises(ValueError): backup.safe_generation(root, name)
            name='vanpi-20260920T010203Z-12345678'
            (root/name).symlink_to(root)
            with self.assertRaises(ValueError): backup.safe_generation(root, name)

    def test_retention_ignores_unrelated_future_and_malformed_paths(self):
        names=[f'vanpi-202609{day:02d}T010203Z-12345678' for day in range(1,13)]
        names += ['other-person', '../oops', 'vanpi-20269999T010203Z-12345678']
        self.assertEqual(backup.choose_prunable(names, names[9], 8), names[:2])

    def test_no_disk_mount_or_cloud_call_on_starlink(self):
        cfg=json.loads((PI/'configs'/'icloud-backup.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'authenticated.json').write_text('{}')
            with mock.patch.object(backup,'STATE_DIR',root), mock.patch.object(backup,'credentials_ready',return_value=True), \
                 mock.patch.object(backup,'check_parked'), mock.patch.object(backup,'guard',side_effect=backup.Deferred('Starlink')), \
                 mock.patch.object(backup,'run') as run:
                with self.assertRaises(backup.Deferred): backup.weekly(cfg,{})
                run.assert_not_called()

    def test_not_due_never_probes_network(self):
        cfg=json.loads((PI/'configs'/'icloud-backup.json').read_text())
        with mock.patch.object(backup,'guard') as guard:
            self.assertEqual(backup.weekly(cfg,{'last_success_at':time.time()}), 'not due')
            guard.assert_not_called()

    def test_configuration_rejects_remote_escape(self):
        cfg=json.loads((PI/'configs'/'icloud-backup.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'config.json'
            for remote in ('icloud:', 'icloud:VanRecovery/../other', 'other:VanRecovery/vanpi/weekly'):
                cfg['remote']=remote; p.write_text(json.dumps(cfg))
                with self.assertRaises(ValueError): backup.load_config(p)


if __name__ == '__main__': unittest.main()
