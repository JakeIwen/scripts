#!/bin/zsh

set -euo pipefail

script_dir=${0:A:h}
exec /usr/bin/osascript "$script_dir/findmy_open_items.applescript"
