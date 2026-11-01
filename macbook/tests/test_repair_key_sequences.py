"""Key-sequence restore planning against fixture BTT stores; no BTT process is contacted."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import repair_key_sequences as repair

APP, GESTURE, KEY, SEQUENCE, PRESET = 2, 9, 10, 11, 13
COLUMNS = sorted({'ZUNIQUEIDENTIFIER', 'ZPARENT', 'ZGESTURETYPE', 'ZBELONGSTOPRESET2', 'ZKEYSEQUENCE', 'ZNAME3',
                  'ZBUNDLEIDENTIFIER', 'ZENABLEDNEW', 'ZACTION', 'ZCONDITIONS', 'ZORDER', 'ZACTIONDATA',
                  'ZADDITIONALINTEGER', 'ZLASTUSED', 'ZUPDATEDAT', 'ZNUMBEROFREQUIRED', 'ZCODE',
                  *repair.SEQUENCE_FIELDS, *repair.KEY_FIELDS, *repair.UNMAPPED_SEQUENCE, *repair.UNMAPPED_KEY,
                  *('ZBELONGSTOKEYSEQUENCE' + name.upper() for name in repair.KEY_LISTS),
                  *('Z_FOK_BELONGSTOKEYSEQUENCE' + name.upper() for name in repair.KEY_LISTS)})
CODES = {'S': 1, 'H': 4, 'R': 15, 'G': 5, 'L': 37, 'F': 3, '3': 20, 'K': 40, 'A': 0, 'Q': 12, 'B': 11}


class Store:
    """A minimal BTT Core Data store: entities, the app-gesture join and Master/Global/Trash rows."""

    def __init__(self, path):
        self.path = path
        existing = path.exists()
        self.connection = sqlite3.connect(path)
        if existing:
            rows = self.connection.execute('SELECT Z_PK,ZNAME3,ZBUNDLEIDENTIFIER FROM ZBTTBASEENTITY '
                                           'WHERE Z_ENT IN (?, ?)', (APP, PRESET)).fetchall()
            self.master = next(pk for pk, name, _ in rows if name == 'Master')
            self.apps = {bundle: pk for pk, _, bundle in rows if bundle}
            return
        self.connection.execute('CREATE TABLE ZBTTBASEENTITY (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, %s)'
                                % ', '.join(COLUMNS))
        self.connection.execute('CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER, Z_NAME TEXT)')
        self.connection.executemany('INSERT INTO Z_PRIMARYKEY VALUES (?, ?)', [
            (APP, 'APP'), (GESTURE, 'Gesture'), (KEY, 'Key'), (SEQUENCE, 'KeySequence'), (PRESET, 'Preset')])
        self.connection.execute('CREATE TABLE Z_2APPS_GESTURES (Z_2GESTURES INTEGER, Z_9APPS_GESTURES INTEGER)')
        self.master = self.row(PRESET, ZNAME3='Master', ZUNIQUEIDENTIFIER='preset')
        self.apps = {bundle: self.row(APP, ZBUNDLEIDENTIFIER=bundle, ZUNIQUEIDENTIFIER='app-' + bundle)
                     for bundle in ('BT.G', 'BT.D', 'com.github.atom')}

    def row(self, entity, **values):
        names = ['Z_ENT', *values]
        cursor = self.connection.execute('INSERT INTO ZBTTBASEENTITY (%s) VALUES (%s)' % (
            ', '.join(names), ', '.join('?' * len(names))), [entity, *values.values()])
        return cursor.lastrowid

    def trigger(self, uuid, letters, apps=('BT.G',), text='text', last_used=None, **values):
        sequence = self.row(SEQUENCE, ZUNIQUEIDENTIFIER=uuid + '-sequence', ZBETWEENTIME=.3,
                            ZDELETEAFTERWARDS=len(letters), ZKEYCOUNT=2 * len(letters), ZKEYBOARDTYPE=0,
                            ZNUMBEROFREQUIRED=0)
        for index, letter in enumerate(letters):
            for down in (1, 0):
                name = 'DOWN' if down else 'UP'
                self.row(KEY, ZUNIQUEIDENTIFIER=f'{uuid}-{letter}-{down}', ZCODE=CODES[letter], ZDOWN=down,
                         ZCHARACTER=' ' + letter, ZORDERRELEVANT=down, ZREQUIRED=1, ZTIME=671529600.0,
                         **{'ZBELONGSTOKEYSEQUENCE' + name: sequence, 'Z_FOK_BELONGSTOKEYSEQUENCE' + name: index,
                            'ZBELONGSTOKEYSEQUENCEMIXED': sequence,
                            'Z_FOK_BELONGSTOKEYSEQUENCEMIXED': 2 * index + (1 - down)})
        fields = {**dict(ZUNIQUEIDENTIFIER=uuid, ZGESTURETYPE=repair.KEY_SEQUENCE, ZBELONGSTOPRESET2=self.master,
                         ZKEYSEQUENCE=sequence, ZENABLEDNEW=1, ZACTION=118, ZORDER=0, ZADDITIONALINTEGER='0',
                         ZACTIONDATA=json.dumps({'BTTActionTextToPaste': text}), ZLASTUSED=last_used), **values}
        pk = self.row(GESTURE, **fields)
        self.connection.executemany('INSERT INTO Z_2APPS_GESTURES VALUES (?, ?)',
                                    [(self.apps[bundle], pk) for bundle in apps])
        return pk

    def close(self):
        self.connection.commit()
        self.connection.close()
        return self.path


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def store(self, version):
        return Store(self.root / f'btt_data_store.version_6_{version}_build_2026010101')

    def preview(self, current):
        with patch.object(repair, 'current_database', return_value=current):
            return repair.restore()

    def test_restores_only_sequences_no_current_trigger_types(self):
        old = self.store(748)
        old.trigger('SHRG-OLD', 'SHRG', apps=('BT.G', 'BT.D'), last_used=10)
        old.trigger('SHRG-NEW', 'SHRG', apps=('BT.G', 'BT.D'), last_used=None)
        old.trigger('L3K', 'L3K', apps=('BT.G', 'BT.D'))
        old.trigger('RLF-OLD', 'RLF')
        old.close()
        current = self.store(885)
        current.trigger('RLF-RERECORDED', 'RLF')
        result = self.preview(current.close())
        self.assertEqual([(item['keys'], item['uuid']) for item in result['restore']],
                         [('L-3-K', 'L3K'), ('S-H-R-G', 'SHRG-OLD')])
        self.assertEqual([item['uuid'] for item in result['skipped_duplicates']], ['SHRG-NEW'])
        self.assertFalse(result['changes_applied'])

    def test_trash_only_app_scoped_and_multi_action_triggers_wait_for_review(self):
        old = self.store(748)
        old.trigger('DELETED', 'SHRG', apps=('BT.D',))
        old.trigger('ATOM', 'L3K', apps=('com.github.atom',))
        old.trigger('MULTI', 'AQB', ZACTION=366)
        old.close()
        result = self.preview(self.store(885).close())
        self.assertEqual(result['restore'], [])
        self.assertEqual({item['uuid']: item['reason'] for item in result['needs_review']},
                         {'DELETED': 'only in BTT Trash', 'ATOM': 'app-specific scope',
                          'MULTI': 'multi-action trigger'})

    def test_current_trash_linked_triggers_are_reported_at_risk(self):
        current = self.store(885)
        current.trigger('LINKED', 'SHRG', apps=('BT.G', 'BT.D'))
        current.trigger('TRASHED', 'L3K', apps=('BT.D',))
        self.assertEqual(self.preview(current.close())['at_risk_trash_linked'], ['LINKED'])

    def test_definition_rebuilds_btt_export_shape(self):
        old = self.store(748)
        old.trigger('AQB', 'AQB', text='advantagequickbase', ZACTION=193)
        trigger = repair.read_store(old.close()).triggers[0]
        node = repair.definition(trigger)
        self.assertEqual((node['BTTTriggerClass'], node['BTTAppBundleIdentifier'], node['BTTTriggerBelongsToPreset']),
                         ('BTTTriggerTypeKeySequence', 'BT.G', 'Master'))
        self.assertEqual(node['BTTAdditionalActionData'], {'BTTActionTextToPaste': 'advantagequickbase'})
        sequence = node['BTTKeySequence']
        self.assertEqual([key['BTTKEYCode'] for key in sequence['BTTKeySequenceDownKeys']], [0, 12, 11])
        self.assertEqual([key.get('BTTKEYDown', 0) for key in sequence['BTTKeySequenceMixedKeys']], [1, 0] * 3)
        self.assertEqual(sequence['BTTKeySequenceDownKeys'][0]['BTTKEYTime'], 671529600)
        self.assertNotIn('BTTKEYDown', sequence['BTTKeySequenceUpKeys'][0])

    def test_readback_accepts_exports_that_omit_key_code_zero(self):
        old = self.store(748)
        old.trigger('AQB', 'AQB', ZACTION=193)
        wanted = repair.definition(repair.read_store(old.close()).triggers[0])
        exported = json.loads(json.dumps(wanted))
        for keys in exported['BTTKeySequence'].values():
            if isinstance(keys, list):
                for key in keys:
                    if key['BTTKEYCode'] == 0:
                        del key['BTTKEYCode']
        self.assertTrue(repair.runtime_matches(exported, wanted))
        exported['BTTKeySequence']['BTTKeySequenceDownKeys'][1]['BTTKEYCode'] = 13
        self.assertFalse(repair.runtime_matches(exported, wanted))

    def test_saved_check_waits_for_btt_and_refuses_lost_records(self):
        old = self.store(748)
        old.trigger('L3K', 'L3K', apps=('BT.G', 'BT.D'))
        wanted = repair.read_store(old.close()).triggers
        current = self.store(885)
        current.trigger('RLF', 'RLF')
        path = current.close()
        before = repair.read_store(path).uuids
        self.assertFalse(repair.saved(path, wanted, before))
        with self.assertRaises(RuntimeError):
            repair.saved(path, wanted, before | {'GONE'})
        reopened = Store(path)
        reopened.trigger('L3K', 'L3K')
        self.assertTrue(repair.saved(reopened.close(), wanted, before))

    def test_unsupported_store_schema_is_refused_by_name(self):
        store = self.store(748)
        store.connection.execute("DELETE FROM Z_PRIMARYKEY WHERE Z_NAME = 'Preset'")
        with self.assertRaisesRegex(RuntimeError, 'Unsupported BTT store schema .*no Preset'):
            repair.read_store(store.close())

    def test_older_stores_are_newest_first_and_exclude_wal_files(self):
        for version in (640, 748, 885, 900):
            self.store(version).close()
        (self.root / 'btt_data_store.version_6_748_build_2026010101-wal').write_bytes(b'')
        current = self.root / 'btt_data_store.version_6_885_build_2026010101'
        self.assertEqual([path.name for path in repair.older_stores(current)],
                         ['btt_data_store.version_6_748_build_2026010101',
                          'btt_data_store.version_6_640_build_2026010101'])


if __name__ == '__main__':
    unittest.main()
