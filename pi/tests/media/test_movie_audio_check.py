"""Delayed Sonos orchestration through VLC and HTTP, with no hardware access."""

import subprocess
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from pi.apps.video_library import routes
from pi.apps.video_library.config import (
    DBUS_PROPERTIES, MPRIS_PLAYER, MPRIS_ROOT, SNS, SYSTEMD_RUN, WLOPM,
    SONOS_CHECK_DELAY, SONOS_CHECK_TIMEOUT,
)
from pi.apps.video_library.players.sonos_check import RearMovieCheck
from pi.apps.video_library.players.vlc_player import VlcController
from pi.tests.media.test_video_library_server import (
    FakeClock, FakeSonosVolume, MediaFixture, add_sample_library,
)
from pi.apps.video_library.legacy_progress import ProgressStore
from pi.apps.video_library.service import VideoService


class Timer:
    def __init__(self, delay, callback, args):
        self.delay, self.callback, self.args = delay, callback, args
        self.cancelled = False
        self.started = False
        self.daemon = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        # Include the race where cancel() arrives after the callback begins.
        self.callback(*self.args)


class Mpris:
    def __init__(self, events):
        self.events = events
        self.available = True
        self.state = 'Paused'
        self.path = '/media/movie.mkv'
        self.volume = 1.0
        self.props = SimpleNamespace(Get=self.get, GetAll=self.get_all, Set=self.set)
        self.player = SimpleNamespace(**{
            name: lambda name=name: self.action(name)
            for name in ('Play', 'PlayPause', 'Pause', 'Stop', 'Next', 'Previous')
        })
        self.root = SimpleNamespace(Quit=self.quit)
        interfaces = {
            DBUS_PROPERTIES: self.props, MPRIS_PLAYER: self.player,
            MPRIS_ROOT: self.root,
        }
        self.module = SimpleNamespace(
            SessionBus=lambda: SimpleNamespace(get_object=self.get_object),
            Interface=lambda obj, name: interfaces[name], Double=float,
        )

    def get_object(self, *args):
        if not self.available:
            raise RuntimeError('offline')
        return self

    def get(self, interface, name):
        return self.get_all(interface)[name]

    def get_all(self, interface):
        if interface == MPRIS_ROOT:
            return {}
        return {
            'PlaybackStatus': self.state, 'Volume': self.volume,
            'Metadata': {'xesam:url': f'file://{self.path}', 'mpris:trackid': '/track/1'},
        }

    def set(self, interface, name, value):
        if name == 'Volume':
            self.volume = value

    def action(self, name):
        self.events.append(name)
        self.state = {
            'Play': 'Playing', 'Pause': 'Paused', 'Stop': 'Stopped',
            'PlayPause': 'Paused' if self.state == 'Playing' else 'Playing',
        }.get(name, self.state)

    def quit(self):
        self.available = False


class MovieAudioCheckTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.clock = FakeClock()
        self.timers = []
        self.mpris = Mpris(self.events)
        self.check_result = SimpleNamespace(returncode=0, stdout='rear movie: ok\n', stderr='')
        self.check_effect = None
        self.run = mock.Mock(side_effect=self.command)
        self.check = RearMovieCheck(
            is_playing=lambda: self.mpris.available and self.mpris.state == 'Playing',
            run=self.run, timer=self.timer, clock=self.clock,
        )
        self.controller = VlcController(
            dbus_module=self.mpris.module, run=self.run, audio_check=self.check,
        )

    def timer(self, *args, **kwargs):
        timer = Timer(*args, **kwargs)
        self.timers.append(timer)
        return timer

    def command(self, argv, **kwargs):
        if argv[-1] == 'ensure_rear_movie':
            self.events.append('ensure')
            if self.check_effect:
                return self.check_effect()
            return self.check_result
        if argv[0] == SYSTEMD_RUN:
            self.events.append('launch')
            self.mpris.available = True
            self.mpris.state = 'Playing'
            self.mpris.path = argv[-1]
        elif argv[0] == WLOPM:
            self.events.append('wake')
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    def test_resume_plays_before_check_and_uses_bounded_command(self):
        self.controller.action('toggle')
        self.assertEqual(self.events, ['PlayPause', 'wake'])
        self.assertEqual(len(self.timers), 1)
        timer = self.timers[0]
        self.assertEqual(timer.delay, 1.5)
        self.assertTrue(timer.started and timer.daemon)
        timer.fire()
        self.assertEqual(self.events, ['PlayPause', 'wake', 'ensure'])
        self.assertEqual(self.run.call_args.args[0], ['/bin/bash', SNS, 'ensure_rear_movie'])
        self.assertEqual(self.run.call_args.kwargs['timeout'], SONOS_CHECK_TIMEOUT)

    def test_new_launch_starts_vlc_before_scheduling_check(self):
        snapshot = self.controller.launch(['/media/movie.mp4'])
        self.assertEqual(snapshot['state'], 'PLAYING')
        self.assertEqual(self.events, ['wake', 'launch'])
        self.assertEqual(self.timers[0].delay, SONOS_CHECK_DELAY)
        self.timers[0].fire()
        self.assertEqual(self.events[-1], 'ensure')

    def test_pausing_stopping_and_redundant_play_never_repair(self):
        for action in ('play', 'pause', 'toggle', 'stop'):
            with self.subTest(action=action):
                self.mpris.state = 'Playing'
                self.controller.action(action)
                self.assertEqual(self.timers, [])
        self.controller.action('play')
        pending = self.timers[-1]
        self.controller.action('pause')
        self.assertTrue(pending.cancelled)
        pending.fire()
        self.assertNotIn('ensure', self.events)

    def test_repeated_resume_triggers_coalesce_to_latest_delay(self):
        self.controller.action('play')
        stale = self.timers[-1]
        self.clock.advance(0.5)
        self.controller.action('pause')
        self.controller.action('play')
        self.assertTrue(stale.cancelled)
        stale.fire()
        self.assertNotIn('ensure', self.events)
        self.assertEqual(self.timers[-1].delay, SONOS_CHECK_DELAY)
        self.timers[-1].fire()
        self.assertEqual(self.events.count('ensure'), 1)

    def test_external_pause_before_timer_skips_check(self):
        self.controller.action('play')
        self.mpris.state = 'Paused'
        self.timers[-1].fire()
        self.assertNotIn('ensure', self.events)

    def test_timeout_nonzero_and_missing_script_log_without_failing_playback(self):
        for failure in (subprocess.TimeoutExpired('ensure', 45), FileNotFoundError('sns.sh'), None):
            with self.subTest(failure=failure):
                self.mpris.state = 'Paused'
                self.check_effect = mock.Mock(side_effect=failure) if failure else None
                self.check_result.returncode = 9
                self.controller.action('play')
                with self.assertLogs('pi.apps.video_library.players.sonos_check', level='WARNING'):
                    self.timers[-1].fire()
                self.assertEqual(self.mpris.state, 'Playing')
        # A failed check releases its ownership and allows a subsequent check.
        self.check_result.returncode = 0
        self.controller.action('pause')
        self.controller.action('play')
        self.timers[-1].fire()
        self.assertEqual(self.events.count('ensure'), 4)

    def test_timer_start_failure_is_logged_not_a_playback_failure(self):
        self.check.timer = mock.Mock(side_effect=RuntimeError('no threads'))
        with self.assertLogs('pi.apps.video_library.players.sonos_check', level='ERROR'):
            self.controller.action('play')
        self.assertEqual(self.mpris.state, 'Playing')

    def test_slow_check_never_holds_http_transport_or_volume_controls(self):
        fixture = MediaFixture()
        self.addCleanup(fixture.cleanup)
        store = ProgressStore(':memory:')
        self.addCleanup(store.connection.close)
        sonos = FakeSonosVolume(volume=37)
        service = VideoService(
            add_sample_library(fixture), store, self.controller, sonos=sonos,
            legacy_positions=str(fixture.root / 'missing'),
        )
        entered, release = threading.Event(), threading.Event()

        def slow_check():
            entered.set()
            if not release.wait(2):
                raise RuntimeError('test did not release check')
            return self.check_result

        self.check_effect = slow_check
        with mock.patch.object(routes, 'active_service', return_value=service):
            client = routes.app.test_client()
            response = client.post('/api/control', data={'action': 'play'})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(entered.is_set())
            worker = threading.Thread(target=self.timers[-1].fire)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(service.audio_status()['available'])
                volume = client.post('/api/volume', data={'value': '51'})
                self.assertEqual(volume.status_code, 200)
                self.assertEqual(sonos.volume_calls, [51])
                for _ in range(3):
                    client.post('/api/control', data={'action': 'pause'})
                    client.post('/api/control', data={'action': 'play'})
                self.assertEqual(len(self.timers), 1, 'do not overlap running repairs')
            finally:
                release.set()
                worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(len(self.timers), 2, 'one coalesced follow-up after the running check')
        self.timers[-1].fire()
        self.assertEqual(self.events.count('ensure'), 2)


if __name__ == '__main__':
    unittest.main()
