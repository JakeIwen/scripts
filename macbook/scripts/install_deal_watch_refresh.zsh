#!/bin/zsh
set -eux -o pipefail

readonly repo=/Users/jacobr/dev/scripts
readonly label=com.jacobr.deal-watch-refresh
readonly source_plist=$repo/macbook/launchagents/$label.plist
readonly installed_plist=/Users/jacobr/Library/LaunchAgents/$label.plist
readonly domain=gui/$(/usr/bin/id -u)

/usr/bin/plutil -lint "$source_plist"
/bin/test -x /opt/homebrew/bin/python3
/bin/test -x /Users/jacobr/.nvm/versions/node/v22.22.0/bin/node
/bin/test -f "$repo/macbook/scripts/deal_watch_refresh.py"
/bin/test -f /Users/jacobr/.local/share/codex-browser-tools/doctor.mjs
/bin/mkdir -p -m 700 "$repo/tmp/deal-watch-refresh"
if [[ -e "$installed_plist" ]]; then
    /usr/bin/cmp -s "$source_plist" "$installed_plist"
else
    /usr/bin/install -m 644 "$source_plist" "$installed_plist"
fi
/bin/launchctl enable "$domain/$label"
if ! /bin/launchctl print "$domain/$label" >/dev/null 2>&1; then
    /bin/launchctl bootstrap "$domain" "$installed_plist"
fi
/bin/launchctl kickstart "$domain/$label"
/bin/launchctl print "$domain/$label"
