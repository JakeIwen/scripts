#!/usr/bin/env python3
"""Turn off Firefox ads, recommendations, studies and telemetry in every profile.

Writes a managed block of user_pref() lines into each in-use profile's user.js
(Firefox, Developer Edition, Nightly share one profiles.ini on macOS). Firefox
re-applies user.js at every startup, so the settings survive app updates and
take effect on the next restart; it is safe to run while Firefox is open.
Lines outside the managed block are left alone.

    firefox_quiet_prefs.py            # apply
    firefox_quiet_prefs.py --dry-run  # show what would change
    firefox_quiet_prefs.py --remove   # drop the managed block
"""

from __future__ import annotations

import argparse
import configparser
import json
import sys
from pathlib import Path

FIREFOX_DIR = Path.home() / "Library/Application Support/Firefox"
BEGIN = "// BEGIN firefox_quiet_prefs (managed by ~/dev/scripts; edits inside are overwritten)"
END = "// END firefox_quiet_prefs"

# Pref names checked against Firefox 158 and Developer Edition 159 omni.ja.
PREFS: dict[str, bool] = {
    # Search > Firefox Suggest: sponsored and Mozilla address-bar suggestions,
    # and the online lookups that fetch them as you type.
    "browser.urlbar.suggest.quicksuggest.all": False,
    "browser.urlbar.suggest.quicksuggest.sponsored": False,
    "browser.urlbar.suggest.quicksuggest.nonsponsored": False,
    "browser.urlbar.quicksuggest.online.enabled": False,
    # Home > Firefox Home: sponsored shortcuts and stories, and the whole
    # recommended-stories feed. Regular shortcuts stay.
    "browser.newtabpage.activity-stream.showSponsoredTopSites": False,
    "browser.newtabpage.activity-stream.showSponsored": False,
    "browser.newtabpage.activity-stream.feeds.section.topstories": False,
    # General > Browsing: "Recommend extensions/features as you browse".
    "browser.newtabpage.activity-stream.asrouter.userprefs.cfr.addons": False,
    "browser.newtabpage.activity-stream.asrouter.userprefs.cfr.features": False,
    # Privacy > Data Collection: personalized extension recommendations, studies.
    "browser.discovery.enabled": False,
    "app.shield.optoutstudies.enabled": False,
    # Privacy > Data Collection: technical/interaction data and daily usage ping.
    "datareporting.healthreport.uploadEnabled": False,
    "datareporting.usage.uploadEnabled": False,
}


def active_profiles(firefox_dir: Path) -> list[Path]:
    """Profiles listed in profiles.ini that Firefox has actually run (have prefs.js)."""
    ini = configparser.ConfigParser(interpolation=None)
    ini.read(firefox_dir / "profiles.ini")
    found = []
    for section in ini.sections():
        if not section.startswith("Profile") or "Path" not in ini[section]:
            continue
        raw = ini[section]["Path"]
        path = firefox_dir / raw if ini[section].get("IsRelative", "1") == "1" else Path(raw)
        if (path / "prefs.js").exists():
            found.append(path)
    return found


def managed_block(prefs: dict[str, bool]) -> str:
    lines = [f"user_pref({json.dumps(k)}, {json.dumps(v)});" for k, v in prefs.items()]
    return "\n".join([BEGIN, *lines, END]) + "\n"


def without_block(text: str) -> str:
    start = text.find(BEGIN)
    if start < 0:
        return text
    end = text.find(END, start)
    if end < 0:
        raise ValueError("user.js has a BEGIN marker with no END marker; fix it by hand")
    end = text.find("\n", end)
    return text[:start] + (text[end + 1 :] if end >= 0 else "")


def updated_user_js(current: str, remove: bool) -> str:
    base = without_block(current).rstrip("\n")
    if remove:
        return base + "\n" if base else ""
    return (base + "\n\n" if base else "") + managed_block(PREFS)


def main(argv: list[str] | None = None, firefox_dir: Path = FIREFOX_DIR) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args(argv)

    profiles = active_profiles(firefox_dir)
    if not profiles:
        print(f"no active Firefox profiles under {firefox_dir}", file=sys.stderr)
        return 1
    for profile in profiles:
        user_js = profile / "user.js"
        current = user_js.read_text() if user_js.exists() else ""
        new = updated_user_js(current, args.remove)
        state = "unchanged" if new == current else ("would update" if args.dry_run else "updated")
        if new != current and not args.dry_run:
            user_js.write_text(new)
        print(f"{state}: {user_js}")
    if not args.dry_run:
        print("Restart Firefox for the changes to take effect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
