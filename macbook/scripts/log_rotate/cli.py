"""Command line: inventory, rotate, policy, add, ignore, unignore, status (all take --json)."""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Dict, List, Optional

from . import archives, eligibility, inventory, policy, rotate, state, status
from .problems import find_problems
from .records import EXIT_ERROR, EXIT_OK, LogRotateError

_SWITCH = {"on": True, "off": False}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="log_rotate.py", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--json", action="store_true", help="print JSON (the panel's form)")
        return sub

    command("inventory", "list logs with sizes, archives, policy and state (read-only)")
    command("status", "print the automation status record (read-only)")
    rotation = command("rotate", "rotate the logs that are over their limit")
    rotation.add_argument("--only", metavar="PATH", help="rotate just this log")
    rotation.add_argument("--force", action="store_true",
                          help="rotate below the limit; with --only also a disabled log")
    edit = command("policy", "set or clear one log's policy")
    edit.add_argument("--revision", required=True)
    edit.add_argument("path")
    edit.add_argument("--max-mb")
    edit.add_argument("--keep")
    edit.add_argument("--compress", choices=sorted(_SWITCH))
    edit.add_argument("--enabled", choices=sorted(_SWITCH))
    edit.add_argument("--clear", action="store_true", help="drop the per-file policy")
    for name, text in (("add", "track a log path the sources miss"),
                       ("ignore", "hide a path and never rotate it"),
                       ("unignore", "stop ignoring a path"),
                       ("adopt", "record eligible existing archives so rotation may continue")):
        sub = command(name, text)
        sub.add_argument("--revision", required=True)
        sub.add_argument("path")
    return parser


def _policy_fields(args: argparse.Namespace) -> Dict[str, Any]:
    fields = {}  # type: Dict[str, Any]
    for name in ("max_mb", "keep"):
        raw = getattr(args, name)
        if raw is None:
            continue
        try:
            fields[name] = int(raw)
        except ValueError:
            raise LogRotateError(EXIT_ERROR, "{} must be a whole number (got {!r})".format(name, raw),
                                 field=name) from None
    for name in ("compress", "enabled"):
        if getattr(args, name) is not None:
            fields[name] = _SWITCH[getattr(args, name)]
    return fields


def _usable_path(raw: str, home: str) -> str:
    path = eligibility.normalize(raw, home)
    if path is None:
        raise LogRotateError(EXIT_ERROR, "path must be absolute (or start with ~): {!r}".format(raw),
                             field="path")
    return path


def _inventory_value(snapshot: inventory.Snapshot, home: str) -> Dict[str, Any]:
    """The inventory with what needs attention; the Mac Panel renders that list as sent."""
    value = snapshot.as_dict()
    value["problems"] = find_problems(snapshot, state.read_json(home, state.RUN_FILE), snapshot.generated_at)
    return value


def _edited(home: str, revision: str, path: str, change) -> Dict[str, Any]:
    policy.update_files_config(revision, change)
    snapshot = inventory.take_snapshot(home)
    value = _inventory_value(snapshot, home)
    entry = snapshot.entry(path)
    value["log"] = entry.as_dict() if entry is not None else None
    return value


def _run_edit(args: argparse.Namespace, home: str) -> Dict[str, Any]:
    path = _usable_path(args.path, home)
    if args.command == "adopt":
        policy.require_rotatable(path, home)
        with state.run_lock(home):
            return _edited(home, args.revision, path, lambda data: archives.adopt(path, home))
    if args.command == "policy":
        fields = _policy_fields(args)
        if args.clear == bool(fields):
            raise LogRotateError(EXIT_ERROR, "give either --clear or at least one field to set")
        policy.require_rotatable(path, home)
        fields = policy.validate_fields(fields)
        change = ((lambda data: policy.clear_policy(data, path)) if args.clear
                  else (lambda data: policy.set_policy(data, path, fields)))
    elif args.command == "add":
        policy.require_rotatable(path, home)
        change = lambda data: policy.add_extra(data, path)  # noqa: E731
    elif args.command == "ignore":
        change = lambda data: policy.ignore(data, path)  # noqa: E731
    else:
        change = lambda data: policy.unignore(data, path)  # noqa: E731
    return _edited(home, args.revision, path, change)


def _run_rotate(args: argparse.Namespace, home: str) -> Dict[str, Any]:
    only = _usable_path(args.only, home) if args.only else None
    with state.run_lock(home):
        archives.prune(home)
        snapshot = inventory.take_snapshot(home)
        return rotate.run_rotation(snapshot, home, only=only, force=args.force)


def dispatch(args: argparse.Namespace, home: str) -> Dict[str, Any]:
    if args.command == "inventory":
        return _inventory_value(inventory.take_snapshot(home), home)
    if args.command == "rotate":
        return _run_rotate(args, home)
    if args.command == "status":
        now = time.time()
        return status.build_status(inventory.take_snapshot(home, now), state.read_json(home, state.RUN_FILE), now)
    return _run_edit(args, home)


def _plain(command: str, value: Dict[str, Any]) -> List[str]:
    if command == "status":
        return [value["summary"]] + ["  {}: {}".format(f["label"], f["value"] or "") for f in value["fields"]]
    if command == "rotate":
        lines = ["rotated {} ({} bytes)".format(item["path"], item["bytes_before"]) for item in value["rotated"]]
        lines += ["skipped {}: {}".format(item["path"], item["reason"]) for item in value["skipped"]]
        return lines + ["error {}: {}".format(item["path"], item["error"]) for item in value["errors"]] or ["nothing to rotate"]
    totals = value["totals"]
    lines = ["revision {}  {} logs, {} bytes live, {} bytes archived".format(
        value["revision"], totals["files"], totals["bytes"], totals["archived_bytes"])]
    lines += ["{:>12}  {:<8} {}".format(log["bytes"], log["state"], log["path"]) for log in value["logs"]]
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        value = dispatch(args, eligibility.real_home())
        code = EXIT_ERROR if args.command == "rotate" and value["errors"] else EXIT_OK
    except LogRotateError as error:
        value, code = {"error": error.message, **error.details}, error.code
    except (OSError, ValueError) as error:
        value, code = {"error": str(error)}, EXIT_ERROR
    if "error" in value and args.command == "status":
        value = status.error_status(value["error"])
    if args.json:
        print(json.dumps(value))
    elif "error" in value:
        print(value["error"], file=sys.stderr)
    else:
        print("\n".join(_plain(args.command, value)))
    return code
