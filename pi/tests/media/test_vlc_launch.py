"""VLC launch argv and display wake, with every command faked."""

import subprocess
import unittest
from types import SimpleNamespace

from pi.apps.video_library.config import (
    QT_QPA_PLATFORM, RUNTIME_DIR, SYSTEMD_RUN, WAYLAND_DISPLAY, WLOPM, XSET,
)
from pi.apps.video_library.players.vlc_player import VlcController
from pi.tests.media.test_movie_audio_check import Mpris


class VlcLaunchTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.mpris = Mpris(self.events)
        self.calls = []
        self.failing = {}
        self.controller = VlcController(
            dbus_module=self.mpris.module, run=self.command,
            audio_check=SimpleNamespace(schedule=lambda: None, cancel=lambda: None),
            sleep=lambda _seconds: None,
        )

    def command(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        failure = self.failing.get(argv[0])
        if isinstance(failure, BaseException):
            raise failure
        if argv[0] == SYSTEMD_RUN:
            self.mpris.available = True
            self.mpris.state = 'Playing'
            self.mpris.path = argv[-1]
        return SimpleNamespace(returncode=1 if failure else 0, stdout='', stderr='')

    def programs(self):
        return [argv[0] for argv, _kwargs in self.calls]

    def launch_argv(self):
        return next(argv for argv, _kwargs in self.calls if argv[0] == SYSTEMD_RUN)

    def test_launch_hardware_decodes_and_renders_through_wayland(self):
        self.controller.launch(['/media/movie.mp4'])
        argv = self.launch_argv()
        self.assertIn('--avcodec-hw=any', argv)
        self.assertIn(f'--setenv=WAYLAND_DISPLAY={WAYLAND_DISPLAY}', argv)
        self.assertIn(f'--setenv=QT_QPA_PLATFORM={QT_QPA_PLATFORM}', argv)
        self.assertTrue(any(arg.startswith('--setenv=DISPLAY=') for arg in argv))
        self.assertEqual((WAYLAND_DISPLAY, QT_QPA_PLATFORM), ('wayland-0', 'wayland'))

    def test_wake_uses_wlopm_with_the_session_environment(self):
        self.controller.launch(['/media/movie.mp4'])
        self.assertNotIn(XSET, self.programs())
        argv, kwargs = next(call for call in self.calls if call[0][0] == WLOPM)
        self.assertEqual(argv, [WLOPM, '--on', '*'])
        self.assertEqual(kwargs['env']['WAYLAND_DISPLAY'], WAYLAND_DISPLAY)
        self.assertEqual(kwargs['env']['XDG_RUNTIME_DIR'], RUNTIME_DIR)

    def test_failed_wlopm_falls_back_to_xset(self):
        self.failing[WLOPM] = True
        self.controller.launch(['/media/movie.mp4'])
        programs = self.programs()
        self.assertLess(programs.index(WLOPM), programs.index(XSET))
        self.assertLess(programs.index(XSET), programs.index(SYSTEMD_RUN))

    def test_missing_or_hung_wake_tools_never_block_playback(self):
        self.failing[WLOPM] = FileNotFoundError(WLOPM)
        self.failing[XSET] = subprocess.TimeoutExpired(XSET, 5)
        snapshot = self.controller.launch(['/media/movie.mp4'])
        self.assertEqual(snapshot['state'], 'PLAYING')
        self.assertIn(SYSTEMD_RUN, self.programs())


if __name__ == '__main__':
    unittest.main()
