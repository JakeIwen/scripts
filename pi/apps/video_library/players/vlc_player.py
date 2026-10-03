"""VLC player boundary for the video library."""

from __future__ import annotations

import importlib
import json
import math
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

if __package__:
    from ..config import (
        DBUS_PROPERTIES,
        DISPLAY,
        MKVMERGE,
        MPRIS_NAME,
        MPRIS_PATH,
        MPRIS_PLAYER,
        MPRIS_ROOT,
        PKILL,
        PLAYER_UNIT,
        ROOM_PREPARE_TIMEOUT,
        RUNTIME_DIR,
        SESSION_BUS,
        SNS,
        SYSTEMCTL,
        SYSTEMD_RUN,
        VLC,
        VLC_FIXED_VOLUME,
        XSET,
    )
else:  # Direct execution from the Pi's flat deployment directory.
    from config import (  # type: ignore[no-redef]
        DBUS_PROPERTIES,
        DISPLAY,
        MKVMERGE,
        MPRIS_NAME,
        MPRIS_PATH,
        MPRIS_PLAYER,
        MPRIS_ROOT,
        PKILL,
        PLAYER_UNIT,
        ROOM_PREPARE_TIMEOUT,
        RUNTIME_DIR,
        SESSION_BUS,
        SNS,
        SYSTEMCTL,
        SYSTEMD_RUN,
        VLC,
        VLC_FIXED_VOLUME,
        XSET,
    )

def native(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [native(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return str(value)


class RoomPreparationError(RuntimeError):
    """Raised when rear_movie cannot be completed before playback resumes."""


class VlcController:
    def __init__(
        self,
        *,
        dbus_module: Any = None,
        run: Callable[..., Any] = subprocess.run,
        popen: Callable[..., Any] = subprocess.Popen,
        killpg: Callable[[int, int], None] = os.killpg,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._dbus_module = dbus_module
        self.run = run
        self.popen = popen
        self.killpg = killpg
        self.sleep = sleep
        self.lock = threading.RLock()
        self.room_lock = threading.RLock()
        self.room_process: Any = None

    def _dbus(self):
        if self._dbus_module is None:
            self._dbus_module = importlib.import_module("dbus")
        return self._dbus_module

    def _interfaces(self):
        module = self._dbus()
        bus = module.SessionBus()
        obj = bus.get_object(MPRIS_NAME, MPRIS_PATH)
        return (
            module,
            module.Interface(obj, DBUS_PROPERTIES),
            module.Interface(obj, MPRIS_PLAYER),
            module.Interface(obj, MPRIS_ROOT),
        )

    def snapshot(self) -> dict[str, Any]:
        try:
            _module, props, _player, _root = self._interfaces()
            player_values = native(props.GetAll(MPRIS_PLAYER))
            try:
                root_values = native(props.GetAll(MPRIS_ROOT))
            except Exception:
                root_values = {}
            metadata = player_values.get("Metadata") or {}
            url = str(metadata.get("xesam:url") or "")
            path = unquote(urlsplit(url).path) if url.startswith("file:") else None
            duration_us = int(metadata.get("mpris:length") or 0)
            position_us = int(player_values.get("Position") or 0)
            snapshot = {
                "available": True,
                "state": str(player_values.get("PlaybackStatus") or "Stopped").upper(),
                "path": path,
                "url": url,
                "title": str(metadata.get("xesam:title") or (os.path.basename(path) if path else "")),
                "position": max(0.0, position_us / 1_000_000),
                "duration": max(0.0, duration_us / 1_000_000),
                "track_id": metadata.get("mpris:trackid"),
                "volume": float(player_values.get("Volume", 0.0)),
                "rate": float(player_values.get("Rate", 1.0)),
                "fullscreen": bool(root_values.get("Fullscreen", False)),
                "can_fullscreen": bool(root_values.get("CanSetFullscreen", False)),
                "can_seek": bool(player_values.get("CanSeek", False)),
                "can_next": bool(player_values.get("CanGoNext", False)),
                "can_previous": bool(player_values.get("CanGoPrevious", False)),
                "can_play": bool(player_values.get("CanPlay", False)),
                "can_pause": bool(player_values.get("CanPause", False)),
                "can_control": bool(player_values.get("CanControl", False)),
            }
            try:
                self.enforce_fixed_volume(snapshot)
            except Exception as exc:
                snapshot["volume_error"] = str(exc)
            return snapshot
        except Exception as exc:
            return {
                "available": False,
                "state": "OFFLINE",
                "error": str(exc),
                "position": 0.0,
                "duration": 0.0,
            }

    def action(self, name: str) -> None:
        methods = {
            "toggle": "PlayPause",
            "play": "Play",
            "pause": "Pause",
            "next": "Next",
            "previous": "Previous",
            "stop": "Stop",
        }
        if name not in methods:
            raise ValueError(f"unknown player action '{name}'")
        with self.lock:
            _module, _props, player, _root = self._interfaces()
            getattr(player, methods[name])()

    def seek(self, seconds: float) -> None:
        with self.lock:
            module, _props, player, _root = self._interfaces()
            player.Seek(module.Int64(int(seconds * 1_000_000)))

    def set_position(self, track_id: Any, seconds: float) -> None:
        with self.lock:
            module, _props, player, _root = self._interfaces()
            player.SetPosition(track_id, module.Int64(int(seconds * 1_000_000)))

    def set_volume(self, value: float) -> float:
        value = max(0.0, min(1.25, float(value)))
        with self.lock:
            module, props, _player, _root = self._interfaces()
            props.Set(MPRIS_PLAYER, "Volume", module.Double(value))
        return value

    def enforce_fixed_volume(self, snapshot: dict[str, Any]) -> bool:
        if not snapshot.get("available"):
            return False
        try:
            current = float(snapshot.get("volume"))
        except (TypeError, ValueError):
            current = math.nan
        changed = not math.isfinite(current) or abs(current - VLC_FIXED_VOLUME) > 0.005
        if changed:
            self.set_volume(VLC_FIXED_VOLUME)
        snapshot["volume"] = VLC_FIXED_VOLUME
        return changed

    def set_rate(self, value: float) -> float:
        value = max(0.5, min(2.0, float(value)))
        with self.lock:
            module, props, _player, _root = self._interfaces()
            props.Set(MPRIS_PLAYER, "Rate", module.Double(value))
        return value

    def toggle_fullscreen(self) -> bool:
        snapshot = self.snapshot()
        if not snapshot.get("available"):
            raise RuntimeError("VLC is not running")
        if not snapshot.get("can_fullscreen"):
            raise RuntimeError("this VLC session does not expose fullscreen control")
        value = not snapshot.get("fullscreen", False)
        with self.lock:
            module, props, _player, _root = self._interfaces()
            props.Set(MPRIS_ROOT, "Fullscreen", module.Boolean(value))
        return value

    def quit(self) -> None:
        try:
            _module, _props, _player, root = self._interfaces()
            root.Quit()
        except Exception:
            pass

    def _command(self, args: list[str], timeout: float = 10) -> Any:
        return self.run(args, capture_output=True, text=True, timeout=timeout, check=False)

    def _stop_existing(self) -> None:
        self.quit()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not self.snapshot().get("available"):
                break
            self.sleep(0.15)
        if self.snapshot().get("available"):
            self._command(
                [PKILL, "-TERM", "-u", str(os.getuid()), "-x", "vlc"],
                timeout=5,
            )
        self._command([SYSTEMCTL, "--user", "stop", PLAYER_UNIT], timeout=5)
        self._command([SYSTEMCTL, "--user", "reset-failed", PLAYER_UNIT], timeout=5)

    def _prepare_room(self, task: str = "rear_movie") -> Any:
        if task not in ("rear_movie", "rear_movie_resume"):
            raise ValueError("unknown rear-room preparation task")
        self._command([XSET, "-display", DISPLAY, "dpms", "force", "on"], timeout=5)
        with self.room_lock:
            process = self.room_process
            if process is not None:
                try:
                    if process.poll() is None:
                        return process
                except Exception:
                    pass
                self.room_process = None
            if os.path.isfile(SNS) and os.access(SNS, os.R_OK):
                self.room_process = self.popen(
                    ["/bin/bash", SNS, task],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            return self.room_process

    def _terminate_room_process(self, process: Any) -> None:
        try:
            pid = int(process.pid)
        except (AttributeError, TypeError, ValueError):
            pid = None
        if pid is not None:
            try:
                self.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            try:
                process.terminate()
            except Exception:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if pid is not None:
                try:
                    self.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                try:
                    process.kill()
                except Exception:
                    pass
            try:
                process.wait(timeout=5)
            except Exception:
                pass
        except Exception:
            pass
        with self.room_lock:
            if self.room_process is process:
                self.room_process = None

    def prepare_room(self, *, wait: bool = False) -> None:
        process = self._prepare_room("rear_movie_resume")
        if process is None:
            raise RoomPreparationError("rear_movie setup script is unavailable")
        if not wait:
            return
        try:
            returncode = process.wait(timeout=ROOM_PREPARE_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            self._terminate_room_process(process)
            raise RoomPreparationError(
                f"rear_movie did not finish within {ROOM_PREPARE_TIMEOUT:g} seconds"
            ) from exc
        with self.room_lock:
            if self.room_process is process:
                self.room_process = None
        if returncode:
            raise RoomPreparationError(
                f"rear_movie exited with status {returncode}"
            )

    def room_preparing(self) -> bool:
        with self.room_lock:
            process = self.room_process
            if process is None:
                return False
            try:
                if process.poll() is None:
                    return True
            except Exception:
                pass
            self.room_process = None
            return False

    def _subtitle_index(self, path: str) -> int | None:
        media_path = os.path.realpath(path)
        if Path(media_path).suffix.casefold() != ".mkv" or not os.path.isfile(MKVMERGE):
            return None
        try:
            result = self._command([MKVMERGE, "-J", media_path], timeout=20)
            if result.returncode:
                return None
            tracks = [track for track in json.loads(result.stdout).get("tracks", []) if track.get("type") == "subtitles"]
            for index, track in enumerate(tracks):
                properties = track.get("properties") or {}
                if properties.get("language") == "eng" and properties.get("forced_track") is False:
                    return index
        except (OSError, ValueError, TypeError):
            return None
        return None

    def launch(self, paths: list[str], *, position: float = 0, subtitles: str = "auto") -> dict[str, Any]:
        if not paths:
            raise ValueError("no media paths supplied")
        if subtitles not in ("auto", "off"):
            raise ValueError("subtitles must be auto or off")
        with self.lock:
            self._stop_existing()
            self._prepare_room()
            command = [
                VLC,
                "--control=dbus",
                "--audio-language=eng,en",
                "--sub-language=eng,en",
                "--avcodec-hw=v4l2-request",
                "--no-video-title-show",
                f"--volume={round(VLC_FIXED_VOLUME * 256)}",
                "--no-volume-save",
                "--gain=1.0",
            ]
            if subtitles == "off":
                command.append("--sub-track=-1")
            else:
                subtitle_index = self._subtitle_index(paths[0])
                if subtitle_index is not None:
                    command.append(f"--sub-track={subtitle_index}")
            command.extend(paths)
            launch = [
                SYSTEMD_RUN,
                "--user",
                f"--unit={PLAYER_UNIT.removesuffix('.service')}",
                "--collect",
                "--quiet",
                "--service-type=exec",
                f"--setenv=DISPLAY={DISPLAY}",
                f"--setenv=XDG_RUNTIME_DIR={RUNTIME_DIR}",
                f"--setenv=DBUS_SESSION_BUS_ADDRESS={SESSION_BUS}",
                *command,
            ]
            result = self._command(launch, timeout=15)
            if result.returncode:
                message = (result.stderr or result.stdout or "systemd-run failed").strip()
                raise RuntimeError(message)

            deadline = time.monotonic() + 12
            expected_path = os.path.realpath(paths[0])
            snapshot: dict[str, Any] = {}
            while time.monotonic() < deadline:
                snapshot = self.snapshot()
                current_path = snapshot.get("path")
                requested_track_ready = bool(
                    snapshot.get("available")
                    and snapshot.get("track_id") is not None
                    and current_path
                    and os.path.realpath(str(current_path)) == expected_path
                )
                if requested_track_ready:
                    break
                self.sleep(0.25)
            else:
                if not snapshot.get("available"):
                    raise RuntimeError("VLC did not appear on the session bus")
                raise RuntimeError("VLC did not load the requested video")

            self.set_volume(VLC_FIXED_VOLUME)
            snapshot["volume"] = VLC_FIXED_VOLUME
            if position <= 0:
                return snapshot

            last_error: Exception | None = None
            while time.monotonic() < deadline:
                try:
                    self.set_position(snapshot["track_id"], position)
                    self.sleep(0.15)
                    snapshot = self.snapshot()
                    current_path = snapshot.get("path")
                    if (
                        snapshot.get("track_id") is not None
                        and current_path
                        and os.path.realpath(str(current_path)) == expected_path
                        and abs(float(snapshot.get("position") or 0) - position) <= 3
                    ):
                        return snapshot
                except Exception as exc:
                    last_error = exc
                self.sleep(0.25)
            if last_error is not None:
                raise RuntimeError(f"VLC could not resume the video: {last_error}")
            raise RuntimeError("VLC did not accept the resume position")
