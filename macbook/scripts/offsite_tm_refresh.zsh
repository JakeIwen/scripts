#!/bin/zsh
set -eux -o pipefail
sudo -v
( set +x; while kill -0 $$; do sudo -n true; sleep 50; done 2>/dev/null ) &
keepalive=$!

# The agent and share names match the Time Machine installer and runner.
agent=com.jacobr.vanpi-time-machine
share=mbp2tbkup
plist=$HOME/Library/LaunchAgents/$agent.plist
# The image is the Time Machine source; offsite_config.py owns its drain path.
image=m4mac0.sparsebundle
gate=/run/lock/vanpi-samba-drain/$share
# The transient unit is the offsite run documented in pi/docs/backup/OFFSITE.md.
unit=vanpi-offsite
label=FORT-MPLS-5TB
# OFFSITE_TM_REFRESH_VOLUME is a test seam: an isolated volume instead of the real mount.
volume=${OFFSITE_TM_REFRESH_VOLUME:-/Volumes/Backups of m4mac}

# Reload the starter and warn if launchd still cannot find it.
reload_agent() {
  launchctl bootstrap "gui/$UID" "$plist" 2>/dev/null || true
  if ! launchctl print "gui/$UID/$agent" >/dev/null 2>&1; then
    print -u2 "WARNING: Time Machine agent is not loaded; run: launchctl bootstrap gui/$UID $plist"
    return 1
  fi
}

# Stop the keepalive and retry loading the agent exactly once on exit or signal.
cleaned=0
cleanup() {
  (( cleaned )) && return 0
  cleaned=1
  kill "$keepalive" 2>/dev/null || true
  reload_agent || true
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
trap 'cleanup; exit 129' HUP

# Restrict labels to the disk-label contract before passing one to the remote shell.
if (( $# )); then
  [[ $# == 2 && $1 == --label ]] || { print -u2 'Usage: offsite_tm_refresh.zsh [--label LABEL]'; exit 2; }
  label=$2
fi
[[ $label =~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,15}$' ]] || { print -u2 'Invalid disk label'; exit 2; }

# Count the files the Mac still has open inside the Time Machine image on the Pi.
# A failed smbstatus prints nothing (never a false zero), so the wait below keeps waiting.
pi_handles() { ssh vanpi "out=\$(sudo smbstatus -L) || exit 9; printf '%s\n' \"\$out\" | grep -c '$image' || true"; }

# Pause the hourly starter, which ignores tmutil disable; the EXIT trap reloads it.
launchctl bootout "gui/$UID/$agent" 2>/dev/null || true
if launchctl print "gui/$UID/$agent" >/dev/null 2>&1; then
  print -u2 'WARNING: Time Machine agent is still loaded; stopping before unmounting'
  exit 1
fi

# Let the running backup finish on its own.
while [[ $(tmutil status) == *'Running = 1'* ]]; do sleep 30; done

# Give backupd time to finish detaching its volume.
sleep 5

# Unmount snapshots and the backup volume before ejecting its physical store; never force.
mount | awk '/com\.apple\.TimeMachine\..*\.backup@/ {print $3}' | while read -r snap; do sudo diskutil unmount "$snap"; done
if [[ -d "$volume" ]]; then
  store=$(diskutil info "$volume" | awk -F': *' '/APFS Physical Store/ {print $2}')
  [[ -n $store ]] || { print -u2 "Cannot find the APFS physical store of \"$volume\"; not unmounting it"; exit 1; }
  sudo diskutil unmount "$volume"
  sudo diskutil eject "$store"
fi

# Unmount the SMB connection so the Pi can release the image handles.
mount -t smbfs | awk -v share="$share" '$0 ~ "/" share " \\(smbfs" {print $3}' | while read -r mountpoint; do sudo diskutil unmount "$mountpoint"; done

# Wait up to two minutes for zero image handles; reload the starter if they remain.
for i in {1..12}; do [[ $(pi_handles) == 0 ]] && break; sleep 10; done
[[ $(pi_handles) == 0 ]] || { ssh vanpi "sudo smbstatus -L 2>/dev/null | grep '$image' | head -5"; exit 1; }

# Start the offsite Time Machine copy in a detached Pi unit.
ssh vanpi "sudo systemd-run --unit=$unit --collect --service-type=exec --property=KillMode=mixed --property=TimeoutStopSec=120 /home/pi/scripts/backup/offsite_backup.sh backup --label $label --only time-machine"

# Reload the starter after the Pi gate is up or the run has ended.
until ssh -o ConnectTimeout=15 vanpi "test -e $gate || ! systemctl -q is-active $unit"; do sleep 10; done
reload_agent

# Wait for the copy to finish; a dropped connection retries the check.
until ssh -o ConnectTimeout=15 vanpi "! systemctl -q is-active $unit"; do sleep 60; done

# Show the final status.
ssh vanpi 'sudo /home/pi/scripts/backup/offsite_backup.sh status' | /usr/bin/python3 -c 'import json,sys; s=json.load(sys.stdin); print(s["result"], (s.get("last_result") or {}).get("cause", ""))'
