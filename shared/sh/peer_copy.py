#!/usr/bin/python3
"""Copy home-relative text files over bounded, noninteractive SSH.

Known terms and the glossary share a Mac/Pi copy protocol. Keeping it here
lets both flat-deployed scripts use the same timeouts and atomic publication.
A missing remote file reads as empty text; an unreachable peer returns None.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import PurePosixPath

SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=3"]


def read_peer(peer: str, path: str) -> str | None:
    command = f"cat {shlex.quote(path)} 2>/dev/null || true"
    try:
        result = subprocess.run(["ssh", *SSH_OPTIONS, peer, command],
                                capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def write_peer(peer: str, path: str, text: str) -> bool:
    parent = shlex.quote(str(PurePosixPath(path).parent))
    target, temporary = shlex.quote(path), shlex.quote(path + ".tmp")
    command = f"mkdir -p {parent} && cat > {temporary} && mv {temporary} {target}"
    try:
        result = subprocess.run(["ssh", *SSH_OPTIONS, peer, command], input=text,
                                capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0
