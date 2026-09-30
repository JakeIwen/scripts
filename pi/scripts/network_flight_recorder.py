#!/usr/bin/env python3
"""Passive van network evidence collector, interval report and incident export."""

import argparse
import datetime as dt
import json
import sqlite3
import sys
import time
import traceback

from network_recorder import Store, report, export_incident
from network_recorder.collector import Collector, FileImporter
from network_recorder.parsers import epoch
from network_recorder.store import DEFAULT_DATABASE
from network_recorder.storage import (
    DEFAULT_CONFIG,
    load_config,
    resolve_database,
    run_storage,
    write_storage_command,
)
import os
from pathlib import Path


def iso(timestamp):
    return (
        dt.datetime.fromtimestamp(timestamp, dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="auto")
    parser.add_argument("--storage-config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "storage-sync", help="drain volatile evidence to verified flash without probes"
    )
    for name in ("report", "export", "status"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--json", action="store_true")
        cmd.add_argument("--limit", type=int, default=200 if name != "export" else 500)
        cmd.add_argument("--hours", type=float, default=6)
        cmd.add_argument("--start", type=epoch)
        cmd.add_argument("--end", type=epoch)
        cmd.add_argument("--uplink")
        cmd.add_argument("--device")
        cmd.add_argument("--search")
        if name == "export":
            cmd.add_argument("--incident", required=True)
    for name in ("run", "once", "import", "init"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--max-events", type=int, default=150000)
        cmd.add_argument("--max-bytes", type=int, default=256 * 1024 * 1024)
        cmd.add_argument("--retention-days", type=float, default=30)
        if name in ("run", "once"):
            cmd.add_argument("--log-dir", default="/var/log/openwrt")
            cmd.add_argument(
                "--monitor-db", default="/var/lib/vanpi-monitor/events.sqlite3"
            )
            cmd.add_argument("--ubnt-target", default="ubnt@192.168.8.20")
            cmd.add_argument("--ubnt-identity", default="/home/pi/.ssh/id_rsa")
        if name == "import":
            cmd.add_argument("files", nargs="+")
    args = parser.parse_args(argv)
    try:
        configured = args.storage_config or os.environ.get(
            "VANPI_NETWORK_STORAGE_CONFIG", DEFAULT_CONFIG
        )
        if args.command == "storage-sync":
            run_storage(load_config(configured), sync_only=True)
            return 0
        if args.command in ("init", "import") and args.database == "auto":
            write_storage_command(
                load_config(configured), args.command, getattr(args, "files", ())
            )
            return 0
        if args.command in ("run", "once") and args.database == "auto":
            run_storage(load_config(configured), once=args.command == "once")
            return 0
        args.database = resolve_database(args.database, args.storage_config)
        if args.command in ("report", "export", "status"):
            end = time.time() if args.end is None else args.end
            start = end - args.hours * 3600 if args.start is None else args.start
            data = (
                export_incident(args.database, args.incident, limit=args.limit)
                if args.command == "export"
                else report(
                    args.database,
                    start,
                    end,
                    limit=args.limit,
                    uplink=args.uplink,
                    device=args.device,
                    search=args.search,
                )
            )
            if args.json or args.command == "export":
                print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
            else:
                print(
                    f"Network evidence: {data['counts']['events']} events, {data['counts']['incidents']} incidents; {iso(start)}..{iso(end)}"
                )
                for item in data["coverage"]:
                    print(f"  {item['source']}: {item['status']} — {item['detail']}")
                for item in data["incidents"]:
                    print(
                        f"{item['id']} {iso(item['onset'])} {item['status']} {','.join(item['uplinks']) or item['device']}: {item['summary']}"
                    )
                    print("  Observation: " + " ".join(item["observations"]))
                    print("  Hypothesis: " + " ".join(item["hypotheses"]))
                for item in data["events"]:
                    print(
                        f"  [{item['id']}] {iso(item['time'])} {item['device']}/{item['stream']} {item['message']}"
                    )
                print("Limitations: " + " ".join(data["limitations"]))
        else:
            store = Store(
                args.database,
                max_events=args.max_events,
                max_bytes=args.max_bytes,
                retention_days=args.retention_days,
            )
            try:
                if args.command == "import":
                    importer = FileImporter(store)
                    for path in args.files:
                        while True:
                            result = importer.import_file(path)
                            if result["complete"] or not result.get("progress"):
                                break
                elif args.command in ("run", "once"):
                    collector = Collector(
                        store,
                        log_dir=args.log_dir,
                        monitor_db=args.monitor_db,
                        ubnt_target=args.ubnt_target,
                        ubnt_identity=args.ubnt_identity,
                    )
                    collector.run() if args.command == "run" else collector.once()
            finally:
                store.close()
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        # Report bounded generic errors; paths/command data may contain secrets.
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        print(
            json.dumps(
                dict(
                    ok=False,
                    error="Network evidence unavailable or request invalid",
                    error_type=type(exc).__name__,
                    location=dict(
                        file=os.path.basename(frame.filename),
                        function=frame.name,
                        line=frame.lineno,
                    ),
                )
            ),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
