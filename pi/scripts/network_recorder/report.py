"""Read-only, bounded reports. Counts never depend on the displayed row limit."""

import json
import time

from .parsers import safe_object
from .store import connect_readonly, CONTINUITY_SECONDS, SCHEMA_VERSION

LIMITATIONS = [
    "UDP syslog is best-effort: missing messages and exact loss counts cannot be inferred.",
    "Legacy OpenWrt time is Pi receipt time, not source occurrence time. RFC3164 source year/timezone are receiver-inferred and its clock is unverified; receipt ordering is not proof of device ordering.",
    "Incidents are observation episodes: same stream/session/domain, at most 180 seconds between failures or recovery. UTC day boundaries split episodes; gaps and unknown/down observations do not prove recovery.",
    "Durations measure observation times, not exact outage duration. Correlation and nearby context do not establish a cause.",
    "HTTPS uses mwan3 device and socket marking; DNS uses the shared router resolver. Curl timeout alone cannot distinguish DNS, TLS, routing, or destination failure.",
    "Clientwan ICMP uses the existing interface-bound probe semantics. Neither those two targets nor two HTTPS destinations establish provider-wide availability.",
    "Pi/router reachability and retained AP logs do not prove every Wi-Fi client can associate or browse. Silent clients, IPv6, VPNs and arbitrary applications are not tested.",
    "UBNT log times are approximately anchored from boot ID and uptime; first capture and delayed imports are marked backfill. Polling can miss records removed by rotation or reboot.",
    "Reports use retained evidence only. Details/exports cap evidence rows; counts and explicit truncation describe omissions. Retention can truncate an episode's onset.",
]
TIME_SEMANTICS = dict(
    units="Unix seconds, UTC; presentation may use ISO8601 offsets",
    time="Timeline coordinate: original Pi syslog receipt, Pi event wall time, or approximate UBNT uptime anchor; never historical import time",
    source_time="Device-reported time when retained; nullable and unverified for classic syslog",
    received_at="Original Pi receipt for syslog, polling receipt for antenna; nullable when the original receipt is unavailable",
    imported_at="Recorder database ingestion wall time, separate from occurrence/receipt",
    ordering="Monotonic sequence within a known boot is strongest; no exact cross-device order is claimed",
)
DESCRIPTIONS = {
    "upstream-tests": (
        "External reachability tests failed while the gateway answered",
        "The retained clientwan ICMP observation distinguishes gateway response from one or both external test failures.",
        "Loss beyond the gateway is plausible; routing, destination behavior and provider scope remain undetermined.",
    ),
    "gateway-hotspot": (
        "Gateway or upstream association failure evidence",
        "The gateway probe or upstream Wi-Fi log recorded a failure.",
        "The gateway/hotspot path is a candidate; this does not establish a local client's authentication failure.",
    ),
    "local-wifi": (
        "Local Wi-Fi association, authentication or AP interruption",
        "An AP log records a client or radio transition/failure.",
        "Client authentication/radio state is a candidate. Other clients may still have working connections.",
    ),
    "dns": (
        "Name resolution failure evidence",
        "Curl exit 6 or a resolver fault was recorded.",
        "The shared router resolver or its upstream path may be involved; this is not an isolated per-uplink DNS test.",
    ),
    "https": (
        "HTTPS test failure",
        "One or both configured HTTPS tests failed.",
        "The evidence does not by itself distinguish DNS, connect, TLS, destination or uplink failure.",
    ),
    "mwan3": (
        "mwan3 tracking or failover transition",
        "mwan3 logged an offline/disconnected decision.",
        "This is a tracking/policy observation, not independent proof that the provider failed.",
    ),
    "ubnt": (
        "Antenna selection or connection failure",
        "The antenna manager recorded a failure or unsuccessful selection.",
        "The antenna/upstream connection is a candidate; approximate timing limits cross-device ordering.",
    ),
    "monitoring": (
        "Monitoring availability or Pi resource evidence",
        "The collector/system monitor recorded an interruption or resource fault.",
        "Missing network evidence may be explained by monitoring availability; internet state remains undetermined.",
    ),
}


def event_dict(row):
    value = dict(row)
    value["record_id"] = value.pop("fingerprint", None)
    value["provenance"] = json.loads(value["provenance"])
    value["backfill"] = bool(value["backfill"])
    return safe_object(value)


def incident_dict(row, now):
    summary, observation, hypothesis = DESCRIPTIONS.get(
        row["domain"],
        ("Failure observation", "A failure was observed.", "Cause undetermined."),
    )
    status = (
        "recovered"
        if row["end"] is not None
        else (
            "ongoing"
            if row["domain"] not in ("monitoring", "ubnt")
            and row["stream"] != "shared-resolver"
            and not row["closed"]
            and 0 <= now - row["latest"] <= CONTINUITY_SECONDS
            else "unknown-end"
        )
    )
    return dict(
        id=row["id"],
        onset=row["onset"],
        end=row["end"],
        status=status,
        duration_seconds=row["end"] - row["onset"] if row["end"] is not None else None,
        latest_evidence_at=row["latest"],
        domains=[row["domain"]],
        uplinks=[row["uplink"]] if row["uplink"] else [],
        device=row["device"],
        summary=summary,
        observations=[observation],
        hypotheses=[hypothesis],
        evidence_ids=list(dict.fromkeys([row["first_id"], row["last_id"]])),
        evidence_count=row["evidence_count"],
        partition="UTC day; 180-second continuity limit",
    )


def source_coverage(db, now):
    result = []
    rows = db.execute(
        "SELECT source,device,MAX(time) AS last_event_at,MAX(received_at) AS last_received_at FROM events GROUP BY source,device"
    ).fetchall()
    for row in rows:
        item = dict(row)
        at = item["last_received_at"] or item["last_event_at"]
        item.update(
            status="current" if at and 0 <= now - at <= 180 else "stale",
            detail="Last retained evidence; sparse event sources have no periodic liveness guarantee.",
        )
        result.append(item)
    for row in db.execute("SELECT * FROM coverage"):
        item = dict(row)
        item["last_event_at"] = next(
            (x["last_event_at"] for x in result if x["source"] == item["source"]), None
        )
        if not 0 <= now - item["checked_at"] <= 180:
            item["status"] = "stale"
        result = [x for x in result if x["source"] != item["source"]]
        result.append(item)
    # Per-probe freshness must not be hidden by unrelated hostapd/mwan chatter.
    for row in db.execute(
        "SELECT stream,device,MAX(time) AS at FROM events WHERE kind IN ('path-state','path-summary','https-state') GROUP BY stream,device"
    ):
        result.append(
            dict(
                source=row["stream"],
                device=row["device"],
                last_event_at=row["at"],
                last_received_at=row["at"],
                status="current" if 0 <= now - row["at"] <= 180 else "stale",
                detail="Periodic existing probe evidence; current means recently observed, not healthy.",
            )
        )
    for source, device in (
        ("openwrt", "OpenWrt"),
        ("ubnt-manager", "ubnt"),
        ("system-monitor", "vanpi"),
        ("recorder", "vanpi"),
    ):
        if not any(x["source"] == source for x in result):
            result.append(
                dict(
                    source=source,
                    device=device,
                    status="unavailable",
                    last_event_at=None,
                    last_received_at=None,
                    detail="No retained evidence or collector status for this source.",
                )
            )
    return result


def report(
    path, start, end, limit=200, uplink=None, device=None, search=None, now=None
):
    now = time.time() if now is None else now
    if not 0 < end - start <= 31 * 86400 or not 1 <= limit <= 500:
        raise ValueError("range must be positive and <=31 days; limit 1..500")
    if any(
        value is not None and len(value) > maximum
        for value, maximum in ((uplink, 128), (device, 128), (search, 200))
    ):
        raise ValueError("filter too long")
    db = connect_readonly(path)
    try:
        db.execute("BEGIN")
        where, params = ["time>=?", "time<=?"], [start, end]
        iw, ip = ["onset<=?", "COALESCE(end,latest+180)>=?"], [end, start]
        for key, value in (("uplink", uplink), ("device", device)):
            if value:
                where.append(key + "=?")
                params.append(value)
                iw.append(key + "=?")
                ip.append(value)
        if search:
            where.append("message LIKE ? ESCAPE '\\'")
            term = (
                "%"
                + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
            )
            params.append(term)
            iw.append(
                "EXISTS(SELECT 1 FROM events e WHERE e.stream=incidents.stream AND e.session=incidents.session AND e.time BETWEEN onset AND COALESCE(end,latest) AND e.message LIKE ? ESCAPE '\\')"
            )
            ip.append(term)
        ew, iw = " AND ".join(where), " AND ".join(iw)
        event_count = db.execute(
            "SELECT count(*) FROM events WHERE " + ew, params
        ).fetchone()[0]
        incident_count = db.execute(
            "SELECT count(*) FROM incidents WHERE " + iw, ip
        ).fetchone()[0]
        events = [
            event_dict(r)
            for r in db.execute(
                "SELECT * FROM events WHERE "
                + ew
                + " ORDER BY time DESC,id DESC LIMIT ?",
                params + [limit],
            )
        ]
        incidents = [
            incident_dict(r, now)
            for r in db.execute(
                "SELECT * FROM incidents WHERE " + iw + " ORDER BY onset DESC LIMIT ?",
                ip + [limit],
            )
        ]
        checkpoints = {
            r["key"]: json.loads(r["value"])
            for r in db.execute(
                "SELECT key,value FROM checkpoints WHERE key IN ('limits','retention')"
            )
        }
        return dict(
            ok=True,
            schema_version=SCHEMA_VERSION,
            generated_at=now,
            range=dict(start=start, end=end),
            coverage=source_coverage(db, now),
            events=events,
            incidents=incidents,
            counts=dict(events=event_count, incidents=incident_count),
            truncated=dict(
                events=event_count > len(events),
                incidents=incident_count > len(incidents),
            ),
            limitations=LIMITATIONS,
            time_semantics=TIME_SEMANTICS,
            retention=checkpoints,
            filters=dict(
                uplinks=[
                    r[0]
                    for r in db.execute(
                        "SELECT DISTINCT uplink FROM events WHERE uplink IS NOT NULL ORDER BY uplink"
                    )
                ],
                devices=[
                    r[0]
                    for r in db.execute(
                        "SELECT DISTINCT device FROM events ORDER BY device"
                    )
                ],
            ),
        )
    finally:
        db.close()


def export_incident(path, incident_id, limit=500, now=None):
    now = time.time() if now is None else now
    if not 1 <= limit <= 500:
        raise ValueError("limit must be 1..500")
    db = connect_readonly(path)
    try:
        db.execute("BEGIN")
        row = db.execute(
            "SELECT * FROM incidents WHERE id=?", (incident_id,)
        ).fetchone()
        if (
            row is None
            and db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='incident_aliases'"
            ).fetchone()
        ):
            row = db.execute(
                "SELECT i.* FROM incidents i JOIN incident_aliases a ON a.incident_id=i.id WHERE a.id=?",
                (incident_id,),
            ).fetchone()
        if row is None:
            raise KeyError("incident is unavailable or outside retention")
        begin, end = row["onset"] - 120, (row["end"] or row["latest"]) + 120
        # Always include the bounding evidence, even when a burst fills the context budget.
        evidence = [
            event_dict(r)
            for r in db.execute(
                "SELECT * FROM events WHERE id IN (?,?) ORDER BY time,id",
                (row["first_id"], row["last_id"]),
            )
        ]
        remaining = max(0, limit - len(evidence))
        evidence += [
            event_dict(r)
            for r in db.execute(
                "SELECT * FROM events WHERE time BETWEEN ? AND ? AND id NOT IN (?,?) ORDER BY CASE WHEN stream=? THEN 0 ELSE 1 END,time,id LIMIT ?",
                (begin, end, row["first_id"], row["last_id"], row["stream"], remaining),
            )
        ]
        total = db.execute(
            "SELECT count(*) FROM events WHERE time BETWEEN ? AND ?", (begin, end)
        ).fetchone()[0]
        evidence.sort(key=lambda e: (e["time"], e["id"]))
        evidence = evidence[:limit]
        return dict(
            ok=True,
            schema_version=SCHEMA_VERSION,
            generated_at=now,
            range=dict(start=begin, end=end),
            incident=incident_dict(row, now),
            incidents=[incident_dict(row, now)],
            events=evidence,
            coverage=source_coverage(db, now),
            counts=dict(events=total, incidents=1),
            truncated=dict(events=total > len(evidence)),
            limitations=LIMITATIONS,
            time_semantics=TIME_SEMANTICS,
            export_semantics="Redacted evidence + nearby context (120 seconds each side); no browsing payloads. Context is not causal attribution.",
        )
    finally:
        db.close()
