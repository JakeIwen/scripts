"""Bounded, cached read-only access to the network flight recorder.

The collector owns its database schema. Dashboard requests only invoke the
recorder's report/export commands, which must never create or migrate storage.
"""

import copy
import json
import math
import os
import re
import subprocess
import sys
import threading
import time

__all__ = ["NetworkHistoryClient", "NetworkHistoryError", "network_history_query"]


class NetworkHistoryError(RuntimeError):
    pass


def network_history_query(arguments):
    """Validate the public query before constructing a fixed CLI invocation."""
    allowed = {"hours", "start", "end", "uplink", "device", "search"}
    if set(arguments) - allowed or any(len(arguments.getlist(key)) != 1 for key in arguments):
        raise ValueError("network history accepts one value per supported filter")
    query = {}
    if "start" in arguments or "end" in arguments:
        if "hours" in arguments or not {"start", "end"} <= set(arguments):
            raise ValueError("provide both start and end, or hours")
        try:
            start, end = float(arguments["start"]), float(arguments["end"])
        except (TypeError, ValueError):
            raise ValueError("start and end must be UTC epoch seconds") from None
        if not all(math.isfinite(value) for value in (start, end)) or not 0 < start < end <= 253402300799:
            raise ValueError("start and end must be increasing UTC epoch seconds")
        if end - start > 720 * 3600:
            raise ValueError("network history intervals are limited to 30 days")
        query.update(start=start, end=end)
    else:
        try:
            hours = int(arguments.get("hours", "6"))
        except (ValueError, TypeError):
            raise ValueError("hours must be 1, 6, 24, 168, or 720") from None
        if hours not in (1, 6, 24, 168, 720):
            raise ValueError("hours must be 1, 6, 24, 168, or 720")
        query["hours"] = hours
    for key in ("uplink", "device", "search"):
        value = arguments.get(key, "").strip()
        if len(value) > (200 if key == "search" else 128) or any(ord(char) < 32 for char in value):
            raise ValueError(f"invalid {key} filter")
        if value:
            query[key] = value
    return query


class NetworkHistoryClient:
    def __init__(self, tool=None, database=None, command=subprocess.run, clock=time.monotonic):
        self.tool = tool or os.environ.get(
            "VAN_DASHBOARD_NETWORK_RECORDER_TOOL", "/home/pi/scripts/network_flight_recorder.py"
        )
        self.database = database or os.environ.get(
            "VAN_DASHBOARD_NETWORK_RECORDER_DB", "auto"
        )
        self.command = command
        self.clock = clock
        self.cache = {}
        self.lock = threading.Lock()

    def _read(self, command_arguments):
        key = tuple(command_arguments)
        with self.lock:
            cached = self.cache.get(key)
            if cached and cached[0] > self.clock():
                if cached[2]:
                    raise NetworkHistoryError(cached[2])
                return copy.deepcopy(cached[1])
            try:
                result = self.command(
                    [sys.executable, self.tool, "--database", self.database, *command_arguments],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                # Never reflect subprocess stderr or malformed stdout into the UI;
                # they can include local paths, arguments, or unredacted evidence.
                if result.returncode:
                    raise NetworkHistoryError("Recorder data unavailable; check the recorder service")
                if len(result.stdout) > 4 * 1024 * 1024:
                    raise NetworkHistoryError("Recorder report exceeded the response limit")
                payload = json.loads(result.stdout)
                if not isinstance(payload, dict) or payload.get("ok") is not True:
                    raise NetworkHistoryError("Recorder returned an invalid report")
            except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                message = "Recorder report could not be read"
                self.cache[key] = (self.clock() + 2, None, message)
                self._prune_cache()
                raise NetworkHistoryError(message) from exc
            except NetworkHistoryError as exc:
                self.cache[key] = (self.clock() + 2, None, str(exc))
                self._prune_cache()
                raise
            self.cache[key] = (self.clock() + 10, payload, None)
            self._prune_cache()
            return copy.deepcopy(payload)

    def _prune_cache(self):
        while len(self.cache) > 16:
            del self.cache[next(iter(self.cache))]

    def report(self, query):
        arguments = ["report", "--limit", "200", "--json"]
        arguments.extend(f"--{key}={value}" for key, value in sorted(query.items()))
        return self._read(arguments)

    def incident(self, incident_id):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", incident_id):
            raise ValueError("invalid incident identifier")
        return self._read(["export", "--incident", incident_id, "--limit", "500", "--json"])
