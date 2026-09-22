"""RPS repair: persistent linkage and active named-trigger resolution only."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

DIRECTORY=Path(__file__).resolve().parents[1]/'bettertouchtool'
sys.path.insert(0,str(DIRECTORY))
spec=importlib.util.spec_from_file_location('repair_rps',DIRECTORY/'repair_rps.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
sys.path.pop(0)


class RPSPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'tmp').mkdir()
        self.path=self.root/'btt.sqlite'
        with sqlite3.connect(self.path) as c:
            c.execute('''CREATE TABLE ZBTTBASEENTITY (
                Z_PK INTEGER,ZUNIQUEIDENTIFIER TEXT,ZPARENT INTEGER,ZBELONGSTOPRESET2 INTEGER,
                ZGESTURETYPE INTEGER,ZACTION INTEGER,ZORDER INTEGER,ZICONDATA3 BLOB,
                ZACTIONDATA BLOB,ZLAUNCHPATH TEXT,ZADDITIONALACTIONSTRING TEXT,
                ZGESTURECONFIG TEXT,ZACTIVATED INTEGER,ZISENABLED INTEGER,ZENABLEDNEW INTEGER)''')
        self.insert(Z_PK=100,ZUNIQUEIDENTIFIER='master-preset',ZACTIVATED=2)
        self.insert(Z_PK=101,ZUNIQUEIDENTIFIER='inactive-preset',ZACTIVATED=0)
        self.insert(Z_PK=1,ZUNIQUEIDENTIFIER=module.MEDIA,ZGESTURETYPE=767,ZBELONGSTOPRESET2=100,ZACTION=366)
        self.insert(Z_PK=2,ZUNIQUEIDENTIFIER=module.BUTTON,ZGESTURETYPE=773,ZPARENT=1,ZBELONGSTOPRESET2=100,ZACTION=366)
        self.insert(Z_PK=3,ZUNIQUEIDENTIFIER=module.SYNC,ZGESTURETYPE=643,ZBELONGSTOPRESET2=100,
                    ZACTION=206,ZGESTURECONFIG=module.NAME,ZLAUNCHPATH=module.COMMAND)
        self.insert(Z_PK=4,ZUNIQUEIDENTIFIER='inactive-old-sync',ZGESTURETYPE=643,ZBELONGSTOPRESET2=101,
                    ZACTION=206,ZGESTURECONFIG=module.NAME,ZLAUNCHPATH='/obsolete/sync_scripts.sh')

    def insert(self,**row):
        row.setdefault('ZORDER',0);row.setdefault('ZISENABLED',1);row.setdefault('ZENABLEDNEW',1)
        with sqlite3.connect(self.path) as c:
            c.execute('INSERT INTO ZBTTBASEENTITY ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))

    def action(self,**overrides):
        values=dict(Z_PK=5,ZUNIQUEIDENTIFIER=module.ACTION,ZPARENT=2,ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=-1,ZACTION=248,ZORDER=0,ZLAUNCHPATH=module.NAME)
        values.update(overrides);self.insert(**values)

    def test_missing_link_is_detected_but_inactive_obsolete_trigger_is_ignored(self):
        self.assertFalse(module.preflight(self.path))

    def test_correct_persistent_link_is_already_installed(self):
        self.action();self.assertTrue(module.preflight(self.path))

    def test_wrong_parent_disabled_action_and_command_are_rejected(self):
        self.action(ZPARENT=1)
        with self.assertRaises(RuntimeError):module.preflight(self.path)
        with sqlite3.connect(self.path) as c:c.execute('UPDATE ZBTTBASEENTITY SET ZPARENT=2,ZISENABLED=0 WHERE Z_PK=5')
        with self.assertRaises(RuntimeError):module.preflight(self.path)
        with sqlite3.connect(self.path) as c:c.execute("UPDATE ZBTTBASEENTITY SET ZISENABLED=1,ZLAUNCHPATH='Different' WHERE Z_PK=5")
        with self.assertRaises(RuntimeError):module.preflight(self.path)

    def test_ambiguous_active_named_triggers_prevent_repair(self):
        with sqlite3.connect(self.path) as c:c.execute('UPDATE ZBTTBASEENTITY SET ZACTIVATED=2 WHERE Z_PK=101')
        with self.assertRaisesRegex(RuntimeError,'ambiguous'):module.preflight(self.path)

    def test_other_rps_actions_are_not_overwritten(self):
        self.action(ZUNIQUEIDENTIFIER='another-action')
        with self.assertRaisesRegex(RuntimeError,'other saved actions'):module.preflight(self.path)

    def test_backup_retains_full_sqlite_and_exports(self):
        snapshot={'media':{'BTTUUID':module.MEDIA},'sync':{'BTTUUID':module.SYNC}}
        with patch.dict(module.backup_configuration.__globals__,{'ROOT':self.root}):
            path=module.backup_configuration(self.path,snapshot)
        self.assertEqual(json.loads(path.read_text()),snapshot)
        self.assertEqual(path.parent.stat().st_mode & 0o777,0o700)
        self.assertFalse(module.preflight(path.parent/'configuration.sqlite'))

    def test_backup_failure_prevents_apply(self):
        with patch('sys.argv',['repair_rps.py']),patch.object(module,'current_database',return_value=self.path), \
             patch.object(module,'worker',return_value='{"media":{},"sync":{}}') as worker, \
             patch.object(module,'backup_configuration',side_effect=RuntimeError('backup failure')):
            with self.assertRaisesRegex(RuntimeError,'backup failure'):module.main()
        worker.assert_called_once_with('snapshot')


if __name__=='__main__':unittest.main()
