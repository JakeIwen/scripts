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
import install_finder_paste as finder
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
    for (const {script, name, jxa, variable, failRead, failJxa, failVariableRead} of entries) {
        const calls = [];
        const context = vm.createContext({
            Date: {now: () => 100000},
            readFile: async path => {
                calls.push(['readFile', path]);
                if (failRead) throw new Error('fixture read failure');
                return 'SOURCE';
            },
            runJXA: async source => {
                calls.push(['runJXA', source]);
                if (failJxa) throw new Error('fixture runJXA rejection');
                return jxa;
            },
            callBTT: async (command, args) => {
                calls.push(['callBTT', command, args]);
                if (command === 'get_string_variable') {
                    if (failVariableRead) throw new Error('fixture variable read failure');
                    return variable;
                }
            }
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

    def test_p4_single_line_roles_never_intercept_even_in_a_menu_less_app(self):
        cases = [{'BTTActiveAppBundleIdentifier': app,
                  'focused_element_details': conditions.CARET.replace('AXTextArea', role),
                  conditions.LINE_CLIPBOARD_VARIABLE: '1'}
                 for app in ('com.apple.TextEdit', 'com.apple.Spotlight', 'com.runningwithcrayons.Alfred', 'com.raycast.macos')
                 for role in ('AXTextField', 'AXTextArea')]
        self.assertEqual(conditions.evaluate(conditions.PASTE_CONDITION, cases)['results'], [False, True] * 4)
        loose = conditions.PASTE_CONDITION.replace('(?=.*AXRole:  "AXTextArea")', '')
        with patch.object(conditions, 'PASTE_CONDITION', loose):
            self.assertIn('paste condition fires on: tagged AXTextField caret', conditions.truth_table_errors())

    def test_stored_conditions_compare_in_nspredicate_format(self):
        reserialized = conditions.CARET_CONDITION.replace(' AND ', '  AND  ')
        self.assertEqual(conditions.canonical(reserialized), conditions.canonical(conditions.CARET_CONDITION))

    def test_a_case_missing_a_condition_variable_fails_even_if_the_condition_short_circuits(self):
        for app in ('com.apple.TextEdit', 'com.microsoft.VSCode'):
            with self.subTest(app=app), self.assertRaisesRegex(RuntimeError, conditions.LINE_CLIPBOARD_VARIABLE):
                conditions.evaluate(conditions.PASTE_CONDITION,
                    [{'BTTActiveAppBundleIdentifier': app, 'focused_element_details': conditions.CARET}])

    def test_every_installed_condition_reads_only_builtins_or_initialized_variables(self):
        allowed = set(conditions.BUILTIN_VARIABLES) | set(conditions.INITIAL_VARIABLES)
        predicates = {d['BTTTriggerConditionsFormat'] for d in keys.definitions() if d.get('BTTTriggerConditionsFormat')}
        self.assertIn(conditions.PASTE_CONDITION, predicates)
        for predicate in predicates:
            with self.subTest(predicate=predicate[:60]):
                self.assertLessEqual(set(conditions.variables_used(predicate)), allowed)
        self.assertEqual(conditions.variables_used(conditions.PASTE_CONDITION),
                         sorted(allowed))

    def test_the_paste_condition_checks_app_then_variable_then_details(self):
        text = conditions.PASTE_CONDITION
        order = [text.index(part) for part in ('BTTActiveAppBundleIdentifier', conditions.LINE_CLIPBOARD_VARIABLE,
                                               'focused_element_details')]
        self.assertEqual(order, sorted(order))

    def test_a_misspelled_predicate_key_fails_loudly(self):
        with self.assertRaisesRegex(RuntimeError, 'misspelled_variable'):
            conditions.evaluate('misspelled_variable == "1"', [{'different_variable': '1'}])

    def test_a_missing_variable_inside_an_aggregate_fails_loudly(self):
        with self.assertRaisesRegex(RuntimeError, 'missing_candidate'):
            conditions.evaluate('app IN {missing_candidate}', [{'app': 'com.apple.TextEdit'}])


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
                reads = [call for call in outcome['calls'] if call[0] == 'readFile']
                [source] = [call[1] for call in outcome['calls'] if call[0] == 'runJXA']
                self.assertEqual(reads, [['readFile', path] for path in keys.RUNTIME])
                self.assertEqual(call_options(source, 'runEditingKey'),
                                 {'kind': kind, 'directory': str(keys.DIRECTORY),
                                  'lineClipboardVariable': conditions.LINE_CLIPBOARD_VARIABLE})

    def test_a_refused_copy_is_reported_as_a_notification(self):
        [outcome] = run_entries([self.entry(by_note('copies the line'), '{"ok":false,"error":"fixture refusal"}')])
        self.assertEqual(outcome['calls'][-1], ['callBTT', 'display_notification',
                                                {'title': 'Copy line stopped', '': 'fixture refusal'}])

    def test_paste_uses_a_stable_cmd_v_uuid_and_tagged_caret_condition(self):
        paste = by_note('pastes above the line')
        self.assertEqual(paste['BTTUUID'], 'A9D83EB2-3D4F-5A85-984E-511A2477E7CE')
        self.assertEqual(paste['BTTUUID'], keys.stable_uuid('paste line'))
        self.assertEqual((paste['BTTShortcutKeyCode'], paste['BTTShortcutModifierKeys']), (9, triggers.COMMAND))
        self.assertEqual(paste['BTTTriggerConditionsFormat'], conditions.PASTE_CONDITION)
        self.assertTrue(paste[triggers.PASSTHROUGH])
        [outcome] = run_entries([self.entry(paste, '{"ok":true,"result":"pasted line","lineClipboard":true}')])
        self.assertEqual(outcome['calls'][-1], ['callBTT', 'set_persistent_string_variable',
            {'variable_name': conditions.LINE_CLIPBOARD_VARIABLE, 'to': '1'}])
        self.assertEqual(call_options(outcome['calls'][-2][1], 'runEditingKey'),
            {'kind': 'paste-line', 'directory': str(keys.DIRECTORY),
             'lineClipboardVariable': conditions.LINE_CLIPBOARD_VARIABLE, 'waitForTag': False})

    def test_the_disjoint_pair_is_built_from_the_one_finder_and_paste_line_ids(self):
        self.assertEqual(keys.DISJOINT_TRIGGER_PAIRS, (frozenset({finder.TRIGGER, keys.PASTE_LINE_ID}),))
        self.assertEqual(keys.PASTE_LINE_ID, keys.stable_uuid('paste line'))
        self.assertEqual(by_note('pastes above the line')['BTTUUID'], keys.PASTE_LINE_ID)
        self.assertEqual(triggers.DISJOINT_TRIGGER_PAIRS, keys.DISJOINT_TRIGGER_PAIRS)

    def test_variable_logic_is_an_optional_hook_and_finders_action_has_none(self):
        script = finder.action()['BTTAppleScriptString']
        self.assertNotIn('_variable', script)
        self.assertNotIn('lineClipboard', script)
        plain = triggers.runtime_action('f', 'run', {}, 'T', ('a.js',))['BTTAppleScriptString']
        self.assertEqual(plain, script.replace('pasteInFinder', 'f').replace('runFinderPaste', 'run')
                         .replace('Finder paste stopped', 'T').replace(json.dumps(list(finder.RUNTIME)),
                                                                        json.dumps(['a.js'])))
        hooked = triggers.runtime_action('f', 'run', {}, 'T', ('a.js',),
                                         hook=lambda entry, options: triggers.RuntimeHook('    BEFORE\n', '    AFTER\n'))['BTTAppleScriptString']
        order = [hooked.index(part) for part in ('BEFORE', 'readFile', 'runJXA', 'AFTER', 'display_notification')]
        self.assertEqual(order, sorted(order))

    def variable_writes(self, outcome):
        return [call[2]['to'] for call in outcome['calls']
                if call[0] == 'callBTT' and call[1] == 'set_persistent_string_variable'
                and call[2]['variable_name'] == conditions.LINE_CLIPBOARD_VARIABLE]

    def test_copy_and_cut_mark_the_tag_before_reading_sources_and_end_off_without_one(self):
        on, off = conditions.LINE_CLIPBOARD_ON, conditions.LINE_CLIPBOARD_OFF
        for note in ('cuts the line', 'copies the line'):
            definition = by_note(note)
            for jxa, final in (('{"ok":true,"result":"x","lineClipboard":true}', on),
                               ('{"ok":true,"result":"x","lineClipboard":false}', off),
                               ('{"ok":true,"result":"x"}', off),
                               ('{"ok":true,"result":"x","lineClipboard":"true"}', off),
                               ('{"ok":false,"error":"no tag"}', off),
                               ('{"NSLocalizedDescription":"Error: boom"}', off),
                               ('not json', off), ('null', off)):
                with self.subTest(note=note, jxa=jxa):
                    [outcome] = run_entries([self.entry(definition, jxa)])
                    self.assertEqual(outcome['calls'][0][:2], ['callBTT', 'set_persistent_string_variable'])
                    self.assertEqual(outcome['calls'][0][2],
                                     {'variable_name': keys.LINE_COPY_STARTED_VARIABLE, 'to': '100000'})
                    self.assertEqual(outcome['calls'][1][2],
                                     {'variable_name': conditions.LINE_CLIPBOARD_VARIABLE, 'to': on})
                    self.assertEqual(self.variable_writes(outcome), [on, final])
                    self.assertGreater([c[0] for c in outcome['calls']].index('readFile'), 0)

    def test_a_crash_in_the_transport_ends_off_and_notifies(self):
        for note, title, prior in (('cuts the line', 'Cut line stopped', [conditions.LINE_CLIPBOARD_ON]),
                                   ('pastes above the line', 'Paste line stopped', [])):
            for flag in ('failRead', 'failJxa'):
                entry = self.entry(by_note(note), '{"ok":true,"result":"x","lineClipboard":true}')
                entry[flag] = True
                with self.subTest(note=note, flag=flag):
                    [outcome] = run_entries([entry])
                    self.assertEqual(self.variable_writes(outcome), prior + [conditions.LINE_CLIPBOARD_OFF])
                    self.assertEqual(outcome['calls'][-1][:2], ['callBTT', 'display_notification'])
                    self.assertEqual(outcome['calls'][-1][2]['title'], title)
                    self.assertIn('fixture', outcome['result'])

    def test_paste_waits_only_with_a_recent_copy_start_read_by_the_action(self):
        for value, wait in ((None, False), ('', False), ('bad', False), ('0', False), ('98500', False),
                            ('98501', True), ('100000', True), ('100001', False), ('1', False)):
            with self.subTest(value=value):
                entry = self.entry(by_note('pastes above the line'), '{"ok":true,"result":"pasted normally"}')
                entry['variable'] = value
                [outcome] = run_entries([entry])
                self.assertEqual(outcome['calls'][0], ['callBTT', 'get_string_variable',
                                 {'variable_name': keys.LINE_COPY_STARTED_VARIABLE}])
                [source] = [call[1] for call in outcome['calls'] if call[0] == 'runJXA']
                self.assertIs(call_options(source, 'runEditingKey')['waitForTag'], wait)

    def test_a_rejected_timestamp_read_pastes_without_waiting_and_disarms(self):
        entry = self.entry(by_note('pastes above the line'),
                           '{"ok":true,"result":"pasted normally","lineClipboard":false}')
        entry['failVariableRead'] = True
        [outcome] = run_entries([entry])
        [source] = [call[1] for call in outcome['calls'] if call[0] == 'runJXA']
        self.assertIs(call_options(source, 'runEditingKey')['waitForTag'], False)
        self.assertEqual(outcome['result'], 'pasted normally')
        self.assertEqual(self.variable_writes(outcome), [conditions.LINE_CLIPBOARD_OFF])

    def test_trigger_layer_and_shared_ids_do_not_import_their_installers(self):
        subprocess.run([sys.executable, '-c',
            'import trigger_install, editing_key_ids, sys; '
            'trigger_install.active_triggers = lambda *args: []; '
            'trigger_install.conflicts(None, []); '
            'assert "install_text_editing_keys" not in sys.modules; '
            'assert "install_finder_paste" not in sys.modules'], check=True)

    def test_paste_does_not_mark_before_running_and_follows_the_reported_tag(self):
        for jxa, final in (('{"ok":true,"result":"x","lineClipboard":true}', '1'), ('{"ok":true,"result":"x"}', '0'),
                           ('{"ok":false,"error":"e"}', '0'),
                           ('{"ok":false,"error":"e","lineClipboard":true}', '0'), ('null', '0'), ('bad', '0')):
            [outcome] = run_entries([self.entry(by_note('pastes above the line'), jxa)])
            self.assertEqual(self.variable_writes(outcome), [final])

    def test_wrap_and_finder_transport_never_touch_variables(self):
        [outcome] = run_entries([self.entry(by_note('in ('), '{"ok":true,"result":"wrapped"}')])
        self.assertEqual(self.variable_writes(outcome), [])
        script = finder.action()['BTTAppleScriptString']
        self.assertNotIn('callBTT("set_persistent', script)
        self.assertNotIn('lineClipboard', script)

    def test_a_raced_native_clipboard_clears_the_custom_condition_variable(self):
        [outcome] = run_entries([self.entry(by_note('pastes above the line'),
            '{"ok":true,"result":"pasted normally","lineClipboard":false}')])
        self.assertEqual(outcome['calls'][-1], ['callBTT', 'set_persistent_string_variable',
            {'variable_name': conditions.LINE_CLIPBOARD_VARIABLE, 'to': '0'}])

    def test_a_completed_cut_with_a_tag_warning_still_returns_success_and_notifies(self):
        message = 'Line cut, but Cmd-V will paste it inline.'
        [outcome] = run_entries([self.entry(by_note('cuts the line'),
            json.dumps({'ok': True, 'result': message, 'warning': message, 'lineClipboard': False}))])
        self.assertEqual(outcome['result'], message)
        self.assertEqual(outcome['calls'][-1], ['callBTT', 'display_notification',
            {'title': 'Line cut with a warning', '': message}])

    def test_app_switches_prepare_text_accessibility_outside_excluded_apps(self):
        [outcome] = run_entries([self.entry(by_note('on app switch'), '{"ok":true,"result":"enabled"}')])
        self.assertEqual(outcome['result'], 'enabled')
        self.assertEqual(call_options(outcome['calls'][-1][1], 'runAppSwitch'),
                         {'excluded': list(conditions.EXCLUDED_APPS),
                          'lineClipboardVariable': conditions.LINE_CLIPBOARD_VARIABLE})

    def test_app_switch_repairs_an_unbound_variable_before_running_jxa(self):
        definition = by_note('on app switch')
        for variable in ('', None):
            entry = self.entry(definition, '{"ok":true,"result":"enabled"}')
            entry['variable'] = variable
            [outcome] = run_entries([entry])
            self.assertEqual(outcome['calls'][:2], [
                ['callBTT', 'get_string_variable', {'variable_name': conditions.LINE_CLIPBOARD_VARIABLE}],
                ['callBTT', 'set_persistent_string_variable',
                 {'variable_name': conditions.LINE_CLIPBOARD_VARIABLE, 'to': conditions.LINE_CLIPBOARD_OFF}]])

    def test_app_switch_preserves_an_existing_bound_variable(self):
        for variable in (conditions.LINE_CLIPBOARD_OFF, conditions.LINE_CLIPBOARD_ON):
            entry = self.entry(by_note('on app switch'), '{"ok":true,"result":"excluded"}')
            entry['variable'] = variable
            [outcome] = run_entries([entry])
            self.assertEqual([call for call in outcome['calls'] if call[0] == 'callBTT'], [
                ['callBTT', 'get_string_variable', {'variable_name': conditions.LINE_CLIPBOARD_VARIABLE}]])


class InstallFlowTests(unittest.TestCase):
    def setUp(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        self.plan = keys.definitions()
        self.fake = FakeBTT()
        self.calls, self.variable = [], None
        for module, target, value in [(btt_socket, 'call', self.socket), (triggers, 'saved_errors', lambda *args: []),
                                      (keys, 'BACKUPS', directory),
                                      (triggers, 'backup_configuration', lambda *args, **kw: directory / 'backup.json'),
                                      (conditions, 'canonical', lambda predicate: predicate)]:
            patcher = patch.object(module, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('builtins.print')
        patcher.start()
        self.addCleanup(patcher.stop)

    def socket(self, command, **parameters):
        self.calls.append((command, parameters))
        if command == 'set_persistent_string_variable':
            self.variable = parameters['to']
            return ''
        if command == 'get_string_variable':
            return self.variable or ''
        return self.fake(command, **parameters)

    def test_persistent_variable_is_initialized_and_read_back_before_any_trigger_enables(self):
        keys.install('db', self.plan)
        self.assertEqual(self.calls[:2], [
            ('set_persistent_string_variable', {'variable_name': conditions.LINE_CLIPBOARD_VARIABLE,
                                                'to': conditions.LINE_CLIPBOARD_OFF}),
            ('get_string_variable', {'variable_name': conditions.LINE_CLIPBOARD_VARIABLE})])
        enabled = [i for i, (command, args) in enumerate(self.calls)
                   if command == 'update_trigger' and json.loads(args['json'])['BTTEnabled'] == 1]
        self.assertTrue(enabled)
        self.assertGreater(min(enabled), 1)

    def test_an_unconfirmed_variable_initialization_enables_nothing(self):
        with patch.object(btt_socket, 'call', return_value=''):
            with self.assertRaisesRegex(RuntimeError, 'did not initialize'):
                keys.install('db', self.plan)
        self.assertEqual(self.fake.writes, [])

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
        self.assertEqual(triggers.conflicts(self.database, self.plan), ['other-90', 'other-93', 'other-94'])

    def test_only_the_documented_disjoint_finder_uuid_is_allowed(self):
        import install_finder_paste as finder
        self.update('''INSERT INTO ZBTTBASEENTITY (Z_PK,ZUNIQUEIDENTIFIER,ZBELONGSTOPRESET2,ZGESTURETYPE,
            ZKEYCODE,ZMODIFIERKEYS,ZISENABLED,ZENABLEDNEW) VALUES (90,?,1,0,9,?,1,1)''',
            finder.TRIGGER, triggers.COMMAND)
        self.assertEqual(triggers.conflicts(self.database, self.plan), [])
        self.assertEqual(triggers.conflicts(self.database, finder.definitions()), [])
        self.update('UPDATE ZBTTBASEENTITY SET ZUNIQUEIDENTIFIER=? WHERE Z_PK=90', 'unreviewed-cmd-v')
        self.assertEqual(triggers.conflicts(self.database, self.plan), ['unreviewed-cmd-v'])
        self.assertEqual(triggers.conflicts(self.database, finder.definitions()), ['unreviewed-cmd-v'])

    def test_finder_and_line_paste_conditions_never_overlap_in_the_truth_tables(self):
        import finder_paste_condition as finder
        variables = [{'BTTActiveAppBundleIdentifier': case.app, 'focused_element_details': case.details,
                      conditions.LINE_CLIPBOARD_VARIABLE: '1'}
                     for case in (*conditions.CASES, *finder.CASES)]
        overlap = '(' + conditions.PASTE_CONDITION + ') AND (' + finder.CONDITION + ')'
        self.assertFalse(any(conditions.evaluate(overlap, variables)['results']))


if __name__ == '__main__':
    unittest.main()
