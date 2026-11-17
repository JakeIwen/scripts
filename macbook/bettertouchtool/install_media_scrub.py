#!/usr/bin/env python3
"""Preview or install the Media track-scrub slider.

Adds a 220x40 Web View item directly after PPause in the Media bar (a range slider with
elapsed and remaining time for media playing on the Pi) and points PPause at the local
media_scrub.py snapshot instead of ssh, refreshing every second. Inspects by default.
--apply backs up Media, writes through BTT's socket, verifies the runtime export and the
saved store, undoes every field it changed on failure, then lets BTT Guard accept exactly
those edits. The page and PPause script embed this checkout's absolute media_scrub.py path.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from btt_common import MEDIA, ROOT, backup_configuration, current_database, records
import btt_socket
from btt_guard.installer_checkpoint import after_install, before_install
import media_scrub_install as install
from media_scrub_control.page import build_page

DIRECTORY = Path(__file__).resolve().parent
BACKUPS = DIRECTORY / ".local/media-scrub-backups"
SCRIPT = ROOT / "macbook/scripts/media_scrub.py"
INSTALLER = "install_media_scrub"


def build_plan(call=None, script=None):
    call = btt_socket.call if call is None else call  # looked up late so tests can replace it
    script = SCRIPT if script is None else Path(script)
    template = (DIRECTORY / "play_status.js").read_text(encoding="utf-8")
    return install.make_plan(install.read_trigger(call, MEDIA), install.read_trigger(call, install.SCRUB),
                             install.render_play_status(template, str(script)), build_page(str(script)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="back up, install, verify saved records")
    mode.add_argument("--inspect", action="store_true", help="read-only preview (the default)")
    parser.add_argument("--no-checkpoint", action="store_true", help="do not advance BTT Guard after verification")
    args = parser.parse_args(argv)
    os.umask(0o077)
    plan = build_plan()
    for line in install.describe(plan):
        print(line)
    if not SCRIPT.is_file():
        print("note: %s does not exist yet%s" % (SCRIPT, "; no changes made." if args.apply else ""))
        if args.apply:
            return 1
    if not args.apply:
        print("Already configured." if install.is_noop(plan) else "Inspect only; re-run with --apply to install.")
        return 0
    baseline = before_install(no_checkpoint=args.no_checkpoint)
    if install.is_noop(plan):
        print("Already configured: slider and PPause.")
        after_install(baseline, [], INSTALLER, wrote=False)
        return 0
    database = current_database()
    BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
    message = install.apply_plan(
        btt_socket.call, plan,
        backup=lambda snapshot: backup_configuration(database, snapshot, prefix="btt-media-scrub-backup-",
                                                     directory_root=BACKUPS),
        saved_check=lambda: install.saved_errors(records(database), plan))
    print(message)
    after_install(baseline, plan["touched"], INSTALLER, wrote=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (install.Refusal, install.InstallFailed, RuntimeError, OSError, ValueError, sqlite3.Error,
            subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
