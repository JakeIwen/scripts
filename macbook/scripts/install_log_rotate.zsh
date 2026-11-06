#!/bin/zsh
# Install or update the hourly LaunchAgent that rotates the Mac's job logs.
# Stdlib Python on /usr/bin/python3, so there is no venv to build.
set -eux -o pipefail

readonly repo=/Users/jacobr/dev/scripts
readonly label=com.jacobr.log-rotate
readonly source_plist=$repo/macbook/launchagents/$label.plist
readonly installed_plist=/Users/jacobr/Library/LaunchAgents/$label.plist
readonly state_dir=/Users/jacobr/.local/state/log-rotate
readonly domain=gui/$(/usr/bin/id -u)

/usr/bin/plutil -lint "$source_plist"
/bin/test -f "$repo/macbook/scripts/log_rotate.py"
PYTHONPATH="$repo/shared/python" /usr/bin/python3 -B "$repo/macbook/scripts/log_rotate.py" status --json >/dev/null
# launchd opens its log before the job starts, so the directory has to exist.
/bin/mkdir -p -m 700 "$state_dir"
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
/bin/launchctl print "$domain/$label" | /usr/bin/grep -E 'state =|runs =|last exit'
