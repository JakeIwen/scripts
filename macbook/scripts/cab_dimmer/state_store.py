"""Private, locked display cab dimmer state."""
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time

TOOL = "display-cab-dimmer"
STATE_DIR_ENV = "DISPLAY_CAB_DIMMER_STATE_DIR"
STATE_VERSION = 1


def _state_directory(state_dir: Path | str | None = None) -> Path:
    if state_dir is not None:
        return Path(state_dir).expanduser()
    configured = os.environ.get(STATE_DIR_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "state" / TOOL


def _state_path(state_dir: Path | str | None = None) -> Path:
    return _state_directory(state_dir) / "state.json"


def read_state(state_dir: Path | str | None = None) -> dict:
    try:
        value = json.loads(_state_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _atomic_write_state(path: Path, state: dict) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = -1
            json.dump(state, output, sort_keys=True, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def update_state(changes: dict, state_dir: Path | str | None = None) -> bool:
    try:
        directory = _state_directory(state_dir)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        lock_path = directory / "state.lock"
        with lock_path.open("a+b") as lock:
            os.fchmod(lock.fileno(), 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state = read_state(directory)
            state.update(changes)
            state["version"] = STATE_VERSION
            _atomic_write_state(directory / "state.json", state)
        return True
    except Exception as error:
        print(time.strftime("%Y-%m-%d %H:%M:%S"),
              f"state update skipped: {type(error).__name__}: {error}", flush=True)
        return False
