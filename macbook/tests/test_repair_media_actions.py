"""Public CLI inspection uses isolated exports/databases, never live BTT."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from macbook.bettertouchtool.btt_common import MEDIA

SCRIPT = Path(__file__).resolve().parents[1]/'bettertouchtool/repair_media_actions.py'


class ActionRecoveryCLITests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.database = self.root/'config.sqlite'
        self.export = self.root/'export.json'
        with sqlite3.connect(self.database) as c:
            c.executescript('''CREATE TABLE ZBTTBASEENTITY (
                Z_PK INTEGER,ZUNIQUEIDENTIFIER TEXT,ZPARENT INTEGER,ZBELONGSTOPRESET2 INTEGER,
                ZGESTURETYPE INTEGER,ZACTION INTEGER,ZORDER INTEGER,ZICONDATA3 BLOB,
                ZACTIONDATA TEXT,ZLAUNCHPATH TEXT,ZADDITIONALACTIONSTRING TEXT,
                ZSHORTCUT TEXT,ZISENABLED INTEGER,ZENABLEDNEW INTEGER,ZNAME3 TEXT,ZACTIVATED INTEGER,
                ZGESTURECONFIG TEXT);
                INSERT INTO ZBTTBASEENTITY (Z_PK,ZUNIQUEIDENTIFIER,ZNAME3,ZACTIVATED)
                VALUES (42,'preset','Master',2),(43,'inactive','Legacy',0);''')
        self.insert(1,MEDIA,ZGESTURETYPE=767)
        self.insert(2,'button',ZGESTURETYPE=773,ZPARENT=1,ZORDER=7,ZISENABLED=0)
        self.action = {'BTTUUID':'action','BTTPredefinedActionType':248,
                       'BTTNamedTriggerToTrigger':'Partymode','BTTOrder':119,'BTTIsPureAction':True}
        self.write_export()
        self.insert(3,'named',ZGESTURETYPE=643,ZACTION=206,ZGESTURECONFIG='Partymode',
                    ZLAUNCHPATH='echo fixture',ZADDITIONALACTIONSTRING='/bin/zsh:::-c:::-:::')

    def insert(self, pk, uid, **values):
        row = dict(Z_PK=pk,ZUNIQUEIDENTIFIER=uid,ZBELONGSTOPRESET2=42,ZISENABLED=1,
                   ZENABLEDNEW=1,ZACTION=366,ZORDER=0)
        row.update(values)
        with sqlite3.connect(self.database) as c:
            c.execute('INSERT INTO ZBTTBASEENTITY ('+','.join(row)+') VALUES ('+
                      ','.join('?' for _ in row)+')',tuple(row.values()))

    def write_export(self):
        self.export.write_text(json.dumps({'media':{'BTTUUID':MEDIA,'BTTTriggerType':767,
            'BTTMenuItems':[{'BTTUUID':'button','BTTTriggerType':773,'BTTMenuName':'join',
                             'BTTMenuItemActions':[self.action]}]}}))

    def invoke(self, *extra):
        return subprocess.run([sys.executable,'-B',str(SCRIPT),'--database',str(self.database),
                               '--source',str(self.export),*extra],capture_output=True,text=True,timeout=10)

    def invoke_dependencies(self, *extra):
        return subprocess.run([sys.executable,'-B',str(SCRIPT),'--database',str(self.database),
                               '--dependencies-only',*extra],capture_output=True,text=True,timeout=10)

    def test_inspection_finds_empty_button_without_mutating_database(self):
        before = self.database.read_bytes()
        result = self.invoke('--inspect')
        self.assertEqual(result.returncode,0,result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['missing_buttons'],1)
        self.assertEqual(report['buttons'][0]['order'],7)
        self.assertEqual(report['named_triggers_to_restore'],[])
        self.assertEqual(self.database.read_bytes(),before)

    def test_existing_button_actions_are_preserved(self):
        self.insert(4,'custom-action',ZGESTURETYPE=-1,ZPARENT=2,ZACTION=480)
        result = self.invoke('--inspect')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('No enabled Media buttons',result.stdout)

    def test_inactive_named_dependency_is_copied_not_activated(self):
        with sqlite3.connect(self.database) as c:
            c.execute('UPDATE ZBTTBASEENTITY SET ZBELONGSTOPRESET2=43 WHERE Z_PK=3')
        result = self.invoke('--inspect')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['named_triggers_to_restore'],['Partymode'])
        with sqlite3.connect(self.database) as c:
            self.assertEqual(c.execute('SELECT ZACTIVATED FROM ZBTTBASEENTITY WHERE Z_PK=43').fetchone()[0],0)

    def test_ambiguous_active_named_dependency_refuses_recovery(self):
        self.insert(4,'duplicate',ZGESTURETYPE=643,ZACTION=206,ZGESTURECONFIG='Partymode')
        result = self.invoke('--inspect')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Ambiguous',result.stderr)

    def test_missing_action_source_fails_closed(self):
        self.export.write_text(json.dumps({'media':{'BTTUUID':MEDIA,'BTTMenuItems':[]}}))
        result = self.invoke('--inspect')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('No action backup',result.stderr)

    def test_disabled_button_excluded_and_legacy_is_enabled_flag_not_authoritative(self):
        with sqlite3.connect(self.database) as c:
            c.execute('UPDATE ZBTTBASEENTITY SET ZENABLEDNEW=0 WHERE Z_PK=2')
        result = self.invoke('--inspect')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('No enabled Media buttons',result.stdout)

    def test_offline_database_can_never_be_applied(self):
        result = self.invoke('--apply')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('read-only',result.stderr)

    def test_existing_action_id_elsewhere_is_not_reparented(self):
        self.insert(4,'action',ZGESTURETYPE=-1,ZACTION=248,ZLAUNCHPATH='Partymode')
        result = self.invoke('--inspect')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('UUID already exists',result.stderr)

    def test_dependencies_only_copies_missing_inactive_named_trigger_without_mutation(self):
        with sqlite3.connect(self.database) as c:
            c.execute('UPDATE ZBTTBASEENTITY SET ZBELONGSTOPRESET2=43 WHERE Z_PK=3')
        self.insert(4,'reference',ZGESTURETYPE=-1,ZPARENT=2,ZACTION=248,
                    ZLAUNCHPATH='Partymode')
        before = self.database.read_bytes()
        result = self.invoke_dependencies()
        self.assertEqual(result.returncode,0,result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['missing_buttons'],0)
        self.assertEqual(report['action_records'],0)
        self.assertEqual(report['named_triggers_to_restore'],['Partymode'])
        self.assertEqual(report['named_trigger_orders'],[1])
        self.assertEqual(self.database.read_bytes(),before)
        with sqlite3.connect(self.database) as c:
            self.assertEqual(c.execute('SELECT ZACTIVATED FROM ZBTTBASEENTITY WHERE Z_PK=43').fetchone()[0],0)

    def test_dependencies_only_ignores_reference_below_disabled_ancestor(self):
        with sqlite3.connect(self.database) as c:
            c.execute('UPDATE ZBTTBASEENTITY SET ZBELONGSTOPRESET2=43 WHERE Z_PK=3')
            c.execute('UPDATE ZBTTBASEENTITY SET ZENABLEDNEW=0 WHERE Z_PK=2')
        self.insert(4,'disabled-reference',ZGESTURETYPE=-1,ZPARENT=2,ZACTION=248,
                    ZLAUNCHPATH='Partymode')
        result = self.invoke_dependencies('--inspect')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('All enabled Media named-trigger dependencies resolve',result.stdout)

    def test_dependencies_only_keeps_valid_active_dependency(self):
        self.insert(4,'reference',ZGESTURETYPE=-1,ZPARENT=2,ZACTION=248,
                    ZLAUNCHPATH='Partymode')
        result = self.invoke_dependencies('--inspect')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('All enabled Media named-trigger dependencies resolve',result.stdout)

    def test_dependencies_only_assigns_distinct_consecutive_root_orders(self):
        with sqlite3.connect(self.database) as c:
            c.execute('UPDATE ZBTTBASEENTITY SET ZBELONGSTOPRESET2=43 WHERE Z_PK=3')
        self.insert(4,'other-named',ZGESTURETYPE=643,ZACTION=206,ZGESTURECONFIG='Other',
                    ZLAUNCHPATH='echo other',ZADDITIONALACTIONSTRING='config',ZBELONGSTOPRESET2=43)
        self.insert(5,'first-reference',ZGESTURETYPE=-1,ZPARENT=2,ZACTION=248,
                    ZLAUNCHPATH='Partymode')
        self.insert(6,'second-reference',ZGESTURETYPE=-1,ZPARENT=2,ZACTION=248,
                    ZLAUNCHPATH='Other')
        result = self.invoke_dependencies('--inspect')
        self.assertEqual(result.returncode,0,result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['named_triggers_to_restore'],['Other','Partymode'])
        self.assertEqual(report['named_trigger_orders'],[1,2])

    def test_dependencies_only_rejects_ambiguous_or_complex_sources(self):
        self.insert(4,'reference',ZGESTURETYPE=-1,ZPARENT=2,ZACTION=248,
                    ZLAUNCHPATH='Partymode')
        self.insert(5,'duplicate-active',ZGESTURETYPE=643,ZACTION=206,
                    ZGESTURECONFIG='Partymode')
        result = self.invoke_dependencies('--inspect')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Ambiguous active named trigger',result.stderr)
        with sqlite3.connect(self.database) as c:
            c.execute('DELETE FROM ZBTTBASEENTITY WHERE Z_PK=5')
            c.execute('UPDATE ZBTTBASEENTITY SET ZBELONGSTOPRESET2=43 WHERE Z_PK=3')
        self.insert(6,'nested-action',ZGESTURETYPE=-1,ZPARENT=3,ZACTION=206)
        result = self.invoke_dependencies('--inspect')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('additional actions',result.stderr)


if __name__ == '__main__':
    unittest.main()
