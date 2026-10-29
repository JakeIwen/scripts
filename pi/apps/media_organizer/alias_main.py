"""Compatibility entry point for am/an and qBittorrent's completion command."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from . import config
from .alias_links import run_locked


def _detach(mode: str):
    config.ALIAS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    # Preserve a checkout or pinned release for the child even with -P and an
    # unrelated caller cwd; never let a queued worker drift to a newer release.
    root = str(Path(__file__).resolve().parents[3])
    env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"]
                                if env.get("PYTHONPATH") else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    code = ("from pi.apps.media_organizer.alias_links import run_locked; "
            "import sys; sys.exit(run_locked(sys.argv[1]))")
    with config.ALIAS_LOG_PATH.open("ab") as log:
        subprocess.Popen([sys.executable, "-P", "-c", code, mode],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True, close_fds=True, env=env)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args not in ([], [config.ALIAS_MODE_NEW]):
        print("usage: alias_media.sh [new]", file=sys.stderr)
        return 2
    mode = config.ALIAS_MODE_NEW if args else config.ALIAS_MODE_FULL
    if os.environ.get(config.ALIAS_OUTPUT_ROOT_ENV) is not None:
        return run_locked(mode)
    _detach(mode)
    print(f"alias media {mode} scheduled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
