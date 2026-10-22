"""Movie topology tasks against a stateful SoCo boundary, never real devices."""

import importlib.util
import io
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / 'python' / 'sonos_tasks.py'
LEFT = 'RINCON_7828CA20F21A01400'
RIGHT = 'RINCON_7828CA20F1DA01400'


class Group:
    def __init__(self, coordinator, members):
        self.coordinator = coordinator
        self.members = set(members)

    @property
    def mute(self):
        return all(member.mute for member in self.members)

    @mute.setter
    def mute(self, value):
        for member in self.members:
            member.mute = value


class Speaker:
    def __init__(self, uid, name, events, *, visible=True):
        self.uid = uid
        self.player_name = name
        self.is_visible = visible
        self.events = events
        self._volume = 25
        self._mute = name == 'vonMid'
        self.is_playing_tv = True
        self.state = 'PLAYING'
        self.zone_group_state = mock.Mock()
        self.group = Group(self, [self])
        self.household = []

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, value):
        self.events.append(('volume', self.uid, value))
        self._volume = value

    @property
    def mute(self):
        return self._mute

    @mute.setter
    def mute(self, value):
        self.events.append(('mute', self.uid, value))
        self._mute = value

    def get_current_transport_info(self):
        return {'current_transport_state': self.state}

    def stop(self):
        self.events.append(('stop', self.uid))
        self.state = 'STOPPED'

    def play(self):
        self.events.append(('play', self.uid))
        self.state = 'PLAYING'

    def unjoin(self):
        self.events.append(('unjoin', self.uid))
        self.group.members.discard(self)
        self.group = Group(self, [self])

    def switch_to_tv(self):
        self.events.append(('tv', self.uid))
        self.is_playing_tv = True

    def partymode(self):
        self.events.append(('party', self.uid))
        group = Group(self, self.household)
        for member in self.household:
            member.group = group


class SonosMovieTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.mid = Speaker('MID', 'vonMid', self.events)
        self.rear = Speaker(LEFT, 'vonRear', self.events)
        self.partner = Speaker(RIGHT, 'vonRear', self.events, visible=False)
        self.devices = {self.mid, self.rear, self.partner}
        group = Group(self.mid, self.devices)
        for device in self.devices:
            device.group = group
            device.household = self.devices
        discovery = types.ModuleType('soco.discovery')
        discovery.discover = mock.Mock(return_value=self.devices)
        discovery.by_name = lambda name: next(
            (device for device in self.devices if device.player_name == name), None
        )
        discovery.any_soco = lambda: self.mid
        exceptions = types.ModuleType('soco.exceptions')
        exceptions.SoCoUPnPException = RuntimeError
        music = types.ModuleType('soco.music_library')
        music.MusicLibrary = mock.Mock()
        modules = {
            'soco': types.ModuleType('soco'), 'soco.discovery': discovery,
            'soco.exceptions': exceptions, 'soco.music_library': music,
        }
        with mock.patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location('movie_tasks_test', SOURCE)
            self.tasks = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.tasks)
        self.discover = discovery.discover

    def ensure(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = self.tasks.ensure_rear_movie()
        self.assertIs(result, self.rear)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        return output.getvalue().strip()

    def test_healthy_check_reads_once_without_mutations_or_discovery_waits(self):
        self.assertEqual(self.ensure(), 'rear movie: ok')
        self.assertEqual(self.events, [])
        self.discover.assert_called_once_with(5, True)
        self.mid.zone_group_state.clear_cache.assert_called_once()

    def test_each_broken_invariant_repairs_and_retains_rear_volume(self):
        cases = (
            ('source', 'vonMid is not on TV/optical'),
            ('transport', 'vonMid is not playing'),
            ('group', 'rear master is not grouped with vonMid'),
            ('mid_mute', 'vonMid is unmuted'),
            ('rear_mute', 'rear master is muted'),
        )
        for broken, reason in cases:
            for volume in (0, 64):
                with self.subTest(broken=broken, volume=volume):
                    self.rear._volume = volume
                    if broken == 'source':
                        self.mid.is_playing_tv = False
                    elif broken == 'transport':
                        self.mid.state = 'PAUSED_PLAYBACK'
                    elif broken == 'group':
                        self.rear.group = Group(self.rear, [self.rear, self.partner])
                    elif broken == 'mid_mute':
                        self.mid._mute = False
                    else:
                        self.rear._mute = True
                    self.events.clear()
                    self.assertEqual(self.ensure(), f'rear movie: repaired ({reason})')
                    self.assertTrue(self.mid.is_playing_tv)
                    self.assertEqual(self.mid.state, 'PLAYING')
                    self.assertIs(self.rear.group.coordinator, self.mid)
                    self.assertTrue(self.mid.mute)
                    self.assertFalse(self.rear.mute)
                    self.assertEqual(self.rear.volume, volume)
                    self.events.clear()
                    self.assertEqual(self.ensure(), 'rear movie: ok')
                    self.assertEqual(self.events, [])

    def test_all_problems_are_reported_on_one_line(self):
        self.mid.is_playing_tv = False
        self.mid.state = 'STOPPED'
        self.mid._mute = False
        self.rear._mute = True
        output = self.ensure()
        for text in ('not on TV/optical', 'not playing', 'vonMid is unmuted', 'rear master is muted'):
            self.assertIn(text, output)

    def test_inverted_pair_uses_visible_physical_master_not_name(self):
        self.rear.is_visible = False
        self.partner.is_visible = True
        self.rear, self.partner = self.partner, self.rear
        self.rear._volume = 39
        self.mid._mute = False
        self.assertIn('repaired', self.ensure())
        self.assertEqual(self.rear.volume, 39)
        self.assertFalse(self.rear.mute)

    def test_missing_or_split_pair_fails_without_topology_writes_or_retries(self):
        for missing in (self.mid, self.partner):
            with self.subTest(missing=missing.uid):
                self.tasks.all_devices = self.devices - {missing}
                with self.assertRaises(RuntimeError):
                    self.tasks.ensure_rear_movie()
        self.tasks.all_devices = self.devices
        self.partner.is_visible = True
        with self.assertRaisesRegex(RuntimeError, 'one stereo pair'):
            self.tasks.ensure_rear_movie()
        self.assertEqual(self.events, [])
        self.discover.assert_called_once()

    def test_unreadable_state_is_not_evidence_of_a_broken_group(self):
        self.mid.get_current_transport_info = mock.Mock(side_effect=OSError('offline'))
        with self.assertRaises(OSError):
            self.tasks.ensure_rear_movie()
        self.assertEqual(self.events, [])

    def test_manual_movie_and_resume_keep_their_volume_contracts(self):
        for task, current, expected in (
            ('rear_movie', 25, 47), ('rear_movie_resume', 0, 0),
            ('rear_movie_resume', 25, 25),
        ):
            with self.subTest(task=task, current=current):
                self.rear._volume = current
                with redirect_stdout(io.StringIO()):
                    result = getattr(self.tasks, task)()
                self.assertIs(result, self.rear)
                self.assertEqual(self.rear.volume, expected)
                self.assertTrue(self.mid.mute)
                self.assertFalse(self.rear.mute)


if __name__ == '__main__':
    unittest.main()
