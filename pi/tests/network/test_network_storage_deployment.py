"""Storage migration safety tests using isolated files and mocked services/mounts."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

PI = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PI))
import deploy_network_storage as deploy


def create_database(path):
    connection=sqlite3.connect(path)
    connection.executescript('CREATE TABLE events(id INTEGER PRIMARY KEY, message TEXT);'
                             'CREATE TABLE incidents(id INTEGER PRIMARY KEY);'
                             'CREATE TABLE checkpoints(key TEXT PRIMARY KEY,value TEXT);'
                             "INSERT INTO events VALUES(1,'before'),(2,'during');"
                             'INSERT INTO incidents VALUES(1);'
                             "INSERT INTO checkpoints VALUES('offset','200');")
    connection.close()


class StorageDeploymentTests(unittest.TestCase):
    def test_real_recorder_scope_excludes_ui_receiver_and_backup(self):
        expected = {'pi/scripts/network_flight_recorder.py', 'pi/tmpfiles.d/vanpi-network.conf'}
        expected.update('pi/scripts/network_recorder/' + name + '.py'
                        for name in ('__init__', 'collector', 'parsers', 'report', 'store', 'storage'))
        self.assertEqual(set(deploy.RECORDER_TARGETS), expected)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for source in expected:
                path=root/source;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('# fixture\n')
            with mock.patch.object(deploy,'REPO',root),\
                 mock.patch.object(deploy.subprocess,'run',return_value=subprocess.CompletedProcess([],1,b'',b'')),\
                 mock.patch.object(Path, 'rglob', side_effect=AssertionError('frontend must not be scanned')):
                plan = deploy.make_plan('fixture', recorder_only=True)
        self.assertEqual(plan['frontend_files'], [])
        self.assertIsNone(plan['config'])
        deploy.validate_plan(plan)

    def test_known_hash_lineage_combines_full_and_partial_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, rows in (
                ('network-storage-a', [{'destination':'recorder','sha256':'old'},
                                      {'destination':'dashboard','sha256':'untouched'}]),
                ('network-storage-b', [{'destination':'recorder','sha256':'fixed'}]),
            ):
                root=Path(directory)/name;root.mkdir()
                (root/'manifest.json').write_text(json.dumps(dict(files=rows)))
                (root/'complete').touch()
            with mock.patch.object(deploy, 'BACKUPS', Path(directory)):
                self.assertEqual(deploy.known_hashes(), {'recorder':'fixed','dashboard':'untouched'})
                (Path(directory)/'network-storage-b/rolled-back').touch()
                self.assertEqual(deploy.known_hashes(), {'recorder':'old','dashboard':'untouched'})

    def test_readiness_parses_only_ok_and_never_returns_report_evidence(self):
        raw=json.dumps(dict(ok=True, events=[dict(message='synthetic-secret-bearing-evidence')]))
        result=subprocess.CompletedProcess([],0,raw,'synthetic stderr')
        with mock.patch.object(deploy.subprocess,'run',return_value=result) as run:
            self.assertIs(deploy.recorder_report_ready(7), True)
        arguments=run.call_args.args[0]
        self.assertIn('report',arguments)
        self.assertNotIn('storage-sync',arguments)
        self.assertEqual(run.call_args.kwargs['timeout'],7)

    def test_borg_patch_preserves_unrelated_bytes_and_is_idempotent(self):
        original="private_command='unchanged sensitive fixture'\nBORG_EXCLUDES=(\n  '/original'\n)\nOTHER=untouched\n"
        result=deploy.patch_exclusions(original)
        expected=original.replace("  '/original'\n","  '/original'\n  '/var/lib/vanpi-network'\n  '/var/log/openwrt'\n")
        self.assertEqual(result,expected)
        self.assertEqual(deploy.patch_exclusions(result),result)
        with self.assertRaises(ValueError):
            deploy.patch_exclusions('BORG_EXCLUDES=dynamic_array\n')

    def test_unmounted_fallback_rejected_before_flash_directory_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            mount=Path(directory).resolve()
            config=dict(mountpoint=str(mount),uuid='fixture',fstype='exfat',flash_root=str(mount/'vanpi-network'))
            rows=dict(filesystems=[dict(target='/',fstype='ext4',uuid='fixture',label='EXFAT512',options='rw',**{'maj:min':'0:1'})])
            with mock.patch.object(deploy.base,'command',return_value=subprocess.CompletedProcess([],0,json.dumps(rows),'')):
                with self.assertRaisesRegex(ValueError,'identity'):
                    deploy.FlashRoot(config,create=True)
            self.assertFalse((mount/'vanpi-network').exists())

    def test_database_backup_keeps_evidence_and_checkpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.sqlite3';target=Path(directory)/'target.sqlite3'
            create_database(source);target.touch()
            counts=deploy.database_snapshot(source,target)
            self.assertEqual(counts,dict(events=2,incidents=1,checkpoints=1))
            connection=sqlite3.connect(target)
            try:
                self.assertEqual(connection.execute('SELECT value FROM checkpoints').fetchone()[0],'200')
                self.assertEqual(connection.execute('SELECT message FROM events ORDER BY id').fetchall(),[('before',),('during',)])
            finally:
                connection.close()

    def test_flash_copy_refuses_overwrite_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.write_bytes(b'same repeated line\nsame repeated line\n')
            descriptor=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
            try:
                checksum=deploy.copy_bytes(source,descriptor,'copy')
                self.assertEqual(checksum,deploy.base.digest(source))
                with self.assertRaises(FileExistsError):
                    deploy.copy_bytes(source,descriptor,'copy')
                (root/'linked').symlink_to(source)
                with self.assertRaises(ValueError):
                    deploy.copy_bytes(root/'linked',descriptor,'second')
                self.assertEqual((root/'copy').read_bytes(),source.read_bytes())
            finally:
                os.close(descriptor)

    def test_flash_root_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            mount=Path(directory).resolve();other=mount/'other';other.mkdir()
            (mount/'vanpi-network').symlink_to(other)
            config=dict(mountpoint=str(mount),flash_root=str(mount/'vanpi-network'))
            with mock.patch.object(deploy,'mount_identity',return_value=dict(device=mount.stat().st_dev)):
                with self.assertRaises(OSError):
                    deploy.FlashRoot(config,create=True)
            self.assertEqual(list(other.iterdir()),[])

    @unittest.skipUnless(Path('/proc/self/fd').is_dir(),'directory-fd SQLite path is Linux-specific')
    def test_migration_preserves_exact_log_generations_and_sqlite_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            mount=Path(directory).resolve();old_db=mount/'old.sqlite3';create_database(old_db)
            logs=mount/'oldlogs';logs.mkdir()
            fixture={'dendelion.log':b'first\nrepeat\nrepeat\n','network.jsonl.1':b'{"first":1}\n'}
            for name,data in fixture.items():(logs/name).write_bytes(data)
            config=dict(mountpoint=str(mount),flash_root=str(mount/'vanpi-network'))
            with mock.patch.object(deploy,'mount_identity',return_value=dict(device=mount.stat().st_dev)),\
                 mock.patch.object(deploy,'OLD_DB',old_db),mock.patch.object(deploy,'OLD_LOGS',logs):
                flash=deploy.FlashRoot(config,create=True)
                try:
                    result=deploy.migrate_data(dict(release='network-storage-fixture'),flash)
                finally:
                    flash.close()
            self.assertEqual(result['counts'],dict(events=2,incidents=1,checkpoints=1))
            for name,data in fixture.items():
                self.assertEqual((mount/'vanpi-network/openwrt'/name).read_bytes(),data)
                self.assertEqual((logs/name).read_bytes(),data)

    def test_rollback_refuses_missing_flash_before_stopping_services(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'network-storage-fixture';root.mkdir()
            plan=dict(schema_version=1,release=root.name,files=[],config={},installed_tree_sha256='tree')
            (root/'manifest.json').write_text(json.dumps(plan))
            with mock.patch.object(deploy,'BACKUPS',Path(directory)),mock.patch.object(deploy,'validate_plan'),\
                 mock.patch.object(deploy.base,'regular_info',return_value={'sha256':'x'}),\
                 mock.patch.object(deploy.base,'link_info',return_value='releases/'+root.name),\
                 mock.patch.object(deploy.base,'tree_digest',return_value='tree'),\
                 mock.patch.object(deploy,'FlashRoot',side_effect=ValueError('flash absent')),\
                 mock.patch.object(deploy.base,'systemctl') as service:
                plan['backup_conf_sha256']='x';(root/'manifest.json').write_text(json.dumps(plan))
                with self.assertRaisesRegex(ValueError,'flash absent'):
                    deploy.rollback(root.name)
                service.assert_not_called()


class RecorderOnlyUpdateTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory(prefix='recorder-only-test-',dir='/private/tmp' if Path('/private/tmp').is_dir() else '/tmp')
        self.addCleanup(directory.cleanup)
        self.root=Path(directory.name).resolve()
        self.runtime=self.root/'runtime';self.runtime.mkdir()
        self.flash=self.root/'flash';self.flash.mkdir()
        self.config=dict(runtime_root=str(self.runtime),flash_root=str(self.flash))
        self.conf=self.root/'storage.json';self.conf.write_text(json.dumps(self.config))
        self.core=self.root/'recorder.py';self.core.write_text('old=True\n')
        self.tmpfiles=self.root/'tmpfiles.conf';self.tmpfiles.write_text('old tmpfiles mode')
        self.database=self.flash/'events.sqlite3';self.database.write_bytes(b'initial evidence')
        self.raw=self.runtime/'raw.log';self.raw.write_bytes(b'raw evidence')
        self.permissions=self.runtime/'permissions';self.permissions.write_text('broken')
        self.dashboard=self.root/'dashboard.py';self.dashboard.write_bytes(b'unrelated evolving UI')
        self.backup=self.root/'backup.conf';self.backup.write_bytes(b'unrelated backup config')
        targets={'pi/scripts/network_flight_recorder.py':str(self.core),
                 'pi/tmpfiles.d/vanpi-network.conf':str(self.tmpfiles)}
        self.contents={'pi/scripts/network_flight_recorder.py':b'fixed=True\n',
                       'pi/tmpfiles.d/vanpi-network.conf':b'corrected tmpfiles mode'}
        self.state=dict(active=True,enabled='enabled')
        self.calls=[]
        def command(args,check=True):
            self.calls.append(args)
            if args[0]=='/usr/bin/systemctl':
                self.assertEqual(args[-1],'network-flight-recorder')
                self.assertIn(args[1],('stop','restart'))
                self.state['active']=args[1]=='restart'
            if args[0]=='/usr/bin/systemd-tmpfiles':
                self.assertEqual(args, ['/usr/bin/systemd-tmpfiles','--create',str(self.tmpfiles)])
                self.permissions.write_text('setgid directory applied')
            return subprocess.CompletedProcess(args,0,'','')
        def repair(config):
            self.assertEqual(config,self.config)
            self.assertEqual(self.permissions.read_text(),'setgid directory applied')
            self.permissions.write_text('corrected live ownership and setgid')
            return dict(checked=8,repaired=8,missing=0)
        def state(name):
            self.assertEqual(name,'network-flight-recorder')
            return copy.deepcopy(self.state)
        patches=[mock.patch.object(deploy,'RECORDER_TARGETS',targets),
                 mock.patch.object(deploy,'CONFIG',self.conf),
                 mock.patch.object(deploy,'BACKUPS',self.root/'backups'),
                 mock.patch.object(deploy,'BACKUP_CONF',self.backup),
                 mock.patch.object(deploy.base,'command',side_effect=command),
                 mock.patch.object(deploy.base,'service_state',side_effect=state),
                 mock.patch.object(deploy.base,'tree_digest',side_effect=AssertionError('frontend must be untouched')),
                 mock.patch.object(deploy,'repair_spool_permissions',side_effect=repair),
                 mock.patch.object(deploy,'mount_identity',return_value=dict(device=123)),
                 mock.patch.object(deploy.os,'access',return_value=True),
                 mock.patch('pwd.getpwnam',return_value=types.SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())),
                 mock.patch.object(deploy.base.os,'chown'),
                 mock.patch.object(deploy,'wait_recorder_ready',return_value=dict(mode='flash',report_ok=True))]
        for patch in patches:patch.start();self.addCleanup(patch.stop)
        self.plan=dict(schema_version=1,release='network-storage-recorder-only',recorder_only=True,
                       config=None,frontend_files=[],files=[])
        for source,destination in targets.items():
            self.plan['files'].append(dict(source=source,destination=destination,
                                           sha256=deploy.base.sha(self.contents[source]),
                                           head_sha256=deploy.base.digest(destination)))
        deploy.inspect(self.plan)
        directory=tempfile.TemporaryDirectory(prefix='network-storage-stage.',dir='/tmp')
        self.addCleanup(directory.cleanup);self.stage=Path(directory.name)
        for index,row in enumerate(self.plan['files']):(self.stage/str(index)).write_bytes(self.contents[row['source']])

    def test_apply_and_code_rollback_preserve_new_evidence_and_repaired_permissions(self):
        config_before=self.conf.read_bytes()
        result=deploy.apply(self.plan,self.stage)
        self.assertTrue(result['recorder_only'])
        self.assertEqual(self.core.read_text(),'fixed=True\n')
        self.database.write_bytes(b'new evidence written during replay')
        calls_before=len(self.calls)
        deploy.rollback(self.plan['release'])
        self.assertEqual(self.core.read_text(),'old=True\n')
        self.assertEqual(self.tmpfiles.read_text(),'old tmpfiles mode')
        self.assertEqual(self.database.read_bytes(),b'new evidence written during replay')
        self.assertEqual(self.raw.read_bytes(),b'raw evidence')
        self.assertEqual(self.permissions.read_text(),'corrected live ownership and setgid')
        self.assertEqual(self.dashboard.read_bytes(),b'unrelated evolving UI')
        self.assertEqual(self.backup.read_bytes(),b'unrelated backup config')
        self.assertEqual(self.conf.read_bytes(),config_before)
        self.assertTrue(self.state['active'])
        self.assertFalse(any(call[0]=='/usr/bin/systemd-tmpfiles' for call in self.calls[calls_before:]))
        self.assertEqual([call for call in self.calls if call[0]=='/usr/bin/systemctl'],
                         [['/usr/bin/systemctl','stop','network-flight-recorder'],
                          ['/usr/bin/systemctl','restart','network-flight-recorder'],
                          ['/usr/bin/systemctl','stop','network-flight-recorder'],
                          ['/usr/bin/systemctl','restart','network-flight-recorder']])

    def test_existing_config_is_required_and_changed_config_blocks_apply(self):
        self.conf.write_text('{"changed":true}')
        with self.assertRaisesRegex(ValueError,'configuration changed'):
            deploy.apply(self.plan,self.stage)
        self.conf.unlink()
        with self.assertRaisesRegex(ValueError,'existing storage configuration'):
            deploy.inspect(self.plan)
        self.assertFalse(any(call[0]=='/usr/bin/systemctl' for call in self.calls))

    def test_mode_rejects_migration_and_out_of_scope_targets(self):
        bad=copy.deepcopy(self.plan);bad['migration']=True
        with self.assertRaisesRegex(ValueError,'cannot deploy frontend or migrate'):
            deploy.validate_plan(bad)
        bad=copy.deepcopy(self.plan);bad['files'][0]['destination']=str(self.dashboard)
        with self.assertRaisesRegex(ValueError,'unexpected managed'):
            deploy.validate_plan(bad)

    def test_readiness_failure_restores_only_code_and_preserves_progress(self):
        def failed(*args):
            self.database.write_bytes(b'new evidence before readiness timeout')
            raise RuntimeError('fixture delayed recovery')
        with mock.patch.object(deploy,'wait_recorder_ready',side_effect=failed):
            with self.assertRaisesRegex(RuntimeError,'delayed recovery'):
                deploy.apply(self.plan,self.stage)
        self.assertEqual(self.core.read_text(),'old=True\n')
        self.assertEqual(self.database.read_bytes(),b'new evidence before readiness timeout')
        self.assertEqual(self.permissions.read_text(),'corrected live ownership and setgid')
        self.assertEqual(self.dashboard.read_bytes(),b'unrelated evolving UI')
        self.assertTrue(self.state['active'])


class RecorderReadinessTests(unittest.TestCase):
    def test_replay_longer_than_45_seconds_waits_and_reports_bounded_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();runtime=root/'runtime';runtime.mkdir()
            config=dict(runtime_root=str(runtime),flash_root=str(root/'flash'))
            plan=dict(config=config,mount={'device':123})
            database=runtime/'events.sqlite3'
            connection=sqlite3.connect(database)
            connection.executescript('CREATE TABLE events(id INTEGER PRIMARY KEY); CREATE TABLE checkpoints(key TEXT PRIMARY KEY,value TEXT);')
            connection.executemany('INSERT INTO events VALUES(?)',[(i,) for i in range(1,3243)])
            connection.execute("INSERT INTO checkpoints VALUES('flash-drained-through','0')");connection.commit();connection.close()
            clock=[0.0]
            def write_status():
                mode='flash' if clock[0]>=220 else 'ram'
                (runtime/'storage-status.json').write_text(json.dumps(dict(mode=mode,checked_at=100+clock[0],
                    active_database=str(root/'flash/events.sqlite3') if mode=='flash' else str(database))))
                if mode=='flash':
                    connection=sqlite3.connect(database)
                    connection.execute("UPDATE checkpoints SET value='3242'");connection.commit();connection.close()
            def sleep(seconds):clock[0]+=seconds;write_status()
            write_status()
            with mock.patch.object(deploy.time,'monotonic',side_effect=lambda:clock[0]),\
                 mock.patch.object(deploy.time,'sleep',side_effect=sleep),\
                 mock.patch.object(deploy.base,'service_state',return_value=dict(active=True)),\
                 mock.patch.object(deploy,'mount_identity',return_value={'device':123}),\
                 mock.patch.object(deploy,'recorder_report_ready',return_value=True) as report:
                result=deploy.wait_recorder_ready(plan,root,100)
            self.assertEqual(clock[0],220)
            self.assertEqual(result['ram_pending_events'],0)
            self.assertEqual(result['mode'],'flash')
            self.assertTrue(result['report_ok']);report.assert_called_once()
            self.assertNotIn('events.sqlite3',(root/'readiness.json').read_text())

    def test_stale_flash_status_does_not_pass_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            config=dict(runtime_root=str(root),flash_root=str(root/'flash'))
            (root/'storage-status.json').write_text(json.dumps(dict(mode='flash',checked_at=99,active_database=str(root/'flash/events.sqlite3'))))
            clock=[0.0]
            with mock.patch.object(deploy.time,'monotonic',side_effect=lambda:clock[0]),\
                 mock.patch.object(deploy.time,'sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)),\
                 mock.patch.object(deploy.base,'service_state',return_value=dict(active=True)),\
                 mock.patch.object(deploy,'recorder_report_ready') as report:
                with self.assertRaisesRegex(RuntimeError,'did not finish'):
                    deploy.wait_recorder_ready(dict(config=config),root,100,timeout=6)
            report.assert_not_called()


class SpoolRepairTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='spool-repair-test-')
        self.addCleanup(temporary.cleanup)
        self.run=Path(temporary.name).resolve()
        self.runtime=self.run/'vanpi-network-fixture';self.runtime.mkdir()
        self.spool=self.runtime/'spool';self.spool.mkdir();self.spool.chmod(0o2750)
        self.config=dict(runtime_root=str(self.runtime))
        self.paths=[]
        for stream in ('dendelion.log','network.jsonl'):
            for suffix in ('','.1','.2','.3'):
                path=self.spool/(stream+suffix)
                path.write_bytes(('fixture '+path.name+'\n').encode());path.chmod(0o600)
                self.paths.append(path)
        self.sentinel=self.spool/'network.jsonl.4'
        self.sentinel.write_bytes(b'out of bounds sentinel');self.sentinel.chmod(0o666)
        self.outsider=self.run/'outside';self.outsider.write_bytes(b'outside untouched')
        self.bad_owner=set();self.bad_device=set();self.spool_setgid=True
        actual_fstat=os.fstat
        spool_inode=self.spool.stat().st_ino
        def fstat(fd):
            info=actual_fstat(fd);values=list(info)
            # Unit fixtures emulate root-owned files without requiring sudo;
            # the separate real-rsyslog checker exercises actual ownership.
            if not stat.S_ISDIR(info.st_mode) or info.st_ino==spool_inode:values[4]=0
            # The macOS sandbox strips setgid from fixture chmod calls. Real
            # setgid inheritance is covered by the sudo Pi rsyslog canary.
            if info.st_ino==spool_inode and self.spool_setgid:values[0]|=stat.S_ISGID
            if info.st_ino in self.bad_owner:values[4]=12345
            if info.st_ino in self.bad_device:values[2]+=1
            return os.stat_result(values)
        device=self.run.stat().st_dev
        discovery=json.dumps(dict(filesystems=[{'target':str(self.run),'fstype':'tmpfs',
                                               'maj:min':f'{os.major(device)}:{os.minor(device)}'}]))
        actual_open=os.open
        def safe_open(path,flags,*args,**kwargs):
            if isinstance(path,str) and path.startswith(('dendelion.log','network.jsonl')):
                self.assertTrue(flags & os.O_NONBLOCK,'must not block on a FIFO before fstat')
                self.assertTrue(flags & os.O_NOFOLLOW,'must reject symlinks before fstat')
            return actual_open(path,flags,*args,**kwargs)
        self.chown=mock.Mock(wraps=os.fchown)
        self.chmod=mock.Mock(wraps=os.fchmod)
        patches=[mock.patch.object(deploy,'RUN_ROOT',self.run),
                 mock.patch.object(deploy.base,'command',return_value=subprocess.CompletedProcess([],0,discovery,'')),
                 mock.patch.object(deploy.grp,'getgrnam',return_value=types.SimpleNamespace(gr_gid=os.getgid())),
                 mock.patch.object(deploy.os,'fstat',side_effect=fstat),
                 mock.patch.object(deploy.os,'open',side_effect=safe_open),
                 mock.patch.object(deploy.os,'fchown',self.chown),
                 mock.patch.object(deploy.os,'fchmod',self.chmod)]
        for patch in patches:patch.start();self.addCleanup(patch.stop)

    def assert_no_metadata_change(self):
        self.chown.assert_not_called();self.chmod.assert_not_called()
        for path in self.paths[:-1]:self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)

    def test_exact_eight_repaired_without_reading_payload_or_touching_outsiders(self):
        before={path:path.read_bytes() for path in [*self.paths,self.sentinel,self.outsider]}
        identities={path:(path.stat().st_ino,path.stat().st_size) for path in before}
        with mock.patch.object(deploy.os,'read',side_effect=AssertionError('payload read forbidden')),\
             mock.patch.object(deploy.os,'pread',side_effect=AssertionError('payload read forbidden')):
            result=deploy.repair_spool_permissions(self.config)
        self.assertEqual(result,dict(checked=8,repaired=8,missing=0))
        self.assertEqual(self.chown.call_count,8);self.assertEqual(self.chmod.call_count,8)
        for call in self.chown.call_args_list:self.assertEqual(call.args[1:],(-1,os.getgid()))
        for path in self.paths:self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o640)
        self.assertEqual(stat.S_IMODE(self.sentinel.stat().st_mode),0o666)
        for path,data in before.items():
            self.assertEqual(path.read_bytes(),data)
            self.assertEqual((path.stat().st_ino,path.stat().st_size),identities[path])

    def test_missing_rotations_are_skipped(self):
        self.paths[2].unlink();self.paths[6].unlink()
        self.assertEqual(deploy.repair_spool_permissions(self.config),dict(checked=6,repaired=6,missing=2))

    def test_symlink_rejected_before_any_metadata_change(self):
        self.paths[-1].unlink();self.paths[-1].symlink_to(self.outsider)
        with self.assertRaises(OSError):deploy.repair_spool_permissions(self.config)
        self.assert_no_metadata_change()

    def test_hardlink_rejected_before_any_metadata_change(self):
        self.paths[-1].unlink();os.link(self.outsider,self.paths[-1])
        with self.assertRaisesRegex(ValueError,'unsafe spool file'):deploy.repair_spool_permissions(self.config)
        self.assert_no_metadata_change()
        self.assertEqual(self.outsider.read_bytes(),b'outside untouched')

    def test_fifo_is_opened_nonblocking_and_rejected_before_mutation(self):
        self.paths[-1].unlink();os.mkfifo(self.paths[-1],0o600)
        with self.assertRaisesRegex(ValueError,'unsafe spool file'):deploy.repair_spool_permissions(self.config)
        self.assert_no_metadata_change()

    def test_nonroot_owner_and_wrong_filesystem_fail_closed(self):
        for changed in (self.bad_owner,self.bad_device):
            changed.add(self.paths[-1].stat().st_ino)
            with self.assertRaisesRegex(ValueError,'unsafe spool file'):deploy.repair_spool_permissions(self.config)
            self.assert_no_metadata_change();changed.clear()

    def test_spool_must_already_have_setgid(self):
        self.spool_setgid=False
        self.spool.chmod(0o750)
        with self.assertRaisesRegex(ValueError,'02750'):deploy.repair_spool_permissions(self.config)
        self.assert_no_metadata_change()

    def test_runtime_symlink_and_external_path_fail_closed(self):
        alias=self.run/'alias';alias.symlink_to(self.runtime)
        with self.assertRaises(OSError):deploy.repair_spool_permissions(dict(runtime_root=str(alias)))
        with self.assertRaisesRegex(ValueError,'direct child'):
            deploy.repair_spool_permissions(dict(runtime_root=str(self.outsider/'runtime')))
        self.assert_no_metadata_change()

    def test_non_tmpfs_or_changed_mount_is_rejected_before_metadata_change(self):
        for filesystem,device in (('ext4','0:0'),('tmpfs','999:999')):
            rows=dict(filesystems=[{'target':str(self.run),'fstype':filesystem,'maj:min':device}])
            with mock.patch.object(deploy.base,'command',return_value=subprocess.CompletedProcess([],0,json.dumps(rows),'')):
                with self.assertRaises(ValueError):deploy.repair_spool_permissions(self.config)
            self.assert_no_metadata_change()


@unittest.skipUnless(Path('/proc/self/fd').is_dir(),'Linux migration integration')
class StorageMigrationIntegration(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory(prefix='network-storage-integration-',dir='/tmp')
        self.addCleanup(directory.cleanup)
        self.root=Path(directory.name).resolve()
        self.mount=self.root/'flash';self.mount.mkdir()
        self.runtime=self.root/'runtime';self.runtime.mkdir()
        self.old_db=self.root/'old.sqlite3';create_database(self.old_db)
        self.old_logs=self.root/'oldlogs';self.old_logs.mkdir()
        (self.old_logs/'dendelion.log').write_bytes(b'old-first\nrepeated\nrepeated\n')
        self.conf=self.root/'storage.json'
        self.backup_conf=self.root/'backup.conf';self.backup_conf.write_text("PREFIX=keep\nBORG_EXCLUDES=(\n  '/keep'\n)\nSUFFIX=keep\n")
        self.frontend=self.root/'frontend'
        for name in ('old','older'):
            path=self.frontend/'releases'/name;path.mkdir(parents=True)
            (path/'index.html').write_text(name)
            (path/'react_dashboard_preview.py').write_text('existing server')
        (self.frontend/'current').symlink_to('releases/old')
        (self.frontend/'previous').symlink_to('releases/older')
        self.core=self.root/'core.py';self.core.write_text('old=True\n')
        self.receiver=self.root/'30-openwrt-dendelion.conf';self.receiver.write_text('old receiver')
        self.new=self.root/'new.py'
        targets={'core.py':str(self.core),'new.py':str(self.new),'receiver.conf':str(self.receiver),'config.json':str(self.conf)}
        self.config=dict(mountpoint=str(self.mount),flash_root=str(self.mount/'vanpi-network'),runtime_root=str(self.runtime))
        self.contents={'core.py':b'new=True\n','new.py':b'new=True\n','receiver.conf':b'new receiver','config.json':json.dumps(self.config).encode()}
        self.states={name:dict(enabled='enabled',active=True) for name in deploy.SERVICES}
        self.fail_once=False
        def command(args,check=True):
            if args[0]=='/usr/bin/systemctl' and args[1] in ('stop','restart'):
                if args[1:] == ['restart','network-flight-recorder'] and self.fail_once:
                    self.fail_once=False
                    raise RuntimeError('fixture start failure')
                self.states[args[-1]]['active']=args[1]=='restart'
            if args[0]=='/usr/bin/curl':
                import time
                (self.runtime/'storage-status.json').write_text(json.dumps(dict(mode='flash',active_database=str(self.mount/'vanpi-network/events.sqlite3'),checked_at=time.time())))
            return subprocess.CompletedProcess(args,0,'{"ok":true}','')
        patches=[mock.patch.object(deploy,'TARGETS',targets),mock.patch.object(deploy,'CONFIG',self.conf),
                 mock.patch.object(deploy,'OLD_DB',self.old_db),mock.patch.object(deploy,'OLD_LOGS',self.old_logs),
                 mock.patch.object(deploy,'BACKUP_CONF',self.backup_conf),mock.patch.object(deploy,'BACKUPS',self.root/'backups'),
                 mock.patch.object(deploy.base,'FRONTEND',self.frontend),mock.patch.object(deploy.base,'command',side_effect=command),
                 mock.patch.object(deploy.base,'service_state',side_effect=lambda name:copy.deepcopy(self.states[name])),
                 mock.patch.object(deploy,'mount_identity',return_value=dict(device=self.mount.stat().st_dev)),
                 mock.patch.object(deploy,'repair_spool_permissions',return_value=dict(checked=8,repaired=8,missing=0)),
                 mock.patch('pwd.getpwnam',return_value=types.SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())),
                 mock.patch.object(deploy.base.os,'chown')]
        for patch in patches:
            patch.start();self.addCleanup(patch.stop)
        self.plan=dict(schema_version=1,release='network-storage-integration',config=self.config,files=[],
                       frontend_files=[dict(relative='index.html',sha256=deploy.base.sha(b'new frontend'))])
        for source,destination in targets.items():
            info=deploy.base.regular_info(destination)
            self.plan['files'].append(dict(source=source,destination=destination,sha256=deploy.base.sha(self.contents[source]),head_sha256=info['sha256'] if info else None))
        deploy.inspect(self.plan)
        directory=tempfile.TemporaryDirectory(prefix='network-storage-stage.',dir='/tmp')
        self.addCleanup(directory.cleanup);self.stage=Path(directory.name)
        for index,row in enumerate(self.plan['files']):(self.stage/str(index)).write_bytes(self.contents[row['source']])
        (self.stage/'frontend-0').write_bytes(b'new frontend')

    def test_full_migration_rollback_keeps_new_history_and_original_config(self):
        original_conf=self.backup_conf.read_bytes()
        deploy.apply(self.plan,self.stage)
        flash_db=self.mount/'vanpi-network/events.sqlite3'
        self.assertEqual(self.core.read_text(),'new=True\n')
        self.assertTrue(self.conf.exists())
        self.assertIn("'/var/lib/vanpi-network'",self.backup_conf.read_text())
        connection=sqlite3.connect(flash_db)
        connection.execute("INSERT INTO events VALUES(3,'after migration')");connection.commit();connection.close()
        deploy.rollback(self.plan['release'])
        self.assertEqual(self.core.read_text(),'old=True\n')
        self.assertFalse(self.new.exists());self.assertFalse(self.conf.exists())
        self.assertEqual(self.backup_conf.read_bytes(),original_conf)
        self.assertEqual(os.readlink(self.frontend/'current'),'releases/old')
        self.assertTrue(all(value['active'] for value in self.states.values()))
        for path in (self.old_db,flash_db):
            connection=sqlite3.connect(path)
            self.assertEqual(connection.execute('SELECT count(*) FROM events').fetchone()[0],3)
            connection.close()
        self.assertEqual((self.mount/'vanpi-network/openwrt/dendelion.log').read_bytes(),(self.old_logs/'dendelion.log').read_bytes())

    def test_failed_start_restores_prior_code_and_leaves_both_histories(self):
        self.fail_once=True
        with self.assertRaisesRegex(RuntimeError,'fixture start failure'):
            deploy.apply(self.plan,self.stage)
        self.assertEqual(self.core.read_text(),'old=True\n')
        self.assertFalse(self.conf.exists())
        self.assertEqual(os.readlink(self.frontend/'current'),'releases/old')
        self.assertTrue((self.mount/'vanpi-network/events.sqlite3').exists())
        self.assertTrue(self.old_db.exists())
        self.assertTrue(all(value['active'] for value in self.states.values()))

    def test_missing_config_repair_never_remigrates_retired_sd_database(self):
        deploy.apply(self.plan,self.stage)
        self.conf.unlink()
        self.old_db.unlink()
        update=copy.deepcopy(self.plan)
        update['release']='network-storage-config-repair'
        deploy.inspect(update)
        self.assertFalse(update['migration'])
        self.assertEqual(update['source_database_bytes'],0)


if __name__=='__main__':
    unittest.main()
