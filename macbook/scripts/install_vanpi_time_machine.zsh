#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"
repo_root="${script_dir:h:h}"
label="com.jacobr.vanpi-time-machine"
source_runner="$script_dir/start_vanpi_time_machine_backup.zsh"
source_plist="$repo_root/macbook/launchagents/$label.plist"
application_root="$HOME/Library/Application Support/vanpi-time-machine"
target_runner="$application_root/start_vanpi_time_machine_backup.zsh"
target_plist="$HOME/Library/LaunchAgents/$label.plist"
user_id="$(/usr/bin/id -u)"

if [[ "$HOME" != "/Users/jacobr" ]]; then
  print -u2 -- "The LaunchAgent is pinned to /Users/jacobr; refusing HOME=$HOME"
  exit 1
fi

echo "Validating the guarded Time Machine job..."
/bin/zsh -n "$source_runner"
/usr/bin/plutil -lint "$source_plist"
/usr/bin/tmutil destinationinfo -X |
  /usr/bin/plutil -extract Destinations raw -o - - >/dev/null

echo "Installing the runner and LaunchAgent..."
/bin/mkdir -p "$application_root" "$HOME/Library/LaunchAgents"
/usr/bin/install -m 700 "$source_runner" "$target_runner"
/usr/bin/install -m 600 "$source_plist" "$target_plist"

echo "Disabling macOS's unguarded automatic Time Machine scheduler..."
echo "This requires administrator access and Terminal Full Disk Access."
/usr/bin/sudo /usr/bin/tmutil disable

/bin/launchctl bootout "gui/$user_id/$label" 2>/dev/null || true
if ! /bin/launchctl bootstrap "gui/$user_id" "$target_plist"; then
  print -u2 -- "Could not load $label."
  print -u2 -- "Restoring macOS automatic Time Machine scheduling."
  /usr/bin/sudo /usr/bin/tmutil enable
  exit 1
fi

/bin/launchctl print "gui/$user_id/$label" >/dev/null
echo "Installed. The guarded job checks hourly and once when loaded."
if ! "$target_runner" --check; then
  echo "The current preflight is not ready; no backup was requested."
fi
