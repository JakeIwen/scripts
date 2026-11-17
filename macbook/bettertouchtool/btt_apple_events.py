"""Large BTT writes over Apple Events, for payloads BTT's socket CLI cannot carry.

bttcli truncates a message somewhere between ~8 KB and ~15 KB (measured: a 15000-char
payload arrives as invalid JSON, 17000+ exits 141/SIGPIPE), which breaks any trigger that
inlines a Web View page. Apple Events have no such limit. The payload goes to
btt_write_large.js through a private temp file, never argv, and is never echoed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable, Optional

DIRECTORY = Path(__file__).resolve().parent
WORKER = DIRECTORY / "btt_write_large.js"
# Messages above this many characters of parameter text go over Apple Events. 8000 sits
# under the smallest size seen truncated; real BTT items below it have always arrived whole.
SOCKET_LIMIT = 8000
WRITE_COMMANDS = ("add_new_trigger", "update_menu_item")


def write_large(command: str, **parameters: Any) -> None:
    """Send one BTT write over Apple Events; raises unless the worker confirms 'ok'."""
    descriptor, name = tempfile.mkstemp(prefix="btt-large-write-", suffix=".json")  # mode 0600
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"command": command, "parameters": {k: str(v) for k, v in parameters.items()}}, handle)
        result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", str(WORKER), name],
                                capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(name)
    if result.returncode or result.stdout.strip() != "ok":
        raise RuntimeError(f"BTT Apple Events command {command} failed; no success confirmed.")


def routed(call: Callable[..., str], large: Optional[Callable[..., None]] = None) -> Callable[..., str]:
    """A socket `call` that sends writes over SOCKET_LIMIT through `large` instead."""
    def send(command: str, **parameters: Any) -> str:
        if command in WRITE_COMMANDS and sum(len(str(v)) for v in parameters.values()) > SOCKET_LIMIT:
            (large or write_large)(command, **parameters)  # looked up late so tests can replace it
            return ""
        return call(command, **parameters)
    return send
