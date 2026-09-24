import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

DIRECTORY = Path(__file__).resolve().parents[1]/'bettertouchtool'
sys.path.insert(0, str(DIRECTORY))
spec = importlib.util.spec_from_file_location('repair_script_paths', DIRECTORY/'repair_script_paths.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.path.pop(0)


class PathRepairTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for relative in ('macbook/scripts/sns.sh', 'macbook/scripts/wake_device.py'):
            target = self.root/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
        self.old = str(self.root/'sh/sns.sh')
        self.new = str(self.root/'macbook/scripts/sns.sh')

    def test_replaces_only_exact_path_preserving_every_argument_and_quote(self):
        command = f"'{self.old}' pink_noise 'rear speaker'; echo \"keep me\""
        value, changes = module.rewrite_command(command, self.root)
        self.assertEqual(value, command.replace(self.old, self.new))
        self.assertEqual(changes, [{'old': self.old, 'new': self.new, 'occurrences': 1}])

    def test_tilde_path_and_repeated_occurrences(self):
        command = '~/dev/scripts/sh/sns.sh stop; ~/dev/scripts/sh/sns.sh play'
        value, changes = module.rewrite_command(command, self.root)
        self.assertEqual(value.count('~/dev/scripts/macbook/scripts/sns.sh'), 2)
        self.assertEqual(changes[0]['occurrences'], 2)

    def test_no_prefix_or_unrelated_checkout_replacements(self):
        for command in [self.old+'.old', self.old+'/extra', '/different'+self.old, self.new]:
            self.assertEqual(module.rewrite_command(command, self.root), (command, []))

    def test_missing_destination_fails_closed(self):
        (self.root/'macbook/scripts/sns.sh').unlink()
        with self.assertRaisesRegex(RuntimeError, 'replacement is missing'):
            module.rewrite_command(self.old, self.root)

    def test_wake_helper_interpreter_repaired_but_not_arbitrary_python(self):
        old_python = '/usr/local/opt/python@3.9/bin/python3'
        command = old_python+' '+str(self.root/'automation/wake_device.py')
        with patch.object(module.Path, 'exists', return_value=False):
            value, changes = module.rewrite_command(command, self.root)
        self.assertEqual(value, '/usr/bin/python3 '+str(self.root/'macbook/scripts/wake_device.py'))
        self.assertEqual(len(changes), 2)
        other = old_python+' /unrelated/tool.py'
        self.assertEqual(module.rewrite_command(other, self.root), (other, []))

    def database(self):
        database = self.root/'test.sqlite'
        with sqlite3.connect(database) as c:
            c.executescript('''CREATE TABLE ZBTTBASEENTITY (
                Z_PK INTEGER,ZUNIQUEIDENTIFIER TEXT,ZPARENT INTEGER,ZGESTURETYPE INTEGER,
                ZBELONGSTOPRESET2 INTEGER,ZENABLEDNEW INTEGER,ZACTIVATED INTEGER,ZACTION INTEGER,ZLAUNCHPATH TEXT);
                INSERT INTO ZBTTBASEENTITY VALUES (1,'preset',NULL,NULL,NULL,NULL,2,NULL,NULL);
                INSERT INTO ZBTTBASEENTITY VALUES (2,'Media',NULL,767,1,1,NULL,366,NULL);
                INSERT INTO ZBTTBASEENTITY VALUES (3,'button',2,773,1,1,NULL,366,NULL);
                INSERT INTO ZBTTBASEENTITY VALUES (4,'touchbar',NULL,630,1,1,NULL,366,NULL);
                INSERT INTO ZBTTBASEENTITY VALUES (5,'inactive',NULL,NULL,NULL,NULL,0,NULL,NULL);
            ''')
            for pk, uid, parent, kind, preset, enabled in [
                    (10,'menu-action',3,-1,1,1), (11,'named',None,643,1,1),
                    (12,'touchbar-button',4,629,1,1), (13,'inactive-action',None,643,5,1),
                    (14,'disabled-action',3,-1,1,0), (15,'orphan',None,-1,1,1)]:
                c.execute('INSERT INTO ZBTTBASEENTITY VALUES (?,?,?,?,?,?,NULL,206,?)',
                          (pk,uid,parent,kind,preset,enabled,self.old+' noise'))
        return database

    def test_plan_excludes_touchbar_disabled_inactive_and_orphan_records(self):
        database = self.database()
        plan = module.build_plan(database, self.root)
        self.assertEqual([t['uuid'] for t in plan['targets']], ['menu-action','named'])
        self.assertTrue(all(t['field'] == 'BTTShellTaskActionScript' for t in plan['targets']))

    def test_disabled_ancestor_excludes_its_actions(self):
        database = self.database()
        with sqlite3.connect(database) as c:
            c.execute('UPDATE ZBTTBASEENTITY SET ZENABLEDNEW=0 WHERE Z_PK=2')
        self.assertEqual([t['uuid'] for t in module.build_plan(database,self.root)['targets']], ['named'])

    def test_inspect_never_calls_btt_or_backs_up(self):
        import contextlib, io
        plan = {'targets': [{'uuid': 'u', 'replacements': [{'old': self.old, 'new': self.new}]}]}
        with patch.object(module,'current_database',return_value='unused'), \
             patch.object(module,'build_plan',return_value=plan), \
             patch.object(module,'worker') as worker, \
             patch.object(module,'backup_configuration') as backup, contextlib.redirect_stdout(io.StringIO()):
            module.main(['--inspect'])
        worker.assert_not_called()
        backup.assert_not_called()

    def test_saved_report_distinguishes_pending_from_unexpected_changes(self):
        previous = {'a': {'ZLAUNCHPATH': 'old', 'ZORDER': 3, 'config': {'BTTLastChangeUUID': 'old', 'width': 40},
                          'ZICONDATA3': b'old'}}
        plan = {'targets': [{'uuid': 'a', 'after': 'new'}]}
        with patch.object(module,'records',return_value=previous):
            self.assertEqual(module.saved_report('unused',previous,plan), (['a'],[]))
        after = {'a': {**previous['a'], 'ZLAUNCHPATH': 'new', 'config': {'BTTLastChangeUUID': 'new','width':40}}}
        with patch.object(module,'records',return_value=after):
            self.assertEqual(module.saved_report('unused',previous,plan), ([],[]))
            after['a']['ZORDER'] = 9
            self.assertTrue(module.saved_report('unused',previous,plan)[1])


if __name__ == '__main__':
    unittest.main()
