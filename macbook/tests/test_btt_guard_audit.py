"""Focused read-only database and comparison tests for BTT Guard."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from macbook.bettertouchtool.btt_guard.audit import compare
from macbook.bettertouchtool.btt_guard.database import read_snapshot
from macbook.bettertouchtool.btt_guard.model import AuditStatus, FindingKind


class GuardDatabaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)/'btt_data_store.version_6_826_build_fixture'
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executescript('''CREATE TABLE ZBTTBASEENTITY (
                Z_PK INTEGER, Z_ENT INTEGER, ZUNIQUEIDENTIFIER TEXT,
                ZBUNDLEIDENTIFIER TEXT, ZPARENT INTEGER,
                ZBELONGSTOPRESET2 INTEGER, ZGESTURETYPE INTEGER,
                ZENABLEDNEW INTEGER, ZORDER INTEGER, ZACTION INTEGER,
                ZACTIONCATEGORY INTEGER, ZICONDATA3 BLOB, ZACTIONDATA BLOB,
                ZLAUNCHPATH TEXT, ZADDITIONALACTIONSTRING TEXT,
                ZSHORTCUT TEXT, ZGESTURECONFIG TEXT, ZACTIVATED INTEGER);
                CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER, Z_NAME TEXT);
                INSERT INTO Z_PRIMARYKEY VALUES (2,'App'),(9,'Gesture');
                CREATE TABLE Z_2APPS_GESTURES (
                    Z_2GESTURES INTEGER, Z_9APPS_GESTURES INTEGER);
                INSERT INTO ZBTTBASEENTITY (
                    Z_PK,Z_ENT,ZUNIQUEIDENTIFIER,ZBUNDLEIDENTIFIER)
                    VALUES (300,2,'app-uuid','BT.G');''')
        self.insert(100, 'preset', ZACTIVATED=2)
        self.insert(200, 'old-preset', ZACTIVATED=0)

    def insert(self, pk, uuid, **values):
        app_scope = values.pop('app_scope', False)
        row = dict(Z_PK=pk, Z_ENT=9, ZUNIQUEIDENTIFIER=uuid,
                   ZBUNDLEIDENTIFIER=None, ZPARENT=None,
                   ZBELONGSTOPRESET2=None, ZGESTURETYPE=0, ZENABLEDNEW=1,
                   ZORDER=0, ZACTION=366, ZACTIONCATEGORY=0, ZICONDATA3=None,
                   ZACTIONDATA=None, ZLAUNCHPATH=None,
                   ZADDITIONALACTIONSTRING=None, ZSHORTCUT=None,
                   ZGESTURECONFIG=None, ZACTIVATED=0)
        row.update(values)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('INSERT INTO ZBTTBASEENTITY ('+','.join(row)+') VALUES ('+
                               ','.join('?' for _ in row)+')', tuple(row.values()))
            if app_scope:
                connection.execute('INSERT INTO Z_2APPS_GESTURES VALUES (?,?)', (300, pk))

    @staticmethod
    def blob(value):
        return b'\x01'+json.dumps(value).encode()

    def test_snapshot_follows_recursive_dependencies_and_ignores_orphans(self):
        config = self.blob({
            'BTTMenuVisibility': 2,
            'BTTMenuModifierKeys': 1048576,
            'BTTMenuCategoryText': 1,
            'BTTMenuItemFontColor': 'private-appearance',
        })
        self.insert(1, 'root', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767,
                    ZICONDATA3=config, app_scope=True)
        self.insert(2, 'button', ZPARENT=1, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=773, ZORDER=4)
        self.insert(3, 'named-action', ZPARENT=2, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=-1, ZACTION=248, ZLAUNCHPATH='Private Name')
        self.insert(4, 'named-trigger', ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=643, ZGESTURECONFIG='Private Name', ZACTION=206,
                    ZLAUNCHPATH='secret command')
        menu_data = {'BTTMenuActionMenuID': 'floating',
                     'BTTMenuActionMenuName': 'Private Menu'}
        self.insert(5, 'show-menu', ZPARENT=4, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=-1, ZACTION=386,
                    ZACTIONDATA=self.blob(menu_data))
        self.insert(6, 'floating', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767,
                    ZICONDATA3=self.blob({'BTTMenuElementIdentifier': 'Private Menu'}))
        self.insert(7, 'floating-item', ZPARENT=6, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=774)
        self.insert(8, 'legacy-orphan', ZBELONGSTOPRESET2=200,
                    ZGESTURETYPE=767, ZENABLEDNEW=0)

        before = self.path.read_bytes()
        snapshot = read_snapshot(self.path, ['root'])

        self.assertEqual(set(snapshot['records']),
                         {'root', 'button', 'named-action', 'named-trigger',
                          'show-menu', 'floating', 'floating-item'})
        self.assertEqual(snapshot['presets'], {'preset': True})
        self.assertEqual(snapshot['btt_version'], '6.826')
        self.assertEqual(snapshot['records']['root']['parent'], None)
        self.assertEqual(snapshot['records']['button']['parent'], 'root')
        self.assertEqual(snapshot['records']['root']['config'], {
            'BTTMenuModifierKeys': 1048576, 'BTTMenuVisibility': 2})
        self.assertEqual(snapshot['records']['root']['app_scope'], ['BT.G'])
        self.assertEqual(snapshot['records']['button']['app_scope'], [])
        self.assertEqual(snapshot['records']['named-trigger']['payload']['launch_path'],
                         'secret command')
        self.assertNotIn('gesture_config', snapshot['records']['button']['payload'])
        self.assertEqual(snapshot['findings'], [])
        self.assertEqual(self.path.read_bytes(), before)

    def test_app_scope_is_direct_deduplicated_and_sorted(self):
        self.insert(1, 'root', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767,
                    app_scope=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('''INSERT INTO ZBTTBASEENTITY
                (Z_PK,Z_ENT,ZUNIQUEIDENTIFIER,ZBUNDLEIDENTIFIER)
                VALUES (301,2,'second-app','com.example.app')''')
            connection.execute('INSERT INTO Z_2APPS_GESTURES VALUES (?,?)', (301, 1))
            connection.execute('INSERT INTO Z_2APPS_GESTURES VALUES (?,?)', (300, 1))
        snapshot = read_snapshot(self.path, ['root'])
        self.assertEqual(snapshot['records']['root']['app_scope'],
                         ['BT.G', 'com.example.app'])

    def test_dependency_and_empty_button_findings_do_not_expose_names(self):
        self.insert(1, 'root', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767)
        self.insert(2, 'empty', ZPARENT=1, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=773)
        self.insert(3, 'missing-named', ZPARENT=1, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=-1, ZACTION=248, ZLAUNCHPATH='Secret Missing Name')
        snapshot = read_snapshot(self.path, ['root'])
        kinds = {item['kind'] for item in snapshot['findings']}
        self.assertEqual(kinds, {FindingKind.UNASSIGNED.value,
                                 FindingKind.DEPENDENCY_CHANGED.value})
        self.assertNotIn('Secret Missing Name', json.dumps(snapshot['findings']))

    def test_script_only_button_is_not_reported_as_empty(self):
        self.insert(1, 'root', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767)
        self.insert(2, 'scripted', ZPARENT=1, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=773,
                    ZICONDATA3=self.blob({
                        'BTTMenuCategoryContentScript': 1,
                        'BTTMenuItemScriptActive': True,
                        'BTTMenuScriptSettings': {'BTTAppleScriptString': 'private source'},
                        'BTTMenuItemText': 'runtime label',
                    }))
        snapshot = read_snapshot(self.path, ['root'])
        self.assertEqual(snapshot['findings'], [])
        self.assertEqual(snapshot['records']['scripted']['config'], {
            'BTTMenuItemScriptActive': True,
            'BTTMenuScriptSettings': {'BTTAppleScriptString': 'private source'},
        })
        self.assertNotIn('BTTMenuCategoryContentScript',
                         snapshot['records']['scripted']['config'])

    def test_no_action_with_sentinel_or_stale_payload_is_still_unassigned(self):
        self.insert(1, 'root', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767)
        self.insert(2, 'empty', ZPARENT=1, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=773, ZACTION=366, ZSHORTCUT='-1',
                    ZACTIONDATA=self.blob({'stale': True}),
                    ZADDITIONALACTIONSTRING='stale configuration')
        snapshot = read_snapshot(self.path, ['root'])
        self.assertEqual(snapshot['findings'], [{
            'kind': FindingKind.UNASSIGNED.value,
            'uuid': 'empty',
            'detail': 'enabled action button has no action',
        }])

    def test_direct_keyboard_action_requires_meaningful_shortcut(self):
        self.insert(1, 'root', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767)
        self.insert(2, 'sentinel', ZPARENT=1, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=773, ZACTION=-1, ZSHORTCUT='-1')
        self.insert(3, 'keyboard', ZPARENT=1, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=773, ZACTION=-1, ZSHORTCUT='42')
        snapshot = read_snapshot(self.path, ['root'])
        self.assertEqual([item['uuid'] for item in snapshot['findings']], ['sentinel'])

    def test_inactive_script_settings_do_not_exempt_an_empty_button(self):
        self.insert(1, 'root', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767)
        self.insert(2, 'inactive-script', ZPARENT=1, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=773, ZACTION=366,
                    ZICONDATA3=self.blob({
                        'BTTMenuItemScriptActive': 0,
                        'BTTMenuScriptSettings': {'BTTAppleScriptString': 'stale source'},
                    }))
        snapshot = read_snapshot(self.path, ['root'])
        self.assertEqual([item['uuid'] for item in snapshot['findings']],
                         ['inactive-script'])

    def test_disabled_only_child_action_does_not_hide_unassigned_button(self):
        self.insert(1, 'root', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767)
        self.insert(2, 'button', ZPARENT=1, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=773, ZACTION=366)
        self.insert(3, 'disabled-action', ZPARENT=2, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=-1, ZACTION=206, ZENABLEDNEW=0)
        snapshot = read_snapshot(self.path, ['root'])
        self.assertEqual([item['uuid'] for item in snapshot['findings']], ['button'])

    def test_enabled_child_action_keeps_button_assigned_among_disabled_siblings(self):
        self.insert(1, 'root', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767)
        self.insert(2, 'button', ZPARENT=1, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=773, ZACTION=366)
        self.insert(3, 'disabled-action', ZPARENT=2, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=-1, ZACTION=206, ZENABLEDNEW=0)
        self.insert(4, 'enabled-action', ZPARENT=2, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=-1, ZACTION=206)
        snapshot = read_snapshot(self.path, ['root'])
        self.assertEqual(snapshot['findings'], [])

    def test_disabled_floating_menu_dependency_is_retained_and_reported(self):
        self.insert(1, 'root', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767)
        self.insert(2, 'show-menu', ZPARENT=1, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=-1, ZACTION=386,
                    ZACTIONDATA=self.blob({'BTTMenuActionMenuID': 'floating'}))
        self.insert(3, 'floating', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767,
                    ZENABLEDNEW=0)
        self.insert(4, 'floating-item', ZPARENT=3, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=774)
        snapshot = read_snapshot(self.path, ['root'])
        self.assertTrue({'floating', 'floating-item'} <= set(snapshot['records']))
        self.assertEqual(snapshot['findings'], [{
            'kind': FindingKind.DEPENDENCY_CHANGED.value,
            'uuid': 'show-menu',
            'detail': 'floating-menu dependency disabled',
        }])

    def test_disabled_ancestor_retains_records_without_requiring_dependencies(self):
        self.insert(1, 'root', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767,
                    ZENABLEDNEW=0)
        self.insert(2, 'empty', ZPARENT=1, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=773)
        self.insert(3, 'named-action', ZPARENT=2, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=-1, ZACTION=248, ZLAUNCHPATH='Missing While Disabled')
        snapshot = read_snapshot(self.path, ['root'])
        self.assertEqual(set(snapshot['records']), {'root', 'empty', 'named-action'})
        self.assertEqual(snapshot['findings'], [])

    def test_include_ids_finds_detached_records_without_scoping_foreign_graph(self):
        self.insert(1, 'root', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767)
        self.insert(2, 'item', ZPARENT=1, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=774)
        expected = read_snapshot(self.path, ['root'])
        self.insert(3, 'foreign', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767)
        self.insert(4, 'foreign-child', ZPARENT=3, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=774)
        self.insert(5, 'detached-action', ZPARENT=3, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=-1, ZACTION=248, ZORDER=119,
                    ZLAUNCHPATH='Missing Detached Name')
        self.insert(6, 'foreign-action', ZPARENT=3, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=-1, ZACTION=206, ZORDER=3)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('UPDATE ZBTTBASEENTITY SET ZPARENT=3 WHERE ZUNIQUEIDENTIFIER=?',
                               ('item',))
        current = read_snapshot(self.path, ['root'], include_ids=['item', 'detached-action'])
        self.assertEqual(set(current['records']), {'root', 'item', 'detached-action'})
        self.assertEqual(current['records']['detached-action']['order'], 1)
        self.assertEqual(current['findings'], [])
        report = compare(expected, current, 'checkpoint-detached')
        self.assertEqual([item['kind'] for item in report['findings']],
                         [FindingKind.REPARENTED.value])

    def test_missing_root_keeps_explicit_expected_preset_in_scope(self):
        self.insert(1, 'root', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767)
        expected = read_snapshot(self.path, ['root'])
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('DELETE FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER=?', ('root',))
        current = read_snapshot(self.path, ['root'], include_ids=['root'],
                                include_preset_ids=['preset'])
        self.assertEqual(current['presets'], {'preset': True})
        report = compare(expected, current, 'checkpoint-root')
        self.assertEqual(report['findings'], [{
            'kind': FindingKind.MISSING.value,
            'uuid': 'root',
            'detail': 'expected root missing',
        }])

    def test_single_sparse_action_order_is_canonical_and_raw_change_is_healthy(self):
        self.insert(1, 'root', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767)
        self.insert(2, 'button', ZPARENT=1, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=773)
        self.insert(3, 'action', ZPARENT=2, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=-1, ZACTION=206, ZORDER=119,
                    ZLAUNCHPATH='private command')
        expected = read_snapshot(self.path, ['root'])
        self.assertEqual(expected['records']['action']['order'], 0)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('UPDATE ZBTTBASEENTITY SET ZORDER=0 WHERE ZUNIQUEIDENTIFIER=?',
                               ('action',))
        current = read_snapshot(self.path, ['root'])
        self.assertEqual(compare(expected, current, 'checkpoint-sparse')['status'],
                         AuditStatus.HEALTHY.value)

    def test_swapped_action_sequence_reports_order_changes(self):
        self.insert(1, 'root', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767)
        self.insert(2, 'button', ZPARENT=1, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=773)
        self.insert(3, 'action-a', ZPARENT=2, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=-1, ZACTION=206, ZORDER=10)
        self.insert(4, 'action-b', ZPARENT=2, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=-1, ZACTION=206, ZORDER=20)
        expected = read_snapshot(self.path, ['root'])
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('UPDATE ZBTTBASEENTITY SET ZORDER=30 WHERE ZUNIQUEIDENTIFIER=?',
                               ('action-a',))
            connection.execute('UPDATE ZBTTBASEENTITY SET ZORDER=5 WHERE ZUNIQUEIDENTIFIER=?',
                               ('action-b',))
        current = read_snapshot(self.path, ['root'])
        findings = compare(expected, current, 'checkpoint-order')['findings']
        self.assertEqual([(item['kind'], item['uuid']) for item in findings], [
            (FindingKind.ORDER_CHANGED.value, 'action-a'),
            (FindingKind.ORDER_CHANGED.value, 'action-b'),
        ])

    def test_equal_raw_action_orders_use_uuid_tie_breaker(self):
        self.insert(1, 'root', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767)
        self.insert(2, 'button', ZPARENT=1, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=773)
        self.insert(3, 'action-z', ZPARENT=2, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=-1, ZACTION=206, ZORDER=7)
        self.insert(4, 'action-a', ZPARENT=2, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=-1, ZACTION=206, ZORDER=7)
        snapshot = read_snapshot(self.path, ['root'])
        self.assertEqual(snapshot['records']['action-a']['order'], 0)
        self.assertEqual(snapshot['records']['action-z']['order'], 1)

    def test_menu_identifier_resolves_when_action_id_is_not_a_record_uuid(self):
        self.insert(1, 'root', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767)
        self.insert(2, 'show-menu', ZPARENT=1, ZBELONGSTOPRESET2=100,
                    ZGESTURETYPE=-1, ZACTION=386,
                    ZACTIONDATA=self.blob({'BTTMenuActionMenuID': 'stable-menu-id'}))
        self.insert(3, 'floating-uuid', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767,
                    ZICONDATA3=self.blob({'BTTMenuElementIdentifier': 'stable-menu-id'}))
        snapshot = read_snapshot(self.path, ['root'])
        self.assertIn('floating-uuid', snapshot['records'])
        self.assertEqual(snapshot['findings'], [])

    def test_duplicate_uuid_and_schema_mismatch_fail_closed(self):
        self.insert(1, 'duplicate', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767)
        self.insert(2, 'duplicate', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767)
        with self.assertRaisesRegex(ValueError, 'Duplicate stored BTT UUID'):
            read_snapshot(self.path, ['duplicate'])
        broken = self.path.with_name('broken.sqlite')
        with closing(sqlite3.connect(broken)) as connection, connection:
            connection.execute('CREATE TABLE ZBTTBASEENTITY (Z_PK INTEGER)')
        with self.assertRaisesRegex(ValueError, 'Unsupported BTT database schema'):
            read_snapshot(broken, ['root'])

    def test_missing_app_scope_relationship_schema_fails_closed(self):
        self.insert(1, 'root', ZBELONGSTOPRESET2=100, ZGESTURETYPE=767)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute('DROP TABLE Z_2APPS_GESTURES')
        with self.assertRaisesRegex(ValueError, 'app-scope relationship schema'):
            read_snapshot(self.path, ['root'])


class GuardAuditTests(unittest.TestCase):
    def snapshot(self):
        record = dict(uuid='action', parent='button', preset='preset',
                      trigger_type=-1, enabled=True, order=0, action_type=206,
                      action_category=0,
                      payload={'launch_path': 'secret old command'},
                      config={'BTTMenuVisibility': 1}, app_scope=[])
        return dict(schema_version=1, captured_at=1.0, btt_version='6.826',
                    database_name='fixture', roots=['root'], presets={'preset': True},
                    records={'action': record}, findings=[])

    def test_compare_reports_fields_without_payload_values(self):
        expected = self.snapshot()
        current = deepcopy(expected)
        current['records']['action'].update(parent='other', preset='other-preset',
                                            enabled=False, order=3, action_type=248,
                                            action_category=1)
        current['records']['action']['payload']['launch_path'] = 'secret new command'
        current['records']['action']['config']['BTTMenuVisibility'] = 2
        current['presets']['preset'] = False
        current['records']['unrelated'] = deepcopy(current['records']['action'])
        current['records']['unrelated']['uuid'] = 'unrelated'

        report = compare(expected, current, 'checkpoint-1')

        self.assertEqual(report['status'], AuditStatus.DRIFT.value)
        kinds = {item['kind'] for item in report['findings']}
        self.assertTrue({FindingKind.REPARENTED.value,
                         FindingKind.PRESET_CHANGED.value,
                         FindingKind.DISABLED.value,
                         FindingKind.ACTION_CHANGED.value,
                         FindingKind.CONFIG_CHANGED.value} <= kinds)
        self.assertNotIn(FindingKind.ORDER_CHANGED.value, kinds)
        encoded = json.dumps(report)
        self.assertNotIn('secret old command', encoded)
        self.assertNotIn('secret new command', encoded)
        self.assertNotIn('unrelated', encoded)

    def test_menu_item_reorder_and_unrelated_addition_are_not_damage(self):
        expected = self.snapshot()
        expected['records']['action']['trigger_type'] = 773
        current = deepcopy(expected)
        current['records']['action']['order'] = 99
        current['records']['extra'] = deepcopy(current['records']['action'])
        current['records']['extra']['uuid'] = 'extra'
        report = compare(expected, current, 'checkpoint-2')
        self.assertEqual(report['status'], AuditStatus.HEALTHY.value)
        self.assertEqual(report['findings'], [])

    def test_detached_action_order_is_subsumed_by_parent_drift(self):
        expected = self.snapshot()
        expected['records']['action']['order'] = 1
        current = deepcopy(expected)
        current['records']['action']['parent'] = None
        current['records']['action']['order'] = 0
        report = compare(expected, current, 'checkpoint-orphan')
        self.assertEqual([(item['kind'], item['detail']) for item in report['findings']],
                         [(FindingKind.REPARENTED.value, 'field: parent')])

    def test_app_scope_loss_is_configuration_drift_without_exposing_bundle(self):
        expected = self.snapshot()
        expected['records']['action']['app_scope'] = ['private.bundle']
        current = deepcopy(expected)
        current['records']['action']['app_scope'] = []
        report = compare(expected, current, 'checkpoint-scope')
        self.assertEqual(report['findings'], [{
            'kind': FindingKind.CONFIG_CHANGED.value,
            'uuid': 'action',
            'detail': 'fields: app_scope',
        }])
        self.assertNotIn('private.bundle', json.dumps(report))

    def test_current_dependency_finding_and_missing_record_are_drift(self):
        expected = self.snapshot()
        current = deepcopy(expected)
        del current['records']['action']
        current['findings'] = [{
            'kind': FindingKind.DEPENDENCY_CHANGED.value,
            'uuid': 'root',
            'detail': 'named-trigger dependency unresolved',
        }]
        report = compare(expected, current, 'checkpoint-3')
        self.assertEqual([item['kind'] for item in report['findings']],
                         [FindingKind.DEPENDENCY_CHANGED.value,
                          FindingKind.MISSING.value])


if __name__ == '__main__':
    unittest.main()
