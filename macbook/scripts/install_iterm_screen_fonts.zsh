#!/bin/zsh
# Install or update the LaunchAgent that matches iTerm font sizes to displays.
# Rebuilds its venv when missing or broken (for example after Homebrew drops
# the Python it was built from).
set -eux -o pipefail

readonly repo=/Users/jacobr/dev/scripts
readonly label=com.jacobr.iterm-screen-fonts
readonly source_plist=$repo/macbook/launchagents/$label.plist
readonly installed_plist=/Users/jacobr/Library/LaunchAgents/$label.plist
readonly venv=/Users/jacobr/.local/share/iterm-screen-fonts/venv
readonly iterm2_version=2.30
readonly domain=gui/$(/usr/bin/id -u)

/usr/bin/plutil -lint "$source_plist"
/bin/test -f "$repo/macbook/scripts/iterm_screen_fonts.py"
if ! "$venv/bin/python" -c 'import iterm2' 2>/dev/null; then
    /bin/rm -rf "$venv"
    /opt/homebrew/bin/python3 -m venv "$venv"
    "$venv/bin/pip" install --disable-pip-version-check -q "iterm2==$iterm2_version"
fi
/bin/mkdir -p -m 700 "$repo/tmp/iterm-screen-fonts"
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
