"""CLI contracts with in-memory speakers; sockets and discovery are forbidden."""

import io
import json
import sys
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from shared.tests import test_sonos_movie as fixtures
from sonos_cli import COMMANDS, main


SAMPLES = {
    'brown_noise': [], 'pink_noise': [], 'discover_weekly': [],
    'random_album': [], 'random_radio': [], 'group_vol_set': ['42'],
    'group_vol_up': ['8'], 'group_vol_down': ['8'], 'vol_up': ['8'],
    'vol_down': ['8'], 'back_15': [], 'back_30': [], 'fwd_15': [],
    'fwd_30': [], 'next_track': [], 'prev_track': [], 'rear_movie': ['47'],
    'rear_movie_resume': [], 'ensure_rear_movie': [], 'rear_normal': [],
    'rear_inverted': [], 'play_soundbyte': ['chime', 'vonRear'],
    'make_stereo_pair': ['left', 'right'], 'vol_eql_all': ['50'],
    'adjust_volume': ['all', 'set', '35'], 'play': ['vonRear'],
    'pause': [], 'stop': [], 'mute': [], 'start_noise': ['Pink Noise', '35'],
    'play_from_faves': ['Album', 'false'],
    'audio_source': ['vonRear', 'optical', '50'], 'scrub': ['-15'],
    'add_to_main_group': ['vonRear'], 'remove_from_group': ['vonRear'],
    'partymode': ['35'], 'unjoin_all': [], 'direct': ['vonRear'],
    'crossfade_on': [], 'crossfade_off': [], 'num_devices': [],
}
VOLUME_PREFIXES = (
    ['group_vol_set'], ['group_vol_up'], ['group_vol_down'], ['vol_up'],
    ['vol_down'], ['rear_movie'], ['vol_eql_all'],
    ['adjust_volume', 'all', 'set'], ['start_noise', 'Pink Noise'],
    ['audio_source', 'vonRear', 'optical'], ['partymode'],
)


class SonosCliTests(unittest.TestCase):
    def setUp(self):
        self.network = mock.patch('socket.socket', side_effect=AssertionError('network forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.fixture = fixtures.SonosMovieTests()
        self.fixture.setUp()
        self.fixture.discover.side_effect = AssertionError('discovery forbidden')
        self.modules = mock.patch.dict(sys.modules, {'sonos_tasks': self.fixture.tasks})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def invoke(self, arguments):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            try:
                code = main(arguments)
            except SystemExit as error:
                code = error.code
        return code, output.getvalue(), errors.getvalue()

    def check(self, arguments):
        code, output, errors = self.invoke(['--check', *arguments])
        self.assertEqual(code, 0, errors)
        self.assertEqual(errors, '')
        self.fixture.discover.assert_not_called()
        self.assertEqual(self.fixture.events, [])
        return json.loads(output)['arguments']

    def test_every_public_command_resolves_and_checks_without_device_access(self):
        self.assertEqual(set(SAMPLES), set(COMMANDS))
        for task, arguments in SAMPLES.items():
            with self.subTest(task=task):
                self.check([task, *arguments])

    def test_volume_and_increment_arguments_are_integers_in_range(self):
        for prefix in VOLUME_PREFIXES:
            for value in ('0', '42', '100'):
                with self.subTest(prefix=prefix, value=value):
                    parsed = self.check([*prefix, value])
                    self.assertEqual(parsed[-1], int(value))
                    self.assertIs(type(parsed[-1]), int)

    def test_boolean_and_signed_seek_conversion(self):
        for value, expected in (('true', True), ('False', False), ('1', True), ('0', False)):
            parsed = self.check(['play_from_faves', 'Album', value])
            self.assertIs(parsed[-1], expected)
        for value in ('-30', '0', '+90'):
            self.assertEqual(self.check(['scrub', value]), [int(value)])

    def test_invalid_values_fail_before_importing_tasks_or_touching_speakers(self):
        cases = [prefix + [bad] for prefix in VOLUME_PREFIXES
                 for bad in ('-1', '101', 'loud', '1.5')]
        cases.extend([
            ['audio_source', 'vonRear', 'garbage'],
            ['adjust_volume', 'all', 'sideways'],
            ['play_from_faves', 'Album', 'maybe'], ['scrub', 'soon'],
            ['play', ''], ['start_noise', '  '],
            ['pink', 'noise'], ['brown', 'noise'], ['standby_grouped'],
        ])
        for arguments in cases:
            with self.subTest(arguments=arguments):
                for mode in ([], ['--check']):
                    with mock.patch('sonos_cli.importlib.import_module') as load:
                        code, _, errors = self.invoke(mode + arguments)
                    self.assertEqual(code, 2, errors)
                    load.assert_not_called()
        self.assertEqual(self.fixture.events, [])
        self.fixture.discover.assert_not_called()

    def test_internal_device_parameters_are_not_exposed_as_text_arguments(self):
        cases = [[name, 'vonRear'] for name in
                 ('pause', 'stop', 'mute', 'unjoin_all', 'crossfade_on', 'crossfade_off')]
        cases.append(['partymode', '35', 'vonRear'])
        for arguments in cases:
            with self.subTest(arguments=arguments):
                code, _, errors = self.invoke(['--check', *arguments])
                self.assertEqual(code, 2)
                self.assertIn('too many positional arguments', errors)
        self.fixture.discover.assert_not_called()

    def test_missing_required_arguments_are_rejected_without_task_import(self):
        for task in ('group_vol_set', 'play_soundbyte', 'make_stereo_pair',
                     'adjust_volume', 'start_noise', 'play_from_faves',
                     'audio_source', 'add_to_main_group', 'remove_from_group', 'direct'):
            with self.subTest(task=task):
                with mock.patch('sonos_cli.importlib.import_module') as load:
                    code, _, errors = self.invoke([task])
                self.assertEqual(code, 2)
                self.assertIn('missing required arguments', errors)
                load.assert_not_called()

    def test_optional_defaults_remain_valid_for_each_task_signature(self):
        for task, contract in COMMANDS.items():
            if contract.optional:
                with self.subTest(task=task):
                    self.check([task, *SAMPLES[task][:len(contract.required)]])

    def test_false_favorite_grouping_leaves_volumes_and_topology_alone(self):
        tasks = self.fixture.tasks
        tasks.all_devices = self.fixture.devices
        favorite = types.SimpleNamespace(title='Album', reference='offline album')
        tasks.MusicLibrary = lambda device: types.SimpleNamespace(
            get_sonos_favorites=lambda: [favorite])
        original_groups = {device: device.group for device in self.fixture.devices}
        code, _, errors = self.invoke(['play_from_faves', 'Album', 'false'])
        self.assertEqual(code, 0, errors)
        self.assertEqual(self.fixture.mid.queue, ['offline album'])
        self.assertEqual([event[0] for event in self.fixture.events],
                         ['clear_queue', 'queue', 'play_from_queue'])
        for device, group in original_groups.items():
            self.assertIs(device.group, group)
            self.assertEqual(device.volume, 25)

    def test_volume_command_applies_converted_value_to_fixture_speakers(self):
        self.fixture.tasks.all_devices = self.fixture.devices
        code, _, errors = self.invoke(['vol_eql_all', '42'])
        self.assertEqual(code, 0, errors)
        for device in self.fixture.devices:
            self.assertEqual(device.volume, 42)
            self.assertIs(type(device.volume), int)
        self.assertEqual({event[0] for event in self.fixture.events}, {'volume'})


if __name__ == '__main__':
    unittest.main()
