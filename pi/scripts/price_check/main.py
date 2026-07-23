#!/usr/bin/python3
"""Manage private product listings and check their current prices."""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

from amazon_parser import AmazonParseError, Product, parse as parse_amazon
from cron_schedule import CronScheduleError, CronScheduleManager
from store import PriceStore, StoreError, normalized_url


DEFAULT_DB = Path(
    os.environ.get(
        "PRICE_CHECK_DB", "/home/pi/.local/share/price_check/price_check.sqlite3"
    )
)
DEFAULT_NTFY_SENDER = "/home/pi/scripts/ntfy_send.sh"
CURL = "/usr/bin/curl"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "Chrome/126.0 Safari/537.36"
)
PARSERS = {"amazon": parse_amazon}


class PriceCheckError(RuntimeError):
    pass


def parse_money(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise PriceCheckError(f"invalid dollar amount {text!r}") from error
    if value <= 0 or value * 100 != (value * 100).to_integral_value():
        raise PriceCheckError("dollar amount must be positive with at most two decimals")
    return value


def validate_listing(parser: str, threshold: str, url: str, title: str = "") -> tuple:
    if parser not in PARSERS:
        raise PriceCheckError(f"unknown parser {parser!r}")
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise PriceCheckError(f"invalid URL {url!r}")
    if len(url) > 4096:
        raise PriceCheckError("URL is too long")
    if any(character in title for character in ("\t", "\r", "\n")):
        raise PriceCheckError("title cannot contain tabs or newlines")
    if len(title) > 160:
        raise PriceCheckError("title must be 160 characters or fewer")
    return parser, parse_money(threshold), url, title.strip() or None


def fetch(url: str) -> str:
    command = [
        CURL,
        "-LfsS",
        "--compressed",
        "--connect-timeout",
        "10",
        "--max-time",
        "30",
        "--retry",
        "2",
        "--retry-delay",
        "2",
        "-A",
        USER_AGENT,
        "-H",
        "Accept-Language: en-US,en;q=0.9",
        url,
    ]
    try:
        result = subprocess.run(
            command, check=True, capture_output=True, text=True, encoding="utf-8"
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise PriceCheckError(f"download failed: {detail.strip()}") from error
    return result.stdout


def send_ntfy(title: str, message: str, priority: str, tags: str) -> None:
    sender = os.environ.get("NTFY_SEND_BIN", DEFAULT_NTFY_SENDER)
    env = os.environ.copy()
    env["NTFY_TOPIC_VAR"] = "NTFY_PRICE_URL"
    try:
        subprocess.run([sender, title, message, priority, tags], check=True, env=env)
    except (OSError, subprocess.CalledProcessError) as error:
        raise PriceCheckError(f"notification failed: {error}") from error


def send_price_alert(item: dict, product: Product) -> None:
    title = f"{item['display_title']}: ${product.price:.2f} < ${item['threshold']}"
    send_ntfy(title, item["url"], "high", "moneybag")


def send_parser_error(item: dict, error: AmazonParseError) -> None:
    title = f"{item['display_title']}: price parser needs update"
    message = f"Could not parse the product page HTML.\n{error}\n{item['url']}"
    send_ntfy(title, message, "high", "warning")


def check_item(
    store: PriceStore, item: dict, *, notify: bool = True, record: bool = True
) -> dict:
    try:
        product = PARSERS[item["parser"]](fetch(item["url"]))
    except AmazonParseError as error:
        failed_item = item
        if record:
            failed_item = store.record_error(item["id"], str(error))
        if notify and not failed_item["notifications_muted"]:
            try:
                send_parser_error(failed_item, error)
            except PriceCheckError as notify_error:
                raise PriceCheckError(
                    f"{error}; parser-error notification failed: {notify_error}"
                ) from notify_error
        raise PriceCheckError(str(error)) from error
    except PriceCheckError as error:
        if record:
            store.record_error(item["id"], str(error))
        raise

    if record:
        updated = store.record_success(item["id"], product.title, product.price)
    else:
        updated = {
            **item,
            "last_price": f"{product.price:.2f}",
            "last_price_cents": int(product.price * 100),
            "last_title": product.title,
            "display_title": item["title"] or product.title,
            "below_threshold": product.price < Decimal(item["threshold"]),
        }
    if updated["below_threshold"] and notify and not updated["notifications_muted"]:
        send_price_alert(updated, product)
    return updated


def check_items(
    store: PriceStore, items: list[dict], *, notify: bool = True, record: bool = True
) -> tuple[list[dict], list[dict]]:
    checked, errors = [], []
    for item in items:
        try:
            updated = check_item(store, item, notify=notify, record=record)
            checked.append(updated)
            print(
                f"{updated['display_title']}: ${updated['last_price']} is "
                f"{'below' if updated['below_threshold'] else 'not below'} "
                f"${updated['threshold']}",
                file=sys.stderr,
            )
        except (PriceCheckError, StoreError) as error:
            errors.append({"id": item["id"], "message": str(error)})
            print(f"price-check: {item['url']}: {error}", file=sys.stderr)
    return checked, errors


def summary(items: list[dict]) -> dict:
    return {
        "count": len(items),
        "checked": sum(item["last_checked_at"] is not None for item in items),
        "below_threshold": sum(item["below_threshold"] is True for item in items),
        "errors": sum(item["last_status"] == "error" for item in items),
    }


def response(store: PriceStore, **extra) -> dict:
    items = store.list_items()
    return {"ok": True, "items": items, "summary": summary(items), **extra}


def migrate_tsv(store: PriceStore, path: Path) -> int:
    paths = [path, path.with_name(f"{path.stem}.local{path.suffix}")]
    disabled_path = path.with_name(f"{path.stem}.disabled")
    disabled = set()
    if disabled_path.exists():
        disabled = {
            normalized_url(line)
            for line in disabled_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
    added = 0
    for candidate in paths:
        if not candidate.exists():
            continue
        with candidate.open(encoding="utf-8", newline="") as source:
            for line_number, row in enumerate(csv.reader(source, delimiter="\t"), 1):
                if not row or row[0].lstrip().startswith("#"):
                    continue
                if len(row) not in {3, 4}:
                    raise PriceCheckError(f"{candidate}:{line_number}: invalid TSV row")
                parser, threshold, url = (value.strip() for value in row[:3])
                title = row[3].strip() if len(row) == 4 else ""
                if normalized_url(url) in disabled:
                    continue
                values = validate_listing(parser, threshold, url, title)
                try:
                    store.add_item(*values)
                    added += 1
                except StoreError as error:
                    if "already configured" not in str(error):
                        raise
    return added


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--json", action="store_true", help="emit JSON on stdout")
    parser.add_argument("--dry-run", action="store_true", help="check without notifying")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("list", help="list configured products")
    check = commands.add_parser("check", help="check one item ID or all items")
    check.add_argument("target", nargs="?", default="all")
    add = commands.add_parser("add", help="add a product")
    add.add_argument("parser")
    add.add_argument("threshold")
    add.add_argument("url")
    add.add_argument("title", nargs="?", default="")
    edit = commands.add_parser("edit", help="edit an existing product")
    edit.add_argument("id", type=int)
    edit.add_argument("parser")
    edit.add_argument("threshold")
    edit.add_argument("url")
    edit.add_argument("title", nargs="?", default="")
    mute = commands.add_parser(
        "mute", help="mute notifications for an item for a number of days; 0 unmutes"
    )
    mute.add_argument("id", type=int)
    mute.add_argument("days", type=int)
    commands.add_parser("schedule", help="show the live price-check cron schedule")
    schedule_parse = commands.add_parser(
        "schedule-parse", help="describe a cron schedule without changing it"
    )
    schedule_parse.add_argument("expression")
    schedule_set = commands.add_parser(
        "schedule-set", help="replace the live price-check cron schedule"
    )
    schedule_set.add_argument("expression")
    remove = commands.add_parser("remove", help="remove by ID, title, or URL")
    remove.add_argument("match")
    migrate = commands.add_parser("migrate-tsv", help="import the old private TSV")
    migrate.add_argument("path", type=Path)
    return parser


def emit(args, payload: dict, message: str | None = None) -> None:
    if args.json:
        print(json.dumps(payload, separators=(",", ":")))
    elif message:
        print(message)
    elif args.command == "list":
        for item in payload["items"]:
            price = f"${item['last_price']}" if item["last_price"] else "not checked"
            print(f"{item['id']}\t{item['display_title']}\t{price}\t{item['url']}")


def main() -> int:
    args = build_parser().parse_args()
    command = args.command or "run"
    try:
        schedule_manager = CronScheduleManager()
        if command == "schedule":
            schedule = schedule_manager.status()
            emit(
                args,
                {"ok": True, "schedule": schedule},
                f"{schedule['expression']} — "
                f"{schedule['description'] or schedule['error']}",
            )
            return 0
        if command == "schedule-parse":
            schedule = schedule_manager.preview(args.expression)
            emit(args, {"ok": True, "schedule": schedule})
            return 0
        if command == "schedule-set":
            lock_path = args.db.with_suffix(".schedule.lock")
            lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(lock_path.parent, 0o700)
            with lock_path.open("a", encoding="utf-8") as lock:
                os.chmod(lock_path, 0o600)
                fcntl.flock(lock, fcntl.LOCK_EX)
                schedule = schedule_manager.update(args.expression)
            emit(
                args,
                {"ok": True, "schedule": schedule},
                f"updated price-check schedule: {schedule['expression']} — "
                f"{schedule['description']}",
            )
            return 0

        with PriceStore(args.db) as store:
            if command == "list":
                emit(args, response(store))
                return 0
            if command == "add":
                values = validate_listing(args.parser, args.threshold, args.url, args.title)
                item = store.add_item(*values)
                emit(args, response(store, item=item), f"added price check: {item['display_title']}")
                return 0
            if command == "edit":
                values = validate_listing(args.parser, args.threshold, args.url, args.title)
                item = store.update_item(args.id, *values)
                emit(
                    args,
                    response(store, item=item),
                    f"updated price check: {item['display_title']}",
                )
                return 0
            if command == "mute":
                item = store.set_notification_mute(args.id, args.days)
                if args.days:
                    message = (
                        f"muted price-check notifications for "
                        f"{item['display_title']} for {args.days} "
                        f"{'day' if args.days == 1 else 'days'}"
                    )
                else:
                    message = (
                        f"unmuted price-check notifications for "
                        f"{item['display_title']}"
                    )
                emit(args, response(store, item=item), message)
                return 0
            if command == "remove":
                item = store.remove_item(args.match)
                emit(args, response(store, removed=item), f"removed price check: {item['display_title']}")
                return 0
            if command == "migrate-tsv":
                count = migrate_tsv(store, args.path)
                emit(args, response(store, migrated=count), f"migrated {count} price checks")
                return 0

            lock_path = args.db.with_suffix(".check.lock")
            lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with lock_path.open("a", encoding="utf-8") as lock:
                os.chmod(lock_path, 0o600)
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise PriceCheckError("another price check is already running") from error
                if command == "check" and args.target != "all":
                    try:
                        items = [store.get_item(int(args.target))]
                    except ValueError as error:
                        raise PriceCheckError("check target must be an item ID or 'all'") from error
                else:
                    items = store.list_items()
                checked, errors = check_items(
                    store,
                    items,
                    notify=not args.dry_run,
                    record=not args.dry_run,
                )
                payload = response(store, checked=checked, check_errors=errors)
                if errors:
                    payload["message"] = "; ".join(error["message"] for error in errors)
                emit(args, payload)
                return 1 if errors else 0
    except (OSError, CronScheduleError, PriceCheckError, StoreError) as error:
        if args.json:
            print(json.dumps({"ok": False, "message": str(error)}, separators=(",", ":")))
        else:
            print(f"price-check: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
