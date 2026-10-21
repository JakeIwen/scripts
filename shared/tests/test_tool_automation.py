import copy
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tool_automation


class DeclarationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.environment = patch.dict(
            os.environ, {'JACOBR_AUTOMATIONS_DIR': str(self.directory)})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    @staticmethod
    def declaration(tool='fixture'):
        return {
            'tool': tool,
            'title': 'Fixture automation',
            'description': 'Exercises the shared declaration contract.',
            'service': 'com.jacobr.fixture',
            'settings': 'fixture-settings',
            'status': {'argv': ['/bin/echo', 'status'], 'timeout': 8},
            'actions': [
                {'name': 'run-now', 'label': 'Run now', 'description': 'Run it now.',
                 'argv': ['/bin/echo', 'run'], 'timeout': 12, 'confirm': True},
            ],
        }

    def write(self, value, tool='fixture'):
        (self.directory / f'{tool}.json').write_text(json.dumps(value), encoding='utf-8')

    def assert_invalid(self, value, message, tool='fixture'):
        self.write(value, tool)
        with self.assertRaisesRegex(tool_automation.AutomationError, message):
            tool_automation.load_declaration(tool)

    def test_load_expands_placeholders_and_page_view_hides_commands(self):
        value = self.declaration()
        value['status']['argv'] = ['{repo}/bin/status', '{home}/state']
        value['actions'][0]['argv'] = ['{home}/bin/action', '{repo}/input']
        self.write(value)

        declaration = tool_automation.load_declaration('fixture')

        root = Path(tool_automation.__file__).resolve().parents[2]
        self.assertEqual(declaration.status.argv,
                         (str(root / 'bin/status'), str(Path.home() / 'state')))
        self.assertEqual(declaration.actions[0].command.argv,
                         (str(Path.home() / 'bin/action'), str(root / 'input')))
        page = declaration.as_dict()
        self.assertEqual(set(page),
                         {'tool', 'title', 'description', 'service', 'settings', 'actions'})
        self.assertEqual(set(page['actions'][0]),
                         {'name', 'label', 'description', 'confirm'})
        self.assertNotIn('argv', repr(page))

    def test_relative_executable_and_timeout_bounds_are_rejected(self):
        value = self.declaration()
        value['status']['argv'][0] = 'echo'
        self.assert_invalid(value, 'absolute executable')
        for timeout in (0, 121, True, 1.5):
            with self.subTest(timeout=timeout):
                value = self.declaration()
                value['actions'][0]['timeout'] = timeout
                self.assert_invalid(value, 'timeout must be an integer')

    def test_unknown_and_missing_fields_are_rejected(self):
        value = self.declaration()
        value['unexpected'] = True
        self.assert_invalid(value, 'fields must be')

        value = self.declaration()
        del value['description']
        self.assert_invalid(value, 'fields must be')

        value = self.declaration()
        value['status']['unexpected'] = True
        self.assert_invalid(value, 'status must be an object')

        value = self.declaration()
        del value['actions'][0]['label']
        self.assert_invalid(value, 'fields must be')

    def test_file_name_actions_service_and_json_are_validated(self):
        value = self.declaration('different')
        self.assert_invalid(value, 'tool must match the file name')

        value = self.declaration()
        value['actions'].append(copy.deepcopy(value['actions'][0]))
        self.assert_invalid(value, 'action names must be unique')

        value = self.declaration()
        value['service'] = 'org.example.fixture'
        self.assert_invalid(value, 'service is not a valid name')

        (self.directory / 'fixture.json').write_text('{broken', encoding='utf-8')
        with self.assertRaises(tool_automation.AutomationError):
            tool_automation.load_declaration('fixture')


class StatusRecordTests(unittest.TestCase):
    @staticmethod
    def record(level='ok', summary='Ready', fields=None):
        return {'level': level, 'summary': summary, 'fields': [] if fields is None else fields}

    def test_all_declared_levels_are_valid_and_unknown_level_is_rejected(self):
        for level in tool_automation.STATUS_LEVELS:
            with self.subTest(level=level):
                self.assertEqual(tool_automation.validate_status(self.record(level))['level'], level)
        with self.assertRaisesRegex(tool_automation.AutomationError, 'level must be one of'):
            tool_automation.validate_status(self.record('fatal'))

    def test_field_needs_value_or_time_and_time_is_finite_non_negative(self):
        with self.assertRaisesRegex(tool_automation.AutomationError, 'needs a value'):
            tool_automation.validate_status(self.record(fields=[{'label': 'Last run'}]))
        for value in (-1, math.inf, -math.inf, math.nan, True, '1'):
            with self.subTest(time=value):
                field = {'label': 'Last run', 'value': 'present', 'time': value}
                with self.assertRaisesRegex(tool_automation.AutomationError, 'epoch seconds'):
                    tool_automation.validate_status(self.record(fields=[field]))

    def test_length_and_field_count_limits_are_enforced(self):
        invalid = (
            self.record(summary='s' * (tool_automation.MAX_SUMMARY_CHARS + 1)),
            self.record(fields=[{'label': 'l' * (tool_automation.MAX_LABEL_CHARS + 1),
                                 'value': 'present'}]),
            self.record(fields=[{'label': 'Value',
                                 'value': 'v' * (tool_automation.MAX_VALUE_CHARS + 1)}]),
            self.record(fields=[{'label': str(index), 'value': 'present'}
                                for index in range(tool_automation.MAX_FIELDS + 1)]),
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(tool_automation.AutomationError):
                    tool_automation.validate_status(value)

    def test_canonical_output_fills_optional_field_keys_with_null(self):
        value = self.record('idle', 'Waiting', [
            {'label': 'Last run', 'time': 0},
            {'label': 'Result', 'value': 'No work', 'level': 'unknown'},
        ])
        self.assertEqual(tool_automation.validate_status(value), {
            'level': 'idle',
            'summary': 'Waiting',
            'fields': [
                {'label': 'Last run', 'value': None, 'time': 0, 'level': None},
                {'label': 'Result', 'value': 'No work', 'time': None, 'level': 'unknown'},
            ],
        })
        self.assertEqual(
            tool_automation.status_record(
                'ok', 'Ready', [tool_automation.status_field('Runs', value='2')]),
            {'level': 'ok', 'summary': 'Ready',
             'fields': [{'label': 'Runs', 'value': '2', 'time': None, 'level': None}]},
        )


if __name__ == '__main__':
    unittest.main()
