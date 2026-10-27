"""Private, locked, atomic persistence for the weekly retirement check."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile

from .retirement_state import RetirementState


class RetirementStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        with self.path.with_suffix(".lock").open("a+b") as lock:
            os.fchmod(lock.fileno(), 0o600)
            # A second weekly/manual invocation must not race a notification claim.
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield

    def load(self):
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return RetirementState()
        return RetirementState.from_dict(json.loads(raw))

    def save(self, state):
        descriptor, temporary = tempfile.mkstemp(prefix=".retirement-", dir=str(self.path.parent))
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(state.as_dict(), output, indent=2, allow_nan=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, str(self.path))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
