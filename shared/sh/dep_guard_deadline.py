"""One wall-clock budget for discovery, filesystem operations, network and persistence."""
import threading
import time

DEADLINE_SECONDS = 5
DEADLINE_NOTE = 'Dependency check unfinished: deadline reached; check unverified names manually.'


def with_note(result, message):
    out = dict(result or {'hookEventName': 'PreToolUse'})
    key = 'permissionDecisionReason' if out.get('permissionDecision') == 'deny' else 'additionalContext'
    out[key] = '\n'.join(filter(None, (out.get(key), message)))
    return out


class Deadline:
    def __init__(self, seconds=None):
        duration = DEADLINE_SECONDS if seconds is None else min(DEADLINE_SECONDS, max(0, seconds))
        self.end = time.monotonic() + duration
        self.snapshot = None

    def remaining(self):
        return max(0, self.end - time.monotonic())

    def publish(self, result):
        # One coordinator publishes fully rendered, never-mutated snapshots. The
        # waiter need not acquire a lock held by code blocked in a filesystem call.
        self.snapshot = result

    def run(self, check):
        finished = threading.Event()

        def work():
            try:
                self.publish(check(self))
            except ImportError:
                self.publish(with_note(self.snapshot, 'Dependency install guard is broken (required hook module missing); check names manually.'))
            except Exception:
                self.publish(with_note(self.snapshot, 'Dependency install guard unavailable; check package names manually.'))
            finally:
                finished.set()

        threading.Thread(target=work, daemon=True).start()
        if finished.wait(timeout=self.remaining()):
            return self.snapshot
        return with_note(self.snapshot, DEADLINE_NOTE)
