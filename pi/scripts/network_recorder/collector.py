"""Passive adapters with durable physical-record checkpoints and bounded reads."""

import collections
import glob
import gzip
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path

from .parsers import epoch, observation, parse_syslog, parse_ubnt
from .store import Store


def wait_until(deadline, stopped, clock=None, sleeper=None):
    """Bounded interruptible wait, using one clock read per iteration.

    Under CPU quotas a process may be descheduled between a loop condition and
    the next clock read. Compute once and check that value before sleeping.
    """
    clock = clock or time.monotonic
    sleeper = sleeper or time.sleep
    while not stopped():
        remaining = deadline - clock()
        if remaining <= 0:
            return
        sleeper(min(0.5, remaining))


class FileImporter:
    def __init__(self, store):
        self.store = store

    def import_file(self, path, *, backfill=True, max_bytes=1024 * 1024, before=None):
        """Offsets refer to uncompressed bytes. An incomplete last line is retried.

        Generation identity is family + first complete line digest. Rename and
        gzip retain it. Equal messages at different offsets remain distinct.
        A copied archive is intentionally the same evidence, not another capture.
        """
        path = Path(path)
        opener = gzip.open if path.suffix == ".gz" else open
        stat = path.stat()
        file_key = "file-stat:" + str(path)
        signature = [stat.st_size, stat.st_mtime_ns]
        if self.store.checkpoint(file_key) == signature:
            return dict(inserted=0, read_bytes=0, complete=True)
        with opener(path, "rb") as handle:
            first = handle.readline(16385)
            if not first.endswith(b"\n") and len(first) < 16385:
                return dict(inserted=0, read_bytes=0, complete=False)
            family = "json" if first.lstrip().startswith(b"{") else "legacy"
            generation = family + ":" + hashlib.sha256(first).hexdigest()
            key = "file-offset:" + generation
            checkpoint = self.store.checkpoint(key, {})
            offset = checkpoint.get("offset", 0)
            # Copytruncate with the same header is unusual but must start a new
            # physical generation if the previously acknowledged offset vanished.
            if path.suffix != ".gz" and stat.st_size < offset:
                generation += ":truncate:" + str(stat.st_mtime_ns)
                key = "file-offset:" + generation
                checkpoint = self.store.checkpoint(key, {})
                offset = checkpoint.get("offset", 0)
            handle.seek(offset)
            rows = []
            skipped = checkpoint.get("skipped_lines", 0)
            read_bytes = 0
            last = checkpoint.get("last_time")
            session = checkpoint.get("session", "receiver-unverified")
            inherit_session = not checkpoint
            lineage_key = "receiver-lineage:" + family
            lineage = self.store.checkpoint(lineage_key, {})
            discarding = checkpoint.get("discarding", False)
            complete = False
            imported_at = time.time()
            while read_bytes < max_bytes:
                position = handle.tell()
                raw = handle.readline(16385)
                if not raw:
                    complete = True
                    break
                read_bytes += len(raw)
                if discarding:
                    discarding = not raw.endswith(b"\n")
                    continue
                if not raw.endswith(b"\n"):
                    if len(raw) <= 16384:
                        handle.seek(position)
                        break
                    # Overlong physical lines are skipped up to a newline in
                    # bounded chunks; never parse a suffix as another message.
                    discarding = True
                    skipped += 1
                    continue
                event = parse_syslog(
                    raw.decode("utf-8", "replace"),
                    imported_at=imported_at,
                    provenance=dict(
                        file=str(path), generation=generation, offset=position
                    ),
                    backfill=backfill,
                )
                if event is None:
                    continue
                event["backfill"] = backfill or imported_at - event["received_at"] > 120
                if before is not None and event["time"] >= before:
                    continue
                if inherit_session:
                    # Rotation is not a reboot. Inherit the closest preceding
                    # captured session, while older backfill cannot overwrite a
                    # current generation's durable clock checkpoint.
                    if lineage and (
                        not backfill or event["time"] >= lineage["last_time"] - 2
                    ):
                        session = lineage["session"]
                    else:
                        predecessor = self.store.db.execute(
                            "SELECT session FROM events WHERE source='openwrt' AND time<=? ORDER BY time DESC,id DESC LIMIT 1",
                            (event["time"],),
                        ).fetchone()
                        if predecessor:
                            session = predecessor[0]
                    inherit_session = False
                if last is not None and event["time"] < last - 2:
                    previous_session = session
                    session = (
                        "receiver-clock-boundary:" + generation + ":" + str(position)
                    )
                    rows.append(
                        observation(
                            "recorder",
                            "vanpi",
                            "Receiver clock moved backward; incident continuity reset",
                            event["time"],
                            dict(
                                generation=generation,
                                offset=position,
                                marker="clock",
                                previous_session=previous_session,
                                previous_time=last,
                            ),
                            domain="monitoring",
                            kind="receiver-clock-boundary",
                            session=session,
                        )
                    )
                if (
                    event["kind"] == "router-kernel"
                    and "Linux version" in event["message"]
                ):
                    session = "router-boot-observed:" + generation + ":" + str(position)
                last = event["time"]
                event["session"] = session
                rows.append(event)
            next_checkpoint = dict(
                offset=handle.tell(),
                last_time=last,
                session=session,
                discarding=discarding,
                skipped_lines=skipped,
            )
        checkpoints = {key: next_checkpoint}
        if last is not None and (
            not lineage
            or generation == lineage.get("generation")
            or last >= lineage["last_time"]
            or not backfill
        ):
            checkpoints[lineage_key] = dict(
                generation=generation, last_time=last, session=session
            )
        if complete:
            checkpoints[file_key] = signature
        inserted = self.store.ingest(rows, checkpoints=checkpoints)
        return dict(
            inserted=inserted,
            read_bytes=read_bytes,
            complete=complete,
            last_time=last,
            progress=next_checkpoint["offset"] > offset,
        )


class Collector:
    def __init__(
        self,
        store,
        log_dir="/var/log/openwrt",
        monitor_db="/var/lib/vanpi-monitor/events.sqlite3",
        ubnt_target="ubnt@192.168.8.20",
        ubnt_identity="/home/pi/.ssh/id_rsa",
        interval=5,
    ):
        self.store, self.log_dir, self.monitor_db = store, log_dir, monitor_db
        self.ubnt_target, self.ubnt_identity, self.interval = (
            ubnt_target,
            ubnt_identity,
            interval,
        )
        self.files = FileImporter(store)
        self.stopped = False
        self.session = str(uuid.uuid4())
        self.boot = (
            Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            if Path("/proc/sys/kernel/random/boot_id").exists()
            else self.session
        )
        self.last_slow = 0
        self.last_prune = 0
        self.first_heartbeat = True

    def logs(self):
        # Read bounded spool in generation order. Future structured captures
        # supersede legacy copies from the same rsyslog receiver.
        structured = sorted(
            glob.glob(self.log_dir + "/network.jsonl*"),
            key=lambda p: os.stat(p).st_mtime,
        )
        cutover = self.store.checkpoint("structured-cutover")
        if structured and cutover is None:
            for path in structured:
                with open(path, "rb") as handle:
                    line = handle.readline(16385)
                try:
                    cutover = epoch(json.loads(line)["received_at"])
                except (ValueError, KeyError, TypeError):
                    continue
                if cutover is not None:
                    self.store.set_checkpoint("structured-cutover", cutover)
                    self.store.db.commit()
                    break
        legacy = sorted(
            glob.glob(self.log_dir + "/dendelion.log*"),
            key=lambda p: os.stat(p).st_mtime,
        )
        # Keep current capture responsive while older compressed generations
        # catch up under the Pi's CPU quota. This is an input budget, not a loss
        # estimate: unread records remain at their durable offsets.
        active = self.log_dir + "/network.jsonl"
        paths = (
            ([active] if active in structured else [])
            + [p for p in structured if p != active]
            + legacy
        )
        budget = 512 * 1024
        for path in paths:
            if budget <= 0:
                break
            is_legacy = "/dendelion.log" in path
            result = self.files.import_file(
                path,
                backfill=os.stat(path).st_mtime < time.time() - 120,
                max_bytes=min(128 * 1024, budget),
                before=cutover if is_legacy else None,
            )
            budget -= result["read_bytes"]
        pending = sum(
            self.store.checkpoint("file-stat:" + path)
            != [os.stat(path).st_size, os.stat(path).st_mtime_ns]
            for path in paths
        )
        self.store.coverage(
            "historical-import",
            "vanpi",
            "unknown" if pending else "current",
            (
                f"{pending} of {len(paths)} source files have unread or partial records; interval history may be incomplete until catch-up. Counts describe imported, retained evidence only."
                if pending
                else "All current source generations checkpointed; rotated-away records and UDP loss remain unknown."
            ),
        )
        recent = self.store.db.execute(
            "SELECT MAX(received_at) FROM events WHERE source='openwrt'"
        ).fetchone()[0]
        self.store.coverage(
            "openwrt",
            "OpenWrt",
            "current" if recent and 0 <= time.time() - recent < 180 else "stale",
            "Passive UDP receiver; bounded importer, probe freshness listed separately. No delivery/loss guarantee.",
            recent,
        )

    def monitor(self):
        uri = Path(self.monitor_db).absolute().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            checkpoint = self.store.checkpoint("system-monitor-id")
            maximum = db.execute("SELECT MAX(id) FROM events").fetchone()[0] or 0
            categories = "('power','thermal','throttle','resource','service','system','monitor','oom','kernel')"
            if checkpoint is None:
                # Covering index filters before fetching message/state. Startup
                # context is capped; do not copy the multi-GB USB event corpus.
                rows = db.execute(
                    "SELECT id,timestamp,boot_id,category,kind,severity,summary,message FROM events WHERE id IN (SELECT id FROM events INDEXED BY events_report_idx WHERE timestamp>=? AND category IN "
                    + categories
                    + " ORDER BY timestamp DESC LIMIT 2000) ORDER BY id",
                    (time.time() - 30 * 86400,),
                ).fetchall()
                next_id = maximum
            else:
                rows = db.execute(
                    "SELECT id,timestamp,boot_id,category,kind,severity,summary,message FROM events WHERE id>? AND category IN "
                    + categories
                    + " ORDER BY id LIMIT 2000",
                    (checkpoint,),
                ).fetchall()
                next_id = rows[-1]["id"] if len(rows) == 2000 else maximum
            events = []
            for row in rows:
                if row["category"] not in (
                    "power",
                    "thermal",
                    "throttle",
                    "resource",
                    "service",
                    "system",
                    "monitor",
                    "oom",
                    "kernel",
                ):
                    continue
                events.append(
                    observation(
                        "system-monitor",
                        "vanpi",
                        row["summary"] + ": " + row["message"],
                        row["timestamp"],
                        dict(
                            database="system-event-monitor",
                            row_id=row["id"],
                            boot_id=row["boot_id"],
                            source_time=row["timestamp"],
                        ),
                        source_time=row["timestamp"],
                        received_at=None,
                        time_quality="Pi source-wall-time; original receipt unavailable",
                        boot_id=row["boot_id"],
                        session=row["boot_id"] or "unknown",
                        kind=row["kind"],
                        domain="monitoring",
                        stream="system:" + row["kind"],
                        state=(
                            "failure"
                            if row["severity"] in ("warning", "critical")
                            else "info"
                        ),
                        severity=row["severity"],
                        backfill=True,
                    )
                )
            self.store.ingest(events, {"system-monitor-id": next_id})
            sample = db.execute(
                "SELECT timestamp FROM resource_samples ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            at = sample[0] if sample else None
            self.store.coverage(
                "system-monitor",
                "vanpi",
                "current" if at and 0 <= time.time() - at < 90 else "stale",
                "Read-only existing monitor; selected power/resource/boot/service context. Initial import capped at 2000 relevant events/30 days; USB storm rows omitted.",
                at,
            )
        finally:
            db.close()

    def antenna(self):
        if not self.ubnt_target:
            self.store.coverage(
                "ubnt-manager", "ubnt", "unavailable", "UBNT polling disabled"
            )
            return
        # No manager entrypoint, ping, site survey or mutation. The bounded log
        # is in RAM; reads add no flash writes or external traffic.
        command = "cat /proc/sys/kernel/random/boot_id; cut -d ' ' -f 1 /proc/uptime; head -c 524288 /var/log/ubnt-wifi.log"
        started = time.time()
        result = subprocess.run(
            [
                "/usr/bin/ssh",
                "-n",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=4",
                "-i",
                self.ubnt_identity,
                self.ubnt_target,
                command,
            ],
            capture_output=True,
            text=True,
            timeout=12,
        )
        if result.returncode:
            raise RuntimeError("Antenna passive log read failed (SSH unavailable)")
        received = time.time()
        lines = result.stdout.splitlines()
        if len(lines) < 2 or len(lines[0]) > 40:
            raise ValueError("Antenna snapshot metadata unavailable")
        boot, uptime = lines[0], float(lines[1])
        prior = self.store.checkpoint("ubnt-snapshot", {})
        log_digest = hashlib.sha256("\n".join(lines[2:]).encode()).hexdigest()
        unchanged = prior.get("boot") == boot and prior.get("log_digest") == log_digest
        # The log's uptime + occurrence within equal-timestamp/text run survives
        # snapshots/rotation; duplicate genuine records at different lines stay distinct.
        occurrences = collections.Counter()
        events = []
        anchor = received - uptime
        if prior.get("boot") == boot and abs(anchor - prior["anchor"]) < 30:
            anchor = prior["anchor"]
        for line in ([] if unchanged else lines[2:]):
            digest = hashlib.sha256(line.encode()).hexdigest()
            occurrences[digest] += 1
            event = parse_ubnt(
                line,
                boot_id=boot,
                uptime=uptime,
                received_at=anchor + uptime,
                imported_at=received,
                provenance=dict(
                    boot_id=boot,
                    line_digest=digest,
                    occurrence=occurrences[digest],
                    file="/var/log/ubnt-wifi.log",
                ),
                uncertainty=max(5, received - started),
            )
            if event:
                events.append(event)
        if not unchanged:
            self.store.ingest(
                events,
                {
                    "ubnt-snapshot": dict(
                        boot=boot, anchor=anchor, log_digest=log_digest
                    )
                },
            )
        self.store.coverage(
            "ubnt-manager",
            "ubnt",
            "current",
            "Passive uptime-anchored log snapshot; manager healthy heartbeat is hourly. Clock anchor approximate; removed records may be lost.",
            received,
        )

    def heartbeat(self):
        now = time.time()
        previous = self.store.checkpoint("recorder-heartbeat")
        events = []
        if previous and (now - previous["time"] > 120 or now < previous["time"] - 2):
            events.append(
                observation(
                    "recorder",
                    "vanpi",
                    "Recorder heartbeat gap or clock correction; network state during missing coverage is unknown",
                    now,
                    dict(session=self.session, marker="gap", sequence=now),
                    domain="monitoring",
                    kind="collector-gap",
                    state="failure",
                    stream="recorder-availability",
                    session=self.session,
                    boot_id=self.boot,
                )
            )
        events.append(
            observation(
                "recorder",
                "vanpi",
                (
                    "Recorder process started; source coverage is evaluated separately"
                    if self.first_heartbeat
                    else "Recorder running; source coverage is evaluated separately"
                ),
                now,
                dict(session=self.session, marker="heartbeat", sequence=now),
                kind=(
                    "collector-start" if self.first_heartbeat else "collector-heartbeat"
                ),
                stream="recorder-availability",
                session=self.session,
                boot_id=self.boot,
                monotonic=time.monotonic(),
            )
        )
        self.store.ingest(
            events,
            {
                "recorder-heartbeat": dict(
                    time=now, session=self.session, boot=self.boot
                )
            },
        )
        self.store.coverage(
            "recorder",
            "vanpi",
            "current",
            "Collector heartbeat; independent of dashboard. A current collector does not imply working internet.",
            now,
        )
        self.first_heartbeat = False

    def once(self, slow=True):
        for name, device, action in [("openwrt", "OpenWrt", self.logs)] + (
            [
                ("system-monitor", "vanpi", self.monitor),
                ("ubnt-manager", "ubnt", self.antenna),
            ]
            if slow
            else []
        ):
            try:
                action()
            except (
                OSError,
                ValueError,
                sqlite3.Error,
                RuntimeError,
                subprocess.TimeoutExpired,
            ):
                # No raw command output/arguments in diagnostics.
                self.store.coverage(
                    name,
                    device,
                    "unavailable",
                    "Passive source read failed; evidence unavailable. Check source access and local service status.",
                )
        if slow:
            self.heartbeat()

    def run(self):
        def stop(_signum, _frame):
            self.stopped = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while not self.stopped:
            started = time.monotonic()
            slow = started - self.last_slow >= 60
            self.once(slow)
            if slow:
                self.last_slow = started
            if started - self.last_prune >= 3600:
                self.store.prune()
                self.last_prune = started
            deadline = started + self.interval
            wait_until(deadline, lambda: self.stopped)
