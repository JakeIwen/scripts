"""Small, conservative adapters for existing evidence (no probes)."""

import datetime as dt
import hashlib
import json
import math
import re
import time

try:
    from ..system_event_monitor import redact_log_message
except ImportError:
    from system_event_monitor import redact_log_message


def redact(value):
    """Redact before persistence, including fields the older crash reader omits."""
    text = str(value or "")[:16384]
    text = re.sub(r"(?is)-----BEGIN .*?PRIVATE KEY-----.*", "[redacted key]", text)
    text = re.sub(r"(?i)\b(?:https?|ftp|wss?|smb)://[^\s<>]+", "[redacted URL]", text)
    text = re.sub(
        r"(?i)\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*[:=].*",
        "[redacted authentication]",
        text,
    )
    text = re.sub(
        r"(?i)(?<!\w)(?:--(?:proxy-)?user|-u)\s+(?:\"[^\"]*\"|'[^']*'|\S+)",
        "[redacted authentication argument]",
        text,
    )
    text = re.sub(
        r"(?i)\bkey\b[\"']?\s*[=:]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        "[redacted credential]",
        text,
    )
    secret = r"(?:password|passwd|psk|wpa_psk|token|access_token|refresh_token|secret|api[_-]?key|credential)"
    text = re.sub(
        r"(?i)(?:--)?\b"
        + secret
        + r"\b[\"']?\s*(?:[=:]\s*|\s+)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        "[redacted credential]",
        text,
    )
    text = re.sub(
        r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9+/_.=-]+",
        "[redacted authentication]",
        text,
    )
    text = re.sub(
        r"(?i)\b(?:command|cmd|argv|execstart)\s*[=:].*", "[redacted command]", text
    )
    text = redact_log_message(text)
    return re.sub(r"[\x00-\x1f\x7f]", " ", text)[:1200]


def safe_object(value):
    if isinstance(value, dict):
        return {
            redact(k)[:80]: safe_object(v)
            for k, v in list(value.items())[:40]
            if not re.search(
                r"(?i)password|passwd|token|secret|credential|argv|command|authorization|cookie|api[_-]?key|^psk$|^wpa_psk$|^args$|^key$|^headers$",
                str(k),
            )
        }
    if isinstance(value, (list, tuple)):
        return [safe_object(v) for v in value[:50]]
    return redact(value) if isinstance(value, str) else value


def epoch(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("timestamps must be finite")
        return result
    try:
        result = float(value)
    except ValueError:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamps require an explicit timezone")
        return parsed.timestamp()
    if not math.isfinite(result):
        raise ValueError("timestamps must be finite")
    return result


def identity(source, provenance):
    return hashlib.sha256(
        (
            source + json.dumps(provenance, sort_keys=True, separators=(",", ":"))
        ).encode()
    ).hexdigest()


def observation(source, device, message, at, provenance, **extra):
    event = dict(
        time=at,
        source_time=None,
        received_at=at,
        imported_at=time.time(),
        source=source,
        device=device,
        uplink=None,
        kind="observation",
        severity="info",
        message=redact(message),
        time_quality="receiver-time",
        provenance=safe_object(provenance),
        domain="context",
        state="info",
        stream=source,
        boot_id=None,
        monotonic=None,
        session="unverified",
        backfill=False,
    )
    event.update(extra)
    return event


def classify(tag, message):
    """Return observation semantics; success applies only to the same stream."""
    tag = tag.split("[")[0].rstrip(":")
    p = dict(kind=tag, stream=tag, domain="context", state="info", uplink=None)
    if tag == "clientwan-path":
        p.update(uplink="clientwan", stream="clientwan:path")
        match = re.search(
            r"state=(interface-down|gateway-([01])-public-([012]))", message
        )
        if match:
            value, gateway, public = match.groups()
            p.update(kind="path-state")
            if value == "interface-down":
                p.update(domain="gateway-hotspot", state="unknown")
            elif gateway == "0":
                p.update(domain="gateway-hotspot", state="failure")
            elif public != "2":
                p.update(domain="upstream-tests", state="failure")
            else:
                p.update(domain="upstream-tests", state="success")
        elif message.startswith("summary "):
            values = {
                k: int(v)
                for k, v in re.findall(
                    r"\b(samples|interface_up|gateway_ok|public1_ok|public2_ok)=(\d+)",
                    message,
                )
            }
            n = values.get("samples", 0)
            p.update(kind="path-summary")
            if n and values.get("interface_up") == n:
                if values.get("gateway_ok") == 0:
                    p.update(domain="gateway-hotspot", state="failure")
                elif values.get("gateway_ok") == n and (
                    values.get("public1_ok") == 0 or values.get("public2_ok") == 0
                ):
                    p.update(domain="upstream-tests", state="failure")
                elif all(
                    values.get(k) == n
                    for k in ("gateway_ok", "public1_ok", "public2_ok")
                ):
                    p.update(domain="upstream-tests", state="success")
            # Mixed aggregate windows cannot precisely recover or start a failure.
        elif "stopped" in message:
            p.update(domain="monitoring", state="unknown")
    elif tag == "uplink-https":
        match = re.search(
            r"interface=(wan|clientwan|lifiwan) state=(\w+) google=(\d+)/(\d+) cloudflare=(\d+)/(\d+)",
            message,
        )
        if not match:
            return None
        uplink, state, gcode, grc, ccode, crc = match.groups()
        p.update(
            uplink=uplink, stream=uplink + ":https", kind="https-state", domain="https"
        )
        if state == "online" and (gcode, grc, ccode, crc) == ("204", "0", "204", "0"):
            p["state"] = "success"
        elif state in ("offline", "degraded"):
            p["state"] = "failure"
            if "6" in (grc, crc):
                p["domain"] = "dns"
        else:
            p["state"] = "unknown"
    elif tag in ("hostapd", "wpa_supplicant"):
        p.update(domain="local-wifi" if tag == "hostapd" else "gateway-hotspot")
        mac = re.search(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b", message)
        iface = re.match(r"([a-zA-Z0-9_.-]+):", message)
        p["stream"] = (
            tag
            + ":"
            + (iface.group(1) if iface else "unknown")
            + ":"
            + (mac.group().lower() if mac else "radio")
        )
        if re.search(
            r"(?i)SAE.*(?:fail|timeout)|authentication.*(?:fail|timeout)|AP-STA-POSSIBLE-PSK-MISMATCH|4-Way Handshake failed|AP-DISABLED|CTRL-EVENT-DISCONNECTED|AP-STA-DISCONNECTED|ASSOC-REJECT",
            message,
        ):
            p["state"] = "failure"
        elif re.search(
            r"AP-STA-CONNECTED|AP-ENABLED|CTRL-EVENT-CONNECTED|pairwise key handshake completed",
            message,
        ):
            p["state"] = "success"
        elif not re.search(
            r"(?i)auth|assoc|disconnect|channel|DFS|radio|SAE|WPA", message
        ):
            return None
        if tag == "wpa_supplicant":
            # Interface names are evidence; do not assume an interface-to-uplink mapping.
            p["uplink"] = None
    elif tag.startswith("mwan3"):
        p.update(domain="mwan3", kind="mwan-state")
        match = re.search(r"\b(wan|clientwan|lifiwan)\b", message)
        p["uplink"] = match.group() if match else None
        p["stream"] = "mwan3:" + (p["uplink"] or "policy")
        if re.search(r"(?i)offline|disconnect|unreachable", message):
            p["state"] = "failure"
        elif re.search(r"(?i)\bonline\b|connected", message):
            p["state"] = "success"
    elif tag in ("netifd", "udhcpc", "odhcp6c"):
        if not re.search(
            r"(?i)interface|link|route|lease|dhcp|carrier|connect", message
        ):
            return None
        p["kind"] = "link-dhcp-route"
        match = re.search(r"\b(wan|clientwan|lifiwan)\b", message)
        p["uplink"] = match.group() if match else None
    elif tag.startswith("dnsmasq"):
        # No query logging/browsing history. Only resolver infrastructure faults.
        if not re.search(
            r"(?i)no servers|failed to|maximum number of concurrent|DNSSEC.*fail|SERVFAIL|REFUSED",
            message,
        ):
            return None
        p.update(
            domain="dns",
            stream="shared-resolver",
            kind="resolver-failure",
            state="failure",
        )
        # Query names are irrelevant to the shared-resolver diagnosis.
        message = re.sub(
            r"(?i)\b(?:query|reply|forwarded)\b.*", "[DNS query omitted]", message
        )
        p["message"] = redact(message)
    elif tag == "kernel":
        if not re.search(
            r"(?i)Linux version|wlan|wifi|mt76|firmware.*(?:crash|fail)|NETDEV WATCHDOG|link.*(?:down|up)",
            message,
        ):
            return None
        p["kind"] = "router-kernel"
    elif tag in ("sysntpd", "ntpd"):
        p.update(kind="clock-observation", domain="monitoring")
    else:
        return None
    p["severity"] = "warning" if p["state"] == "failure" else "info"
    return p


def parse_syslog(line, imported_at=None, provenance=None, backfill=True):
    imported_at = time.time() if imported_at is None else imported_at
    provenance = dict(provenance or {})
    try:
        if line.lstrip().startswith("{"):
            record = json.loads(line)
            if not isinstance(record, dict) or any(
                not isinstance(record.get(k), str)
                for k in ("hostname", "tag", "message", "received_at")
            ):
                return None
            received = epoch(record["received_at"])
            reported = epoch(record.get("reported_at"))
            device, tag, message = (
                record["hostname"],
                record["tag"],
                record["message"].lstrip(),
            )
            quality = "receiver-time (Pi receipt); source-clock-unverified"
            provenance.update(
                format="rsyslog-json-v1",
                receipt_time_raw=record["received_at"],
                source_ip=record.get("source_ip"),
                source_timezone="receiver interpretation of classic syslog; year/zone inferred",
                reported_at_raw=record.get("reported_at"),
            )
        else:
            parts = line.strip().split(" ", 4)
            if len(parts) != 5:
                return None
            stamp, ip, device, tag, message = parts
            received, reported, quality = (
                epoch(stamp),
                None,
                "receiver-time (Pi receipt); source-time-unavailable",
            )
            provenance.update(
                format="rsyslog-legacy", source_ip=ip, receipt_time_raw=stamp
            )
    except (ValueError, KeyError, TypeError):
        return None
    classification = classify(tag, message)
    if classification is None:
        return None
    message = classification.pop("message", message)
    return observation(
        "openwrt",
        device,
        message,
        received,
        provenance,
        source_time=reported,
        imported_at=imported_at,
        backfill=backfill,
        time_quality=quality,
        **classification
    )


def parse_ubnt(
    line,
    *,
    boot_id,
    uptime,
    received_at,
    imported_at=None,
    provenance=None,
    uncertainty=5
):
    match = re.match(r"uptime=(\d+(?:\.\d+)?)\s+(.*)", line)
    if not match:
        return None
    monotonic = float(match.group(1))
    if monotonic > uptime or monotonic < 0:
        return None
    message = match.group(2)
    state = "info"
    if re.search(
        r"(?i)failed|failure|timed? out|no .*candidate|not associated|internet.*(?:down|unavailable)",
        message,
    ):
        state = "failure"
    elif re.search(r"(?i)healthy|connected.*success|connection successful", message):
        state = "success"
    event = observation(
        "ubnt-manager",
        "ubnt",
        message,
        received_at - (uptime - monotonic),
        provenance or {},
        imported_at=received_at if imported_at is None else imported_at,
        received_at=received_at,
        time_quality="monotonic-anchor-approximate",
        boot_id=boot_id,
        session=boot_id,
        monotonic=monotonic,
        domain="ubnt",
        uplink="wan",
        stream="ubnt:manager",
        kind="selection-transition",
        state=state,
        backfill=uptime - monotonic > 120,
    )
    event["provenance"]["uncertainty_seconds"] = uncertainty
    return event
