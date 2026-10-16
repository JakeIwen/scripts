"""Labels derived from Sonos's own clock, independent of browser/Pi time zones."""

from datetime import datetime
import re

# Sonos encodes the US November/March transitions followed by a -60 minute
# daylight bias. Unknown regional rules must not be named from UTC offset alone.
US_DAYLIGHT_RULE = "0b000102000003000202ffc4"
US_ZONE_NAMES = {
    300: ("EST", "EDT"), 360: ("CST", "CDT"), 420: ("MST", "MDT"),
    480: ("PST", "PDT"), 540: ("AKST", "AKDT"),
}


def sonos_time_zone(clock):
    try:
        local = datetime.strptime(clock["CurrentLocalTime"], "%Y-%m-%d %H:%M:%S")
        utc = datetime.strptime(clock["CurrentUTCTime"], "%Y-%m-%d %H:%M:%S")
    except (KeyError, TypeError, ValueError):
        return None
    seconds = int((local - utc).total_seconds())
    if seconds % 60 or not -12 * 3600 <= seconds <= 14 * 3600:
        return None
    offset = seconds // 60
    rule = clock.get("CurrentTimeZone")
    rule = rule.lower() if isinstance(rule, str) else ""
    if re.fullmatch(r"[0-9a-f]{28}", rule):
        bias = int(rule[:4], 16)
        names = US_ZONE_NAMES.get(bias)
        if rule[4:] == US_DAYLIGHT_RULE and names:
            if offset == -bias:
                return names[0]
            if offset == -bias + 60:
                return names[1]
        if rule == "01a400000000000000000000ffc4" and offset == -420:
            return "MST"
    if offset == 0:
        return "UTC"
    hours, minutes = divmod(abs(offset), 60)
    return f"UTC{'+' if offset > 0 else '-'}{hours:02d}:{minutes:02d}"
