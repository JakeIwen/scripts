#!/bin/zsh
set -eux -o pipefail

readonly repo=/Users/jacobr/dev/scripts
readonly label=com.jacobr.hosted-project-scan
readonly source_plist=$repo/macbook/launchagents/$label.plist
readonly installed_plist=/Users/jacobr/Library/LaunchAgents/$label.plist
readonly domain=gui/$(/usr/bin/id -u)

/usr/bin/plutil -lint "$source_plist"
/bin/test -x /opt/homebrew/opt/python@3.13/bin/python3.13
/bin/test -f "$repo/pi/apps/van_dashboard/hosted_project_scan.py"
/bin/mkdir -p -m 700 "$repo/tmp/hosted-project-scan"
if ! /usr/bin/cmp -s "$source_plist" "$installed_plist"; then
    /bin/launchctl bootout "$domain/$label" 2>/dev/null || true
    /usr/bin/install -m 644 "$source_plist" "$installed_plist"
fi
/bin/launchctl enable "$domain/$label"
if ! /bin/launchctl print "$domain/$label" >/dev/null 2>&1; then
    /bin/launchctl bootstrap "$domain" "$installed_plist"
fi
/bin/launchctl kickstart "$domain/$label"
/bin/launchctl print "$domain/$label"
