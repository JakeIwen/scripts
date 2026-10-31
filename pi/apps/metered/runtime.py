"""Supervised reconciliation independent of dashboard requests and lifetime."""
import signal
import threading
import time

from pi.package_runtime import record_running_release
from .config import TICK_SECONDS
from .torrents import TorrentHold


def run(controller, once=False):
    controller.adapters = {'pi.torrents': TorrentHold(controller.paths.state.parent)}
    if once:
        controller.evaluate(enforce=True)
        return 0
    record_running_release('/run/vanpi-metered', warning_prefix='metered')
    stopped = threading.Event()
    def stop(_signal, _frame):
        stopped.set()
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, stop)
    while not stopped.is_set():
        started = time.monotonic()
        try:
            controller.evaluate(enforce=True)
        except Exception:
            # A broken state store must not leave previously admitted traffic running.
            controller.adapters['pi.torrents'](False)
            raise
        stopped.wait(max(0.0, TICK_SECONDS - (time.monotonic() - started)))
    return 0
