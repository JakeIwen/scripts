"""Editing-key installer: conditions, emitted scripts, install flow and saved state.

Uses Foundation's NSPredicate (osascript), Node for the emitted Real-JS entries,
an in-memory BTT socket fake and a SQLite fixture; never the live configuration.
"""
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import btt_socket
import install_text_editing_keys as keys
import text_editing_conditions as conditions
import trigger_install as triggers
from macbook.bettertouchtool.tests.btt_socket_fake import FakeBTT

# Runs each emitted entry with BTT's Real-JS helpers stubbed and records the calls.
HARNESS = r'''
const vm = require('node:vm');
const entries = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
(async () => {
    const out = [];
    for (const {script, name, jxa} of entries) {
        const calls = [];
        const context = vm.createContext({
            readFile: async path => { calls.push(['readFile', path]); return 'SOURCE'; },
            runJXA: async source => { calls.push(['runJXA', source]); return jxa; },
            callBTT: async (command, args) => { calls.push(['callBTT', command, args]); }
        });
        vm.runInContext(script, context);
        out.push({result: await vm.runInContext(name + '()', context), calls});
    }
    console.log(JSON.stringify(out));
})();
'''


def by_note(text):
    return next(d for d in keys.definitions() if d['BTTGestureNotes'].endswith(text))


def run_entries(entries):
    result = subprocess.run(['node', '-e', HARNESS], input=json.dumps(entries), capture_output=True,
                            text=True, timeout=30, check=True)
    return json.loads(result.stdout)


def call_options(source, function):
    prefix = 'SOURCE\nSOURCE\n' + function + '('
    if not source.startswith(prefix) or not source.endswith(');'):
        raise AssertionError('unexpected runtime call: ' + source)
    return json.loads(source[len(prefix):-2])


@unittest.skipUnless(sys.platform == 'darwin', 'NSPredicate evaluation needs macOS osascript')
class ConditionTests(unittest.TestCase):
    def test_conditions_pass_their_truth_table_in_foundation(self):
        self.assertEqual(conditions.truth_table_errors(), [])

    def test_dropping_the_writable_check_is_caught_before_install(self):
        loose = conditions.CARET_CONDITION.replace('(?=.*AXSelectedText [(]W[)]:  ").*', '.*')
        self.assertNotEqual(loose, conditions.CARET_CONDITION)
        with patch.object(conditions, 'CARET_CONDITION', loose):
            self.assertIn('caret condition fires on: read-only text', conditions.truth_table_errors())

    def test_stored_conditions_compare_in_nspredicate_format(self):
        reserialized = conditions.CARET_CONDITION.replace(' AND ', '  AND  ')
        self.assertEqual(conditions.canonical(reserialized), conditions.canonical(conditions.CARET_CONDITION))


@unittest.skipUnless(shutil.which('node'), 'emitted scripts are executed with Node')
class EmittedScriptTests(unittest.TestCase):
    def entry(self, definition, jxa):
        data = definition['BTTAdditionalActionData']
        return {'script': data['BTTAppleScriptString'], 'name': data['BTTScriptFunctionToCall'], 'jxa': jxa}

    def test_each_wrap_key_runs_the_shared_runtime_with_its_character(self):
        chars = [char for char, _, _ in keys.WRAP_KEYS]
        outcomes = run_entries([self.entry(by_note('wrap the selection in ' + char), '{"ok":true,"result":"wrapped"}')
                                for char in chars])
        for char, outcome in zip(chars, outcomes):
            self.assertEqual(outcome['result'], 'wrapped')
            *reads, (_, source) = outcome['calls']
            self.assertEqual(reads, [['readFile', path] for path in keys.RUNTIME])
            self.assertEqual(call_options(source, 'runEditingKey'),
                             {'kind': 'wrap', 'open': char, 'directory': str(keys.DIRECTORY)})

    def test_a_refused_wrap_is_reported_as_a_notification(self):
        [outcome] = run_entries([self.entry(by_note('in ('), '{"ok":false,"error":"fixture refusal"}')])
        self.assertEqual(outcome['result'], 'fixture refusal')
        self.assertEqual(outcome['calls'][-1], ['callBTT', 'display_notification',
                                                {'title': 'Wrap stopped', '': 'fixture refusal'}])

    def test_the_line_keys_share_the_caret_condition_and_distinct_triggers(self):
        cut, copy = by_note('cuts the line'), by_note('copies the line')
        self.assertEqual((cut['BTTShortcutKeyCode'], copy['BTTShortcutKeyCode']), (7, 8))
        for definition in (cut, copy):
            self.assertEqual(definition['BTTShortcutModifierKeys'], triggers.COMMAND)
            self.assertEqual(definition['BTTTriggerConditionsFormat'], conditions.CARET_CONDITION)
            self.assertTrue(definition[triggers.PASSTHROUGH])
        self.assertNotEqual(cut['BTTUUID'], copy['BTTUUID'])
        self.assertEqual(copy['BTTUUID'], keys.stable_uuid('copy line'))

    def test_a_runtime_script_failure_is_reported_with_its_message(self):
        failure = '{"OSAScriptErrorNumberKey":-2700,"NSLocalizedDescription":"Error: SyntaxError"}'
        [outcome] = run_entries([self.entry(by_note('cuts the line'), failure)])
        self.assertEqual(outcome['result'], 'Error: SyntaxError')
        self.assertEqual(outcome['calls'][-1], ['callBTT', 'display_notification',
                                                {'title': 'Cut line stopped', '': 'Error: SyntaxError'}])

    def test_cut_and_copy_run_one_script_that_selects_the_line_itself(self):
        # The runtime selects the line itself through Accessibility, in the same
        # run that presses Cut or Copy; the action sends BTT no keys first.
        for note, kind, result in (('cuts the line', 'cut-line', 'cut line'),
                                   ('copies the line', 'copy-line', 'copied line')):
            with self.subTest(kind=kind):
                [outcome] = run_entries([self.entry(by_note(note), '{"ok":true,"result":"%s"}' % result)])
                self.assertEqual(outcome['result'], result)
                *reads, (_, source) = outcome['calls']
                self.assertEqual(reads, [['readFile', path] for path in keys.RUNTIME])
                self.assertEqual(call_options(source, 'runEditingKey'),
                                 {'kind': kind, 'directory': str(keys.DIRECTORY)})

    def test_a_refused_copy_is_reported_as_a_notification(self):
        [outcome] = run_entries([self.entry(by_note('copies the line'), '{"ok":false,"error":"fixture refusal"}')])
        self.assertEqual(outcome['calls'][-1], ['callBTT', 'display_notification',
                                                {'title': 'Copy line stopped', '': 'fixture refusal'}])

    def test_app_switches_prepare_text_accessibility_outside_excluded_apps(self):
        [outcome] = run_entries([self.entry(by_note('on app switch'), '{"ok":true,"result":"enabled"}')])
        self.assertEqual(outcome['result'], 'enabled')
        self.assertEqual(call_options(outcome['calls'][-1][1], 'runAppSwitch'),
                         {'excluded': list(conditions.EXCLUDED_APPS)})


class InstallFlowTests(unittest.TestCase):
    def setUp(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        self.plan = keys.definitions()
        self.fake = FakeBTT()
        for module, target, value in [(btt_socket, 'call', self.fake), (triggers, 'saved_errors', lambda *args: []),
                                      (keys, 'BACKUPS', directory),
                                      (triggers, 'backup_configuration', lambda *args, **kw: directory / 'backup.json'),
                                      (conditions, 'canonical', lambda predicate: predicate)]:
            patcher = patch.object(module, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('builtins.print')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_fresh_install_creates_everything_disabled_before_enabling(self):
        touched = keys.install('db', self.plan)
        ids = [d['BTTUUID'] for d in self.plan]
        self.assertEqual(touched, sorted(ids))
        self.assertEqual(self.fake.writes, [('add', uid, 0) for uid in ids] + [('update', uid, 1) for uid in ids])

    def test_a_verified_installation_is_left_untouched(self):
        keys.install('db', self.plan)
        self.fake.writes.clear()
        self.assertEqual(keys.install('db', self.plan), [])
        self.assertEqual(self.fake.writes, [])

    def test_an_outdated_key_is_rewritten_alone(self):
        keys.install('db', self.plan)
        self.fake.writes.clear()
        stale = by_note('cuts the line')['BTTUUID']
        self.fake.triggers[stale]['BTTAdditionalActionData']['BTTScriptString'] = 'async function old() {}'
        self.assertEqual(keys.install('db', self.plan), [stale])
        self.assertEqual(self.fake.writes, [('delete', stale, None), ('add', stale, 0), ('update', stale, 1)])

    def test_a_lost_pass_through_stops_with_every_key_disabled(self):
        self.fake.export_hook = lambda exported: exported.pop('BTTAdditionalDataJSON', None)
        with self.assertRaisesRegex(RuntimeError, 'pass-through(.|\n)*All editing keys are disabled'):
            keys.install('db', self.plan)
        self.assertNotIn(('update', self.plan[0]['BTTUUID'], 1), self.fake.writes)

    def test_disable_flips_only_enabled_keys(self):
        keys.install('db', self.plan)
        first = self.plan[0]['BTTUUID']
        self.fake.triggers[first]['BTTEnabled'] = 0
        self.fake.writes.clear()
        touched = triggers.set_enabled(self.plan, 0)
        self.assertEqual(touched, [d['BTTUUID'] for d in self.plan[1:]])
        self.assertTrue(all(t['BTTEnabled'] == 0 for t in self.fake.triggers.values()))


class SavedStateTests(unittest.TestCase):
    def setUp(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        self.database = directory / 'configuration.sqlite'
        self.plan = keys.definitions()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('''CREATE TABLE ZBTTBASEENTITY (Z_PK INTEGER, ZUNIQUEIDENTIFIER TEXT,
                ZPARENT INTEGER, ZBELONGSTOPRESET2 INTEGER, ZGESTURETYPE INTEGER, ZACTION INTEGER,
                ZORDER INTEGER, ZENABLEDNEW INTEGER, ZICONDATA3 BLOB, ZACTIONDATA BLOB, ZLAUNCHPATH TEXT,
                ZADDITIONALACTIONSTRING TEXT, ZKEYCODE INTEGER, ZMODIFIERKEYS INTEGER, ZTRIGGERONDOWN INTEGER,
                ZISENABLED INTEGER, ZCONDITIONS BLOB, ZACTIVATED INTEGER)''')
            connection.execute("INSERT INTO ZBTTBASEENTITY (Z_PK,ZUNIQUEIDENTIFIER,ZACTIVATED) VALUES (1,'preset',2)")
            connection.execute("INSERT INTO ZBTTBASEENTITY (Z_PK,ZUNIQUEIDENTIFIER,ZACTIVATED) VALUES (2,'off',0)")
            for index, wanted in enumerate(self.plan, start=10):
                keyboard = wanted['BTTTriggerType'] == triggers.KEYBOARD
                config = {triggers.PASSTHROUGH: True} if keyboard else {}
                connection.execute('''INSERT INTO ZBTTBASEENTITY (Z_PK,ZUNIQUEIDENTIFIER,ZBELONGSTOPRESET2,
                    ZGESTURETYPE,ZACTION,ZENABLEDNEW,ZICONDATA3,ZACTIONDATA,ZKEYCODE,ZMODIFIERKEYS,ZTRIGGERONDOWN,
                    ZISENABLED,ZCONDITIONS) VALUES (?,?,1,?,281,1,?,?,?,?,?,1,?)''',
                    (index, wanted['BTTUUID'], wanted['BTTTriggerType'], b'\x00' + json.dumps(config).encode(),
                     json.dumps(wanted['BTTAdditionalActionData']), wanted.get('BTTShortcutKeyCode'),
                     wanted.get('BTTShortcutModifierKeys'), 1 if keyboard else None, b'archived' if keyboard else None))

    def update(self, sql, *parameters):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(sql, parameters)

    def test_a_complete_enabled_save_verifies(self):
        self.assertEqual(triggers.saved_errors(self.database, self.plan, 1), [])
        self.assertEqual(triggers.saved_errors(self.database, self.plan, None), [])

    def test_each_unsaved_property_blocks_verification(self):
        cut = by_note('cuts the line')['BTTUUID']
        for sql, expected in [('UPDATE ZBTTBASEENTITY SET ZENABLEDNEW=0 WHERE ZUNIQUEIDENTIFIER=?', 'enabled'),
                              ("UPDATE ZBTTBASEENTITY SET ZICONDATA3=x'007b7d' WHERE ZUNIQUEIDENTIFIER=?", 'pass-through'),
                              ('UPDATE ZBTTBASEENTITY SET ZCONDITIONS=NULL WHERE ZUNIQUEIDENTIFIER=?', 'condition'),
                              ("UPDATE ZBTTBASEENTITY SET ZACTIONDATA='{}' WHERE ZUNIQUEIDENTIFIER=?", 'action'),
                              ('UPDATE ZBTTBASEENTITY SET ZBELONGSTOPRESET2=2 WHERE ZUNIQUEIDENTIFIER=?', 'scope')]:
            with self.subTest(expected=expected):
                self.setUp()
                self.update(sql, cut)
                [error] = triggers.saved_errors(self.database, self.plan, 1)
                self.assertIn(expected, error)

    def test_only_other_enabled_shortcuts_on_these_keys_in_active_presets_conflict(self):
        for index, preset, enabled, keycode in [(90, 1, 1, 7), (91, 2, 1, 7), (92, 1, 0, 7), (93, 1, 1, 8), (94, 1, 1, 9)]:
            self.update('''INSERT INTO ZBTTBASEENTITY (Z_PK,ZUNIQUEIDENTIFIER,ZBELONGSTOPRESET2,ZGESTURETYPE,
                ZKEYCODE,ZMODIFIERKEYS,ZISENABLED,ZENABLEDNEW) VALUES (?,?,?,0,?,?,?,?)''',
                        index, 'other-%d' % index, preset, keycode, triggers.COMMAND, enabled, enabled)
        self.assertEqual(triggers.conflicts(self.database, self.plan), ['other-90', 'other-93'])


if __name__ == '__main__':
    unittest.main()
