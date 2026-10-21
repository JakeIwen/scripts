#!/bin/zsh
# Install or update the LaunchAgent that dims the cab lights when the monitor
# is plugged in. Rebuilds its venv when missing or broken.
set -eux -o pipefail

readonly repo=/Users/jacobr/dev/scripts
readonly label=com.jacobr.display-cab-dimmer
readonly source_plist=$repo/macbook/launchagents/$label.plist
readonly installed_plist=/Users/jacobr/Library/LaunchAgents/$label.plist
readonly venv=/Users/jacobr/.local/share/display-cab-dimmer/venv
readonly pyobjc_version=12.2.2
readonly domain=gui/$(/usr/bin/id -u)

/usr/bin/plutil -lint "$source_plist"
/bin/test -f "$repo/macbook/scripts/display_cab_dimmer.py"
/bin/test -f "$repo/shared/python/tool_settings.py"
if ! "$venv/bin/python" -c 'import Quartz, AppKit' 2>/dev/null; then
    /bin/rm -rf "$venv"
    /opt/homebrew/bin/python3 -m venv "$venv"
    "$venv/bin/pip" install --disable-pip-version-check -q \
        "pyobjc-framework-Cocoa==$pyobjc_version" \
        "pyobjc-framework-Quartz==$pyobjc_version"
fi
PYTHONPATH="$repo/shared/python" "$venv/bin/python" -c 'import Quartz, AppKit, tool_settings'
/bin/mkdir -p -m 700 "$repo/tmp/display-cab-dimmer"
if ! /usr/bin/cmp -s "$source_plist" "$installed_plist"; then
    /bin/launchctl bootout "$domain/$label" 2>/dev/null || true
    /usr/bin/install -m 644 "$source_plist" "$installed_plist"
fi
/bin/launchctl enable "$domain/$label"
if /bin/launchctl print "$domain/$label" >/dev/null 2>&1; then
    /bin/launchctl kickstart -k "$domain/$label"
else
    /bin/launchctl bootstrap "$domain" "$installed_plist"
fi
/bin/launchctl print "$domain/$label" | /usr/bin/grep -E 'state =|pid =|last exit'
