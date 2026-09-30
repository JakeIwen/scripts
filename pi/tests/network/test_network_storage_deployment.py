"""Storage migration safety tests using isolated files and mocked services/mounts."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
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
