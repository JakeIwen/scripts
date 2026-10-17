"""Saved-state checks use a real SQLite fixture, never the live BTT database."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from repair_text_transforms import saved_errors


class SavedTextActionsTest(unittest.TestCase):
    def test_export_key_cannot_pass_as_an_executable_saved_script(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'configuration.sqlite'
            with sqlite3.connect(database) as connection:
                connection.execute('''CREATE TABLE ZBTTBASEENTITY (
                    Z_PK INTEGER, ZUNIQUEIDENTIFIER TEXT, ZPARENT INTEGER, ZBELONGSTOPRESET2 INTEGER,
                    ZGESTURETYPE INTEGER, ZACTION INTEGER, ZORDER INTEGER, ZICONDATA3 BLOB,
                    ZACTIONDATA BLOB, ZLAUNCHPATH TEXT, ZADDITIONALACTIONSTRING TEXT, ZENABLEDNEW INTEGER)''')
                connection.execute("INSERT INTO ZBTTBASEENTITY (Z_PK,ZUNIQUEIDENTIFIER) VALUES (1,'parent')")
                connection.execute('''INSERT INTO ZBTTBASEENTITY
                    (Z_PK,ZUNIQUEIDENTIFIER,ZPARENT,ZACTION,ZORDER,ZENABLEDNEW) VALUES (2,'action',1,281,2,1)''')
            plan = {'edits': [{'parent': 'parent', 'before': {'BTTUUID': 'action', 'BTTOrder': 2},
                              'patch': {'BTTEnabled': 1, 'BTTPredefinedActionType': 281,
                                        'BTTAdditionalActionData': json.dumps({'BTTAppleScriptString': 'script'})}}]}
            for payload, matches in [({'BTTScriptString': 'script'}, False),
                                     ({'BTTAppleScriptString': 'script'}, True),
                                     ({'BTTAppleScriptString': 'changed'}, False),
                                     ({'BTTAppleScriptString': 'changed', 'BTTScriptString': 'script'}, False)]:
                with self.subTest(payload=payload):
                    with sqlite3.connect(database) as connection:
                        connection.execute('UPDATE ZBTTBASEENTITY SET ZACTIONDATA=? WHERE Z_PK=2', (json.dumps(payload),))
                    self.assertEqual(saved_errors(database, plan) == [], matches)
            with sqlite3.connect(database) as connection:
                connection.execute('UPDATE ZBTTBASEENTITY SET ZORDER=3 WHERE Z_PK=2')
            self.assertIn('parent/order/enabled', saved_errors(database, plan)[0])
