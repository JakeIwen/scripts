import json
import datetime as dt
import hashlib
import io
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
from icloud_progress import ProgressWatch, RcloneStats, StreamDigest


def test_config():
    cfg = json.loads((PI/'configs'/'icloud-backup.json').read_text())
    # Scheduling is tested separately; functional tests must work at any hour.
    cfg['local_backup_window_start'] = cfg['local_backup_window_end'] = '00:00'
    return cfg


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
        e['router']['default_policy'] = 'clientwan_only'
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
    def test_auth_errors_are_actionable_without_echoing_credentials(self):
        for text in ('trust token expired, please reauth', 'missing icloud trust token',
                     'Invalid Session Token', 'Missing PCS cookies from the request',
                     'authSRPComplete: sign in failed: 401'):
            failure = backup.command_failure('/usr/local/bin/rclone','copyto',1,
                text + ' password=PRIVATE cookie=SECRET https://private.invalid/?token=TOKEN')
            self.assertIsInstance(failure,backup.AuthenticationRequired)
            self.assertIn('--login',str(failure))
            for secret in ('PRIVATE','SECRET','private.invalid','TOKEN'):
                self.assertNotIn(secret,str(failure))

    def test_unknown_errors_do_not_echo_server_bodies(self):
        failure=backup.command_failure('/usr/local/bin/rclone','copyto',1,'private account and token')
        self.assertEqual(str(failure),'rclone copyto failed (exit 1)')

    def test_cloud_canary_failure_never_marks_authenticated_or_starts_job(self):
        cfg=test_config()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with mock.patch.object(backup,'STATE_DIR',root), mock.patch.object(backup,'credentials_ready',return_value=True), \
                 mock.patch.object(backup,'run',side_effect=backup.AuthenticationRequired('fresh 2FA required')), \
                 mock.patch.object(backup.subprocess,'run') as service:
                with self.assertRaises(backup.AuthenticationRequired):backup.verify_login(cfg)
                service.assert_not_called()
                self.assertFalse((root/'authenticated.json').exists())
                self.assertEqual(list(root.glob('canary-*.txt')),[])

    def test_cloud_canary_success_clears_auth_error_then_starts_job(self):
        cfg=test_config()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'authentication-error.json').write_text('{}')
            def run(args,*a,**kw):
                if args[1]=='cat':return next(root.glob('canary-*.txt')).read_text()
                return ''
            with mock.patch.object(backup,'STATE_DIR',root), mock.patch.object(backup,'credentials_ready',return_value=True), \
                 mock.patch.object(backup,'run',side_effect=run) as operations, \
                 mock.patch.object(backup.subprocess,'run') as service:
                backup.verify_login(cfg)
                self.assertEqual([c.args[0][1] for c in operations.call_args_list],['copyto','cat','deletefile'])
                self.assertTrue((root/'authenticated.json').is_file())
                self.assertFalse((root/'authentication-error.json').exists())
                service.assert_called_once()

    @unittest.skipUnless(Path('/usr/bin/borg').is_file(), 'Borg integration runs on the Pi')
    def test_real_encrypted_repository_roundtrip(self):
        cfg=test_config()
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
        cfg=test_config()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); name='vanpi-20260920T010203Z-12345678'
            with mock.patch.object(backup,'run',side_effect=[None,RuntimeError('check failed')]):
                with self.assertRaises(RuntimeError):backup.make_snapshot(cfg,root,root/'source',name)
            self.assertFalse((root/name/'payload'/'manifest.json').exists())

    def test_route_becoming_starlink_stops_inflight_client(self):
        cfg=test_config()
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
        cfg=test_config()
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
            with mock.patch.object(backup,'run',side_effect=child) as run, mock.patch.object(backup,'capture',return_value='borg 1.2.4'), mock.patch.object(backup,'check_work_allowed'):
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
        cfg=test_config()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'authenticated.json').write_text('{}')
            with mock.patch.object(backup,'STATE_DIR',root), mock.patch.object(backup,'credentials_ready',return_value=True), \
                 mock.patch.object(backup,'check_parked'), mock.patch.object(backup,'guard',side_effect=backup.Deferred('Starlink')), \
                 mock.patch.object(backup,'run') as run:
                with self.assertRaises(backup.Deferred): backup.weekly(cfg,{})
                run.assert_not_called()

    def test_not_due_never_probes_network(self):
        cfg=test_config()
        with mock.patch.object(backup,'guard') as guard:
            self.assertEqual(backup.weekly(cfg,{'last_success_at':time.time()}), 'not due')
            guard.assert_not_called()

    def test_configuration_rejects_remote_escape(self):
        cfg=test_config()
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'config.json'
            for remote in ('icloud:', 'icloud:VanRecovery/../other', 'other:VanRecovery/vanpi/weekly'):
                cfg['remote']=remote; p.write_text(json.dumps(cfg))
                with self.assertRaises(ValueError): backup.load_config(p)


class ProgressTests(unittest.TestCase):
    def test_final_auth_error_survives_a_large_progress_log(self):
        cfg=test_config()
        class Client:
            returncode=1
            def poll(self):return 1
        def spawn(*args,**kwargs):
            kwargs['stderr'].write('x'*70000+'\ntrust token expired, please reauth\n')
            kwargs['stderr'].flush()
            return Client()
        with mock.patch.object(backup,'guard',return_value=('127.0.0.1','test')),mock.patch.object(backup,'check_work_allowed'),mock.patch.object(backup.subprocess,'Popen',side_effect=spawn):
            with self.assertRaises(backup.AuthenticationRequired):backup.run([backup.RCLONE,'copy','a','b'],cfg,network=True)

    @unittest.skipUnless(Path('/usr/local/bin/rclone').is_file(), 'rclone integration runs on Pi')
    def test_real_rclone_numeric_progress_is_observed(self):
        cfg=test_config();cfg.update(rclone_config='/dev/null',bandwidth_limit='128k',guard_interval_seconds=1)
        samples=[]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();dest=root/'dest'
            value=b'\x01'*(1024*1024);(source/'fixture').write_bytes(value)
            with mock.patch.object(backup,'guard',return_value=('127.0.0.1','local-test')), mock.patch.object(backup,'check_work_allowed'):
                backup.run([backup.RCLONE,'copy',str(source),str(dest),'--disable','Copy'],cfg,network=True,progress=samples.append)
            self.assertEqual((dest/'fixture').read_bytes(),value)
            self.assertTrue(any(s.get('bytes',0)>0 for s in samples),samples)

    def test_progress_extends_deadline_but_unchanged_counters_do_not(self):
        now=[0]
        watch=ProgressWatch(15,clock=lambda:now[0])
        for seconds in (10,20,30,40,50):
            now[0]=seconds
            watch.observe({'bytes':seconds})
            self.assertFalse(watch.expired())
        now[0]=64;watch.observe({'bytes':50})
        self.assertFalse(watch.expired())
        now[0]=65
        self.assertTrue(watch.expired())

    def test_stats_are_numeric_only_and_pread_preserves_writer_offset(self):
        with tempfile.TemporaryFile() as f:
            parser=RcloneStats()
            f.write(b'{"stats":{"bytes":12,"checks":1,"errors":99,"elapsedTime":100},"msg":"private"}\n')
            f.flush();pos=f.tell()
            self.assertEqual(parser.read(f.fileno()),{'bytes':12,'checks':1})
            self.assertEqual(f.tell(),pos)
            f.write(b'{"stats":{"bytes":24');f.flush()
            self.assertEqual(parser.read(f.fileno())['bytes'],12)
            f.write(b'}}\n');f.flush()
            self.assertEqual(parser.read(f.fileno())['bytes'],24)

    def test_stream_hash_preserves_binary_contents(self):
        value=bytes(range(256))*1024
        reader=StreamDigest(io.BytesIO(value))
        self.assertEqual(reader.result(),{'bytes':len(value),'sha256':hashlib.sha256(value).hexdigest()})

    def test_running_stalled_child_is_terminated(self):
        cfg=test_config();cfg['no_progress_timeout_seconds']=0.2
        with mock.patch.object(backup,'check_work_allowed'):
            with self.assertRaisesRegex(RuntimeError,'made no progress'):
                backup.run([sys.executable,'-c','import time; time.sleep(20)'],cfg)
        self.assertIsNone(backup.CHILD)

    def test_run_streams_binary_to_hash_without_buffering_whole_file(self):
        cfg=test_config();value=bytes(range(256))*1024
        with mock.patch.object(backup,'check_work_allowed'):
            result=backup.run([sys.executable,'-c','import sys; sys.stdout.buffer.write(bytes(range(256))*1024)'],cfg,stream_hash=True)
        self.assertEqual(result,{'bytes':len(value),'sha256':hashlib.sha256(value).hexdigest()})

    def test_local_window_boundaries_and_wraparound(self):
        cfg=test_config();cfg.update(local_backup_window_start='02:55',local_backup_window_end='09:00')
        for hour,minute,expected in [(2,54,False),(2,55,True),(3,0,True),(8,59,True),(9,0,False),(23,59,False)]:
            self.assertEqual(backup.local_backup_window(cfg,dt.datetime(2026,9,21,hour,minute)),expected)
        cfg.update(local_backup_window_start='23:00',local_backup_window_end='01:00')
        self.assertTrue(backup.local_backup_window(cfg,dt.datetime(2026,9,21,0,30)))
        self.assertFalse(backup.local_backup_window(cfg,dt.datetime(2026,9,21,1,0)))
        cfg['local_backup_window_end']='23:00'
        self.assertFalse(backup.local_backup_window(cfg,dt.datetime(2026,9,21,23,30)))

    def test_offsite_yields_before_accessing_disks_in_local_window(self):
        cfg=test_config()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'authenticated.json').write_text('{}')
            with mock.patch.object(backup,'STATE_DIR',root), mock.patch.object(backup,'credentials_ready',return_value=True), \
                 mock.patch.object(backup,'check_parked'), mock.patch.object(backup,'local_backup_window',return_value=True), mock.patch.object(backup,'local_backups_complete',return_value=False), \
                 mock.patch.object(backup,'guard') as guard, mock.patch.object(backup,'run') as run:
                with self.assertRaisesRegex(backup.Deferred,'local-backup window'):backup.weekly(cfg,{})
                guard.assert_not_called();run.assert_not_called()

    def test_working_child_is_stopped_when_local_window_begins(self):
        cfg=test_config()
        class Client:
            pid=987654
            returncode=None
            def poll(self):return self.returncode
            def wait(self,timeout=None):self.returncode=-15;return -15
        child=Client()
        with mock.patch.object(backup,'check_parked'), mock.patch.object(backup,'local_backup_window',side_effect=[False,True]), mock.patch.object(backup,'local_backups_complete',return_value=False), \
             mock.patch.object(backup.subprocess,'Popen',return_value=child), \
             mock.patch.object(backup.time,'sleep'), mock.patch.object(backup.time,'monotonic',side_effect=[0,10]), \
             mock.patch.object(backup.os,'killpg') as killpg:
            with self.assertRaisesRegex(backup.Deferred,'local-backup window'):backup.run(['/usr/bin/borg','check','fixture'],cfg)
            self.assertIn(mock.call(child.pid,signal.SIGTERM),killpg.call_args_list)

    def test_local_priority_requires_both_today_stamps_and_resumes_early(self):
        now=dt.datetime(2026,9,21,5,30)
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'borg';b=Path(tmp)/'exfat';a.touch();b.touch()
            today=dt.datetime(2026,9,21,5,10).timestamp()
            yesterday=dt.datetime(2026,9,20,5,10).timestamp()
            env={'VANPI_ICLOUD_BORG_STAMP':str(a),'VANPI_ICLOUD_EXFAT_STAMP':str(b)}
            with mock.patch.dict(os.environ,env):
                os.utime(a,(today,today));os.utime(b,(yesterday,yesterday))
                self.assertFalse(backup.local_backups_complete(now))
                os.utime(b,(today,today))
                self.assertTrue(backup.local_backups_complete(now))
                os.utime(b,(now.timestamp()+60,now.timestamp()+60))
                self.assertFalse(backup.local_backups_complete(now))
        with mock.patch.object(backup,'check_parked'),mock.patch.object(backup,'local_backup_window',return_value=True),mock.patch.object(backup,'local_backups_complete',return_value=True):
            backup.check_work_allowed(test_config())


class CheckpointTests(unittest.TestCase):
    def setup_generation(self,root):
        generation=root/'vanpi-20260921T010203Z-12345678'
        repo=generation/'payload'/'repository';repo.mkdir(parents=True)
        (repo/'a').write_bytes(b'a');(repo/'b').write_bytes(b'bb')
        (repo.parent/'RESTORE.txt').write_text('restore')
        backup.atomic_json(repo.parent/'manifest.json',{'owner':backup.OWNER,'generation':generation.name,'files':backup.file_manifest(repo)})
        self.payload=repo.parent
        self.data={str(p.relative_to(self.payload)):p.read_bytes() for p in self.payload.rglob('*') if p.is_file()}
        self.ids={path:'id:'+path for path in self.data}
        self.calls=[];self.fail=None;self.bad=None;self.change_on_final=False;self.inventories=0
        return generation

    def remote(self,args,cfg,**kwargs):
        if args[1]=='lsjson':
            self.inventories+=1
            if self.change_on_final and self.inventories==2:self.ids['repository/a']='changed-during-check'
            return json.dumps([{'Path':path,'ID':self.ids[path],'ModTime':'2026-09-21T00:00:00Z','Size':len(data),'IsDir':False} for path,data in self.data.items()])
        self.assertEqual(args[1],'cat');self.assertTrue(kwargs['stream_hash'])
        path=args[2].split('/generation/',1)[1];self.calls.append(path)
        if path==self.fail:raise backup.Deferred('interrupted')
        value=self.data[path]
        return {'bytes':len(value),'sha256':'0'*64 if path==self.bad else hashlib.sha256(value).hexdigest()}

    def verify(self,cfg,generation):
        with mock.patch.object(backup,'STATE_DIR',generation.parent), mock.patch.object(backup,'check_work_allowed'), mock.patch.object(backup,'run',side_effect=self.remote):
            return backup.verify_generation(cfg,generation,'icloud:test/generation',{})

    def test_interruption_keeps_successful_file_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            generation=self.setup_generation(Path(tmp));cfg=test_config();self.fail='repository/b'
            with self.assertRaises(backup.Deferred):self.verify(cfg,generation)
            checked=backup.read_json(generation/'verification.json')['verified']
            self.assertIn('repository/a',checked);self.assertNotIn('repository/b',checked)
            self.fail=None;self.calls=[]
            result=self.verify(cfg,generation)
            self.assertNotIn('repository/a',self.calls)
            self.assertEqual(result['verified_files'],len(self.data))
            self.calls=[];self.verify(cfg,generation)
            self.assertEqual(self.calls,[])

    def test_bad_hash_is_never_checkpointed(self):
        with tempfile.TemporaryDirectory() as tmp:
            generation=self.setup_generation(Path(tmp));self.bad='repository/a'
            with self.assertRaisesRegex(RuntimeError,'SHA-256'):self.verify(test_config(),generation)
            self.assertNotIn('repository/a',backup.read_json(generation/'verification.json')['verified'])
            self.assertFalse((generation/'_COMPLETE.json').exists())

    def test_changed_remote_identity_invalidates_only_that_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            generation=self.setup_generation(Path(tmp));cfg=test_config();self.verify(cfg,generation)
            self.ids['repository/a']='replacement-id';self.calls=[]
            self.verify(cfg,generation)
            self.assertEqual(self.calls,['repository/a'])

    def test_manifest_change_invalidates_previous_generation_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            generation=self.setup_generation(Path(tmp));cfg=test_config();self.verify(cfg,generation)
            p=self.payload/'manifest.json';m=backup.read_json(p);m['changed']='new manifest';backup.atomic_json(p,m)
            self.data['manifest.json']=p.read_bytes();self.ids['manifest.json']='new-manifest';self.calls=[]
            self.verify(cfg,generation)
            self.assertEqual(set(self.calls),set(self.data))

    def test_metadata_change_during_verification_prevents_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            generation=self.setup_generation(Path(tmp));self.change_on_final=True
            with self.assertRaisesRegex(backup.Deferred,'changed during verification'):self.verify(test_config(),generation)
            self.assertNotIn('repository/a',backup.read_json(generation/'verification.json')['verified'])
            self.assertFalse((generation/'_COMPLETE.json').exists())

    def test_corrupt_checkpoint_is_rebuilt_and_traversal_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            generation=self.setup_generation(Path(tmp));(generation/'verification.json').write_text('broken json')
            self.verify(test_config(),generation)
            self.assertEqual(set(self.calls),set(self.data))
            p=self.payload/'manifest.json';m=backup.read_json(p);m['files']['../outside']=m['files']['repository/a'] if 'repository/a' in m['files'] else m['files']['a'];backup.atomic_json(p,m)
            with self.assertRaisesRegex(ValueError,'unsafe path'):backup.verification_plan(self.payload,generation.name)


if __name__ == '__main__': unittest.main()
