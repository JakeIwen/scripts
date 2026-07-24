#!/bin/bash
# Disconnect only the Samba shares backed by selected filesystem labels.
#
# samba_require_mount.sh denies new connections as soon as an exact labeled
# filesystem is absent.  Closing the selected live shares here releases their
# current handles without interrupting unrelated shares such as pihome.
set -u

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=disk_policy.sh
. "$script_dir/disk_policy.sh" || exit 1

systemctl_command=${SAMBA_SHARE_CONTROL_SYSTEMCTL:-/usr/bin/systemctl}
smbcontrol_command=${SAMBA_SHARE_CONTROL_SMBCONTROL:-/usr/bin/smbcontrol}
sudo_command=${SAMBA_SHARE_CONTROL_SUDO:-/usr/bin/sudo}

usage() {
  echo "usage: ${0##*/} close <managed-label> [managed-label ...]" >&2
}

[[ $# -ge 2 && "$1" == close ]] || {
  usage
  exit 2
}
shift

shares=()
for label in "$@"; do
  disk_policy_is_control_label "$label" || {
    echo "ERROR: refusing Samba control for unmanaged label '$label'" >&2
    exit 2
  }
  share=$(disk_policy_samba_share_name "$label") || continue
  shares+=("$share")
done

(( ${#shares[@]} )) || exit 0

state=$("$systemctl_command" show --property=ActiveState --value smbd.service 2>&1)
status=$?
if (( status != 0 )) || [[ -z "$state" || "$state" == *$'\n'* ]]; then
  echo "ERROR: cannot determine smbd state (status $status): ${state:-no output}" >&2
  exit 1
fi
case "$state" in
  inactive)
    # No daemon means there are no SMB handles to release.
    exit 0
    ;;
  active) ;;
  *)
    echo "ERROR: smbd is in transitional or failed state '$state'; refusing disk changes" >&2
    exit 1
    ;;
esac

had_failure=0
for share in "${shares[@]}"; do
  output=$("$sudo_command" "$smbcontrol_command" smbd close-share "$share" 2>&1)
  status=$?
  if (( status != 0 )); then
    echo "ERROR: cannot close Samba share $share (status $status): ${output:-no output}" >&2
    had_failure=1
  else
    echo "closed Samba connections for $share"
  fi
done

(( had_failure == 0 ))
