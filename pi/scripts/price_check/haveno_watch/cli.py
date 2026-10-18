"""Command-line registration and locking for Haveno watches."""

from __future__ import annotations

import fcntl
import os
from dataclasses import dataclass
from pathlib import Path

from .service import HavenoWatchError, check_watches, validate_watch
from .store import HavenoStore


HAVENO_COMMANDS = {
    "haveno-add",
    "haveno-edit",
    "haveno-remove",
    "haveno-mute",
    "haveno-check",
}


@dataclass(frozen=True)
class CommandResult:
    extra: dict
    message: str | None
    status: int = 0


def _watch_options(parser) -> None:
    parser.add_argument("threshold_pct")
    parser.add_argument("--fee-pct", default="0")
    parser.add_argument("--payment-method", default="ZELLE")
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--min-xmr")
    parser.add_argument("--max-xmr")
    parser.add_argument("--title", default="")


def register_subcommands(commands) -> None:
    add = commands.add_parser("haveno-add", help="add a Haveno XMR offer watch")
    _watch_options(add)
    edit = commands.add_parser("haveno-edit", help="replace a Haveno XMR offer watch")
    edit.add_argument("id", type=int)
    _watch_options(edit)
    remove = commands.add_parser("haveno-remove", help="remove a Haveno watch")
    remove.add_argument("id", type=int)
    mute = commands.add_parser(
        "haveno-mute", help="mute a Haveno watch for a number of days; 0 unmutes"
    )
    mute.add_argument("id", type=int)
    mute.add_argument("days", type=int)
    check = commands.add_parser("haveno-check", help="check one Haveno watch or all")
    check.add_argument("target", nargs="?", default="all")
    check.add_argument(
        "--no-notify", action="store_true", help="record offers without notifying"
    )


def _configuration(args) -> dict:
    return validate_watch(
        args.threshold_pct,
        fee_pct=args.fee_pct,
        payment_method=args.payment_method,
        currency=args.currency,
        min_xmr=args.min_xmr,
        max_xmr=args.max_xmr,
        title=args.title,
    )


def handle_management(args, store: HavenoStore) -> CommandResult | None:
    if args.command == "haveno-add":
        watch = store.add_watch(_configuration(args))
        return CommandResult(
            {"haveno_watch": watch}, f"watching {watch['display_title']}"
        )
    if args.command == "haveno-edit":
        watch = store.update_watch(args.id, _configuration(args))
        return CommandResult(
            {"haveno_watch": watch}, f"updated Haveno watch: {watch['display_title']}"
        )
    if args.command == "haveno-remove":
        watch = store.remove_watch(args.id)
        return CommandResult(
            {"removed_haveno_watch": watch},
            f"removed Haveno watch: {watch['display_title']}",
        )
    if args.command == "haveno-mute":
        watch = store.set_notification_mute(args.id, args.days)
        action = (
            "unmuted"
            if args.days == 0
            else f"muted for {args.days} {'day' if args.days == 1 else 'days'}"
        )
        return CommandResult(
            {"haveno_watch": watch},
            f"{action} Haveno watch: {watch['display_title']}",
        )
    return None


def handle_command(
    args,
    store: HavenoStore,
    *,
    db_path: Path,
    fetcher,
    notify_sender,
) -> CommandResult | None:
    result = handle_management(args, store)
    if result is not None or args.command != "haveno-check":
        return result
    watches = select_watches(store, args.target)
    checked, errors, _ = run_locked_checks(
        db_path,
        store,
        watches,
        fetcher=fetcher,
        notify_sender=notify_sender,
        notify=not args.dry_run and not args.no_notify,
        record=not args.dry_run,
        skip_locked=False,
    )
    message = "; ".join(error["message"] for error in errors) or None
    return CommandResult(
        {"haveno_checked": checked, "check_errors": errors},
        message,
        1 if errors else 0,
    )


def select_watches(store: HavenoStore, target: str) -> list[dict]:
    if target == "all":
        return store.list_watches()
    try:
        watch_id = int(target)
    except ValueError as error:
        raise HavenoWatchError(
            "haveno-check target must be a watch ID or 'all'"
        ) from error
    if watch_id <= 0:
        raise HavenoWatchError("haveno-check target must be a positive watch ID or 'all'")
    return [store.get_watch(watch_id)]


def run_locked_checks(
    db_path: Path,
    store: HavenoStore,
    watches: list[dict],
    *,
    fetcher,
    notify_sender,
    notify: bool,
    record: bool,
    skip_locked: bool,
) -> tuple[list[dict], list[dict], bool]:
    lock_path = db_path.with_suffix(".haveno.lock")
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(lock_path.parent, 0o700)
    with lock_path.open("a", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            if skip_locked:
                return [], [], True
            raise HavenoWatchError("another Haveno check is already running") from error
        checked, errors = check_watches(
            store,
            watches,
            fetcher=fetcher,
            notify_sender=notify_sender,
            notify=notify,
            record=record,
        )
    return checked, errors, False


def run_combined_checks(
    args,
    store: HavenoStore,
    *,
    command: str,
    fetcher,
    notify_sender,
) -> tuple[list[dict], list[dict], bool]:
    if command != "run" and not (command == "check" and args.target == "all"):
        return [], [], False
    return run_locked_checks(
        args.db,
        store,
        store.list_watches(),
        fetcher=fetcher,
        notify_sender=notify_sender,
        notify=not args.dry_run,
        record=not args.dry_run,
        skip_locked=True,
    )
