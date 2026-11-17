"""Argument parsing and dispatch for the BTT-facing helper."""
import argparse
import json
import os
import sys

from .seek_client import run_seek
from .state_dir import StateDir, StateDirError
from .status import run_status
from .watcher import run_watcher


def build_parser():
    parser = argparse.ArgumentParser(prog="media_scrub.py")
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status")
    status.add_argument("--text", action="store_true")
    status.add_argument("--wait", type=float, default=0.0)
    seek = commands.add_parser("seek")
    seek.add_argument("--b64", required=True)
    commands.add_parser("watch")
    return parser


def main(script, argv=None):
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as error:
        return 2 if error.code else 0
    if args.command == "seek":
        # Validate before touching the state dir: a bad token never starts anything.
        from .records import RecordError, parse_token
        try:
            parse_token(args.b64)
        except RecordError as error:
            print(json.dumps({"ok": False, "superseded": False, "reason": "invalid",
                              "message": str(error)}))
            return 2
    try:
        state = StateDir.open()
    except StateDirError as error:
        if args.command == "status":
            print("Status unavailable" if args.text else json.dumps(
                {"ok": False, "error": str(error)}))
            return 0
        print(json.dumps({"ok": False, "superseded": False, "reason": "unavailable",
                          "message": str(error)}))
        return 1
    if args.command == "status":
        return run_status(state, os.path.abspath(script), args.text, args.wait)
    if args.command == "seek":
        return run_seek(state, args.b64)
    return run_watcher()
