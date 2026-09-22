"""Persistence checks must detect API-only repairs that never reached SQLite."""
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('stabilize_media',Path(__file__).resolve().parents[1]/'bettertouchtool/stabilize_media.py')
module=importlib.util.module_from_spec(spec)
sys.path.insert(0,str(Path(spec.origin).parent))
spec.loader.exec_module(module)
sys.path.pop(0)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'btt.sqlite'
        with sqlite3.connect(self.path) as c:
            c.executescript('''CREATE TABLE ZBTTBASEENTITY (
              Z_PK INTEGER,ZUNIQUEIDENTIFIER TEXT,ZPARENT INTEGER,ZBELONGSTOPRESET2 INTEGER,
              ZGESTURETYPE INTEGER,ZACTION INTEGER,ZORDER INTEGER,ZICONDATA3 BLOB,
              ZACTIONDATA BLOB,ZLAUNCHPATH TEXT,ZADDITIONALACTIONSTRING TEXT);
            ''')
            c.execute('INSERT INTO ZBTTBASEENTITY VALUES (1,?,NULL,42,767,366,0,?,NULL,NULL,NULL)',
                      (module.MEDIA,b'\x01{"BTTMenuSizingBehavior":3}'))
            c.execute("INSERT INTO ZBTTBASEENTITY VALUES (2,'button',1,42,773,366,0,NULL,NULL,NULL,NULL)")
        self.entity={'uuid':'action','parent':'button','node':{'BTTUUID':'action','BTTTriggerType':-1,
                      'BTTOrder':0,'BTTPredefinedActionType':386,'BTTAdditionalActionData':{'BTTMenuActionMenuID':'popup'}}}
        self.plan={'entities':[self.entity],'configChanges':[{'uuid':module.MEDIA,'patch':{'BTTMenuSizingBehavior':3}}],
                   'media':{'BTTMenuItems':[{'BTTUUID':'button','BTTOrder':0}]}}

    def test_missing_action_rejected_even_when_api_would_claim_it_exists(self):
        self.assertEqual(module.disk_errors(self.path,self.plan),['action: not saved'])

    def test_saved_record_with_correct_parent_preset_and_payload_passes(self):
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO ZBTTBASEENTITY VALUES (3,'action',2,42,-1,386,0,NULL,?,NULL,NULL)",
                      (b'{"BTTMenuActionMenuID":"popup"}',))
        self.assertEqual(module.disk_errors(self.path,self.plan),[])

    def test_unattached_or_wrong_preset_action_is_rejected(self):
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO ZBTTBASEENTITY VALUES (3,'action',NULL,NULL,-1,386,0,NULL,?,NULL,NULL)",
                      (b'{"BTTMenuActionMenuID":"popup"}',))
        self.assertTrue(module.disk_errors(self.path,self.plan))

    def test_backup_database_is_read_only(self):
        c=module.connect(self.path)
        self.addCleanup(c.close)
        with self.assertRaises(sqlite3.OperationalError):
            c.execute('DELETE FROM ZBTTBASEENTITY')

    def test_rerun_skips_persisted_layout_but_keeps_verification(self):
        saved = module.records(self.path)
        self.assertEqual(module.pending_config_changes(saved,self.plan['configChanges']),[])
        self.assertEqual(len(self.plan['configChanges']),1)
        changes = [{'uuid':module.MEDIA,'patch':{'BTTMenuSizingBehavior':3,'BTTMenuFrameHeight':40}}]
        self.assertEqual(module.pending_config_changes(saved,changes),
                         [{'uuid':module.MEDIA,'patch':{'BTTMenuFrameHeight':40}}])
        self.assertEqual(changes[0]['patch']['BTTMenuSizingBehavior'],3)

    def test_missing_layout_target_aborts(self):
        with self.assertRaisesRegex(RuntimeError,'layout target missing'):
            module.pending_config_changes({},self.plan['configChanges'])


if __name__=='__main__':unittest.main()
