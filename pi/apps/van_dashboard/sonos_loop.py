"""A-B repeat for the current Sonos track, run on the Pi.

The loop lives here rather than in the browser so it keeps repeating while the
phone that started it is locked. One worker thread polls the looping speaker's
position and seeks back to the start once it reaches the end. State is held in
memory only: a dashboard restart drops the loop.
"""

from dataclasses import dataclass, field
import threading
import time

from .van_dashboard_sonos import clock_label, seek_clock, seekable_track, track_seconds

MIN_LOOP_SECONDS = 2
# Sonos reports whole seconds, so the end is reached when the reported second
# turns over; fast polls near the end catch that within NEAR_POLL_SECONDS.
SLOW_POLL_SECONDS = 1.0
NEAR_POLL_SECONDS = 0.2
NEAR_WINDOW_SECONDS = 2
# A position that has not moved for this long is paused or buffering.
STALL_SECONDS = 2.5
# The speaker can report the old position briefly after a seek.
SEEK_SETTLE_SECONDS = 1.0
MAX_POLL_FAILURES = 10


def track_identity(track):
    # Spotify Connect keeps one URI for the whole session, so the title,
    # artist and duration are what change between tracks.
    return tuple(track.get(key) or "" for key in ("uri", "title", "artist", "duration"))


@dataclass
class ActiveLoop:
    start: int
    end: int
    duration: int
    title: str
    speaker: object
    identity: tuple
    # Worker-owned progress; only the single worker thread touches these.
    last_position: int = -1
    last_change_at: float = 0.0
    seeked_at: float = field(default=float("-inf"))
    failures: int = 0

    @property
    def trigger(self):
        # Seek a second before the track's end so the queue never advances.
        return min(self.end, self.duration - 1)


class SonosLoop:
    def __init__(self, sonos, clock=time.monotonic, start_worker=True):
        self.sonos = sonos
        self.clock = clock
        self.start_worker = start_worker
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.active = None
        self.notice = None
        self.worker = None

    def state(self):
        with self.lock:
            loop, notice = self.active, self.notice
        if loop is None:
            return {"active": False, "start": None, "end": None, "title": None, "notice": notice}
        return {
            "active": True,
            "start": loop.start,
            "end": loop.end,
            "title": loop.title,
            "notice": None,
        }

    def start(self, start, end):
        """Loop the current track between whole seconds; seeks in if outside."""
        start, end = int(start), int(end)
        if start < 0 or end - start < MIN_LOOP_SECONDS:
            raise ValueError(f"the loop must be at least {MIN_LOOP_SECONDS} seconds long")
        speaker = self.sonos.coordinator()
        track, position, duration = seekable_track(speaker)
        if end > duration:
            raise ValueError("the loop ends after the track does")
        loop = ActiveLoop(
            start=start,
            end=end,
            duration=duration,
            title=track.get("title") or "",
            speaker=speaker,
            identity=track_identity(track),
            last_position=position,
            last_change_at=self.clock(),
        )
        if not start <= position < loop.trigger:
            speaker.seek(seek_clock(start))
            loop.last_position = start
            loop.seeked_at = self.clock()
        with self.lock:
            self.active = loop
            self.notice = None
            if self.start_worker and self.worker is None:
                self.worker = threading.Thread(
                    target=self._run, name="sonos-loop", daemon=True
                )
                self.worker.start()
        self.wake.set()
        return f"Looping {clock_label(start)}–{clock_label(end)}"

    def stop(self):
        with self.lock:
            self.active = None
            self.notice = None
        self.wake.set()
        return "Loop stopped"

    def tick(self):
        """Run one poll; return seconds until the next one, or None when idle."""
        with self.lock:
            loop = self.active
        if loop is None:
            return None
        now = self.clock()
        try:
            track = loop.speaker.get_current_track_info() or {}
        except Exception as exc:
            return self._poll_failed(loop, exc)
        if track_identity(track) != loop.identity:
            self._finish(loop, "Loop stopped: the track changed")
            return None
        position = track_seconds(track.get("position"))
        if position is None:
            return self._poll_failed(loop, "no track position")
        loop.failures = 0
        if position != loop.last_position:
            loop.last_position = position
            loop.last_change_at = now
        if position >= loop.trigger and now - loop.seeked_at >= SEEK_SETTLE_SECONDS:
            with self.lock:
                if self.active is not loop:
                    # Stopped or replaced during the poll: never seek for it.
                    return 0
            try:
                loop.speaker.seek(seek_clock(loop.start))
            except Exception as exc:
                self._finish(loop, f"Loop stopped: Sonos could not seek ({exc})")
                return None
            loop.seeked_at = now
            loop.last_position = loop.start
            loop.last_change_at = now
            position = loop.start
        if now - loop.last_change_at > STALL_SECONDS:
            return SLOW_POLL_SECONDS
        if loop.trigger - position <= NEAR_WINDOW_SECONDS:
            return NEAR_POLL_SECONDS
        return SLOW_POLL_SECONDS

    def _poll_failed(self, loop, reason):
        loop.failures += 1
        if loop.failures >= MAX_POLL_FAILURES:
            self._finish(loop, f"Loop stopped: Sonos stopped answering ({reason})")
            return None
        return SLOW_POLL_SECONDS

    def _finish(self, loop, notice):
        with self.lock:
            if self.active is loop:
                self.active = None
                self.notice = notice

    def _run(self):
        while True:
            delay = self.tick()
            with self.lock:
                # Decided under the lock start() spawns under, so a loop
                # started as this worker exits always gets a new worker.
                if self.active is None:
                    self.worker = None
                    return
            # None here means a new loop replaced the one that just ended.
            self.wake.wait(0 if delay is None else delay)
            self.wake.clear()
