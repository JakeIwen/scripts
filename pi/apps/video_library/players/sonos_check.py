"""Delayed, coalesced Sonos checks, independent of VLC playback success."""

from __future__ import annotations

import logging
import subprocess
import threading
import time

from ..config import SONOS_CHECK_DELAY, SONOS_CHECK_TIMEOUT, SNS


LOG = logging.getLogger(__name__)


class RearMovieCheck:
    def __init__(
        self, *, is_playing, run=subprocess.run, timer=threading.Timer,
        clock=time.monotonic,
    ):
        self.is_playing = is_playing
        self.run = run
        self.timer = timer
        self.clock = clock
        self.lock = threading.RLock()
        self.pending = None
        self.deadline = None
        self.running = False
        self.generation = 0

    def schedule(self):
        """Debounce pending checks; retain one follow-up if a check is running."""
        with self.lock:
            self.cancel()
            self.deadline = self.clock() + SONOS_CHECK_DELAY
            if not self.running:
                self._start_timer()

    def cancel(self):
        with self.lock:
            self.generation += 1
            self.deadline = None
            if self.pending is not None:
                self.pending.cancel()
                self.pending = None

    def _start_timer(self):
        deadline = self.deadline
        try:
            self.pending = self.timer(
                max(0, deadline - self.clock()), self._check, args=(self.generation,)
            )
            self.pending.daemon = True
            self.pending.start()
        except Exception:
            self.pending = None
            self.deadline = None
            LOG.exception('could not schedule rear movie audio check')

    def _check(self, generation):
        with self.lock:
            # A cancelled timer can already be entering its callback.
            if self.generation != generation or self.running:
                return
            self.pending = None
            self.deadline = None
            self.running = True
        try:
            if not self.is_playing():
                return
            result = self.run(
                ['/bin/bash', SNS, 'ensure_rear_movie'],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=SONOS_CHECK_TIMEOUT, check=False,
            )
            if result.returncode:
                LOG.warning(
                    'rear movie audio check exited %s: %s', result.returncode,
                    (result.stderr or result.stdout or '').strip(),
                )
            else:
                LOG.info('%s', result.stdout.strip())
        except Exception:
            LOG.exception('rear movie audio check failed; playback continues')
        finally:
            with self.lock:
                self.running = False
                if self.deadline is not None:
                    self._start_timer()
