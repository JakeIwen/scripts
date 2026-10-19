#!/bin/zsh
set -eux -o pipefail

readonly repo=/Users/jacobr/dev/scripts
readonly venv=/Users/jacobr/.local/share/mac-panel/venv
readonly data=$repo/tmp/mac-panel

if ! "$venv/bin/python" -c 'import sys' 2>/dev/null; then
    # Preserve a broken environment rather than recursively deleting an unchecked path.
    if [[ -e "$venv" || -L "$venv" ]]; then
        /bin/mv "$venv" "$venv.broken.$(/bin/date +%Y%m%d%H%M%S).$$"
    fi
    /opt/homebrew/bin/python3 -m venv --without-pip "$venv"
fi
/bin/mkdir -p -m 700 "$data"
/bin/chmod 700 "$data"
"$venv/bin/python" "$repo/macbook/mac_panel/panel_service.py" install
"$venv/bin/python" "$repo/macbook/mac_panel/panel_service.py" status
