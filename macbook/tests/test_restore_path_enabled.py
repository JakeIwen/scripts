"""Inspect real SQLite repair backups; never contact BTT or execute actions."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from restore_path_enabled import inspect_backup


class RestoreEnabledTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.plan = root / 'plan.json'
        self.current = root / 'current.sqlite'
        self.original = root / 'configuration.sqlite'
        for database in (self.original, self.current):
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.executescript('''CREATE TABLE ZBTTBASEENTITY (
                    Z_PK INTEGER,ZUNIQUEIDENTIFIER TEXT,ZPARENT INTEGER,ZBELONGSTOPRESET2 INTEGER,
                    ZGESTURETYPE INTEGER,ZACTION INTEGER,ZORDER INTEGER,ZENABLEDNEW INTEGER,
                    ZICONDATA3 BLOB,ZACTIONDATA TEXT,ZLAUNCHPATH TEXT,ZADDITIONALACTIONSTRING TEXT);
                    INSERT INTO ZBTTBASEENTITY VALUES (1,'target',NULL,3,629,206,0,0,NULL,NULL,'old',NULL);
                    INSERT INTO ZBTTBASEENTITY VALUES (2,'other',NULL,3,629,206,1,0,NULL,NULL,'other',NULL);
                ''')
        self.document = {'targets': [{'uuid': 'target', 'action': 206,
                                     'field': 'BTTShellTaskActionScript', 'before': 'old', 'after': 'new'}],
                         'snapshot': [{'uuid': 'target', 'trigger': {
                             'BTTUUID': 'target', 'BTTEnabled2': 0, 'BTTShellTaskActionScript': 'old'}}]}
        self.plan.write_text(json.dumps(self.document))
        self.change("UPDATE ZBTTBASEENTITY SET ZLAUNCHPATH='new',ZENABLEDNEW=1 WHERE Z_PK=1")

    def change(self, statement):
        with closing(sqlite3.connect(self.current)) as connection, connection:
            connection.execute(statement)

    def test_detects_disabled_state_side_effect_even_when_all_paths_are_correct(self):
        _, before, repairs = inspect_backup(self.plan, self.current)
        self.assertEqual(repairs, ['target'])
        self.assertEqual(before['target']['ZENABLEDNEW'], 0)

    def test_recovered_state_is_an_idempotent_noop(self):
        self.change('UPDATE ZBTTBASEENTITY SET ZENABLEDNEW=0 WHERE Z_PK=1')
        self.assertEqual(inspect_backup(self.plan, self.current)[2], [])

    def test_refuses_unrelated_enabled_or_layout_changes(self):
        for statement in ('UPDATE ZBTTBASEENTITY SET ZENABLEDNEW=1 WHERE Z_PK=2',
                          'UPDATE ZBTTBASEENTITY SET ZORDER=5 WHERE Z_PK=1'):
            with self.subTest(statement=statement):
                self.change(statement)
                with self.assertRaisesRegex(RuntimeError, 'unrelated'):
                    inspect_backup(self.plan, self.current)
                self.change('UPDATE ZBTTBASEENTITY SET ZENABLEDNEW=0 WHERE Z_PK=2')
                self.change('UPDATE ZBTTBASEENTITY SET ZORDER=0 WHERE Z_PK=1')

    def test_refuses_partial_or_later_command_edits(self):
        for command in ('old', 'custom'):
            self.change(f"UPDATE ZBTTBASEENTITY SET ZLAUNCHPATH='{command}' WHERE Z_PK=1")
            with self.assertRaisesRegex(RuntimeError, 'refusing recovery'):
                inspect_backup(self.plan, self.current)

    def test_refuses_conflicting_or_duplicate_backup_records(self):
        self.document['snapshot'][0]['trigger']['BTTEnabled2'] = 1
        self.plan.write_text(json.dumps(self.document))
        with self.assertRaisesRegex(RuntimeError, 'states do not agree'):
            inspect_backup(self.plan, self.current)
        self.document['snapshot'].append(self.document['snapshot'][0])
        self.plan.write_text(json.dumps(self.document))
        with self.assertRaisesRegex(RuntimeError, 'duplicate'):
            inspect_backup(self.plan, self.current)


if __name__ == '__main__':
    unittest.main()
