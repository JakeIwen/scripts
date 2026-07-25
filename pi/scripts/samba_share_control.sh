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
pgrep_command=${SAMBA_SHARE_CONTROL_PGREP:-/usr/bin/pgrep}
sleep_command=${SAMBA_SHARE_CONTROL_SLEEP:-/usr/bin/sleep}
drain_dir=${SAMBA_SHARE_DRAIN_DIR:-/run/lock/vanpi-samba-drain}
state_wait_seconds=${SAMBA_SHARE_STATE_WAIT_SECONDS:-5}

usage() {
  echo "usage: ${0##*/} close|drain|clear <managed-label> [managed-label ...]" >&2
}

[[ $# -ge 2 && "$1" =~ ^(close|drain|clear)$ ]] || {
  usage
  exit 2
}
action=$1
shift

[[ "$state_wait_seconds" =~ ^[0-9]+$ && "$state_wait_seconds" -le 30 ]] || {
  echo "ERROR: invalid Samba state wait: $state_wait_seconds" >&2
  exit 2
}

labels=()
shares=()
for label in "$@"; do
  disk_policy_is_control_label "$label" || {
    echo "ERROR: refusing Samba control for unmanaged label '$label'" >&2
    exit 2
  }
  share=$(disk_policy_samba_share_name "$label") || continue
  labels+=("$label")
  shares+=("$share")
done

(( ${#shares[@]} )) || exit 0

share_names=${shares[0]}
for share in "${shares[@]:1}"; do
  share_names+=", $share"
done

ssc_validate_drain_dir() {
  if [[ -L "$drain_dir" || ( -e "$drain_dir" && ! -d "$drain_dir" ) ]]; then
    echo "ERROR: unsafe Samba drain directory: $drain_dir" >&2
    exit 1
  fi
}

ssc_drain() {
  local index label marker temporary

  ssc_validate_drain_dir
  "$sudo_command" /usr/bin/install -d -m 0755 -o root -g root -- "$drain_dir" ||
    return 1
  ssc_validate_drain_dir
  for index in "${!labels[@]}"; do
    label=${labels[$index]}
    marker="$drain_dir/$label"
    temporary="$drain_dir/.$label.$$"
    if ! "$sudo_command" /usr/bin/install -m 0644 -o root -g root \
        -- /dev/null "$temporary" ||
       ! "$sudo_command" /usr/bin/mv -f -- "$temporary" "$marker"; then
      "$sudo_command" /usr/bin/rm -f -- "$temporary" 2>/dev/null || true
      echo "ERROR: cannot drain Samba share ${shares[$index]}" >&2
      return 1
    fi
    echo "draining Samba share ${shares[$index]}"
  done
}

ssc_clear() {
  local index marker

  ssc_validate_drain_dir
  [[ -d "$drain_dir" ]] || return 0
  for index in "${!labels[@]}"; do
    marker="$drain_dir/${labels[$index]}"
    if ! "$sudo_command" /usr/bin/rm -f -- "$marker"; then
      echo "ERROR: cannot clear Samba drain for ${shares[$index]}" >&2
      return 1
    fi
    echo "cleared Samba drain for ${shares[$index]}"
  done
}

ssc_close() {
  local state status attempt output share had_failure=0

  # systemd can briefly report activating/deactivating, and a failed unit may
  # already have no daemon left. Wait a bounded interval, then accept only an
  # active daemon or a state with no smbd process at all.
  for ((attempt = 0; attempt <= state_wait_seconds; attempt++)); do
    state=$("$systemctl_command" show --property=ActiveState --value smbd.service 2>&1)
    status=$?
    if (( status != 0 )) || [[ -z "$state" || "$state" == *$'\n'* ]]; then
      echo "ERROR: cannot determine smbd state while closing Samba shares $share_names (status $status): ${state:-no output}" >&2
      return 1
    fi
    if [[ "$state" == active ]]; then
      break
    fi

    "$pgrep_command" -x smbd >/dev/null 2>&1
    status=$?
    if (( status == 1 )) && [[ "$state" == inactive || "$state" == failed ]]; then
      echo "Samba shares $share_names are already closed (smbd state: $state)"
      return 0
    elif (( status != 0 && status != 1 )); then
      echo "ERROR: cannot inspect smbd processes while closing Samba shares $share_names (pgrep status $status)" >&2
      return 1
    fi

    if (( attempt == state_wait_seconds )); then
      echo "ERROR: cannot close Samba shares $share_names because smbd remains in state '$state' with live processes" >&2
      return 1
    fi
    "$sleep_command" 1
  done

  for share in "${shares[@]}"; do
    output=$(
      "$sudo_command" "$smbcontrol_command" -t 3 smbd close-share "$share" 2>&1
    )
    status=$?
    if (( status != 0 )); then
      echo "ERROR: cannot close Samba share $share (status $status): ${output:-no output}" >&2
      had_failure=1
    else
      echo "closed Samba connections for $share"
    fi
  done

  (( had_failure == 0 ))
}

case "$action" in
  drain) ssc_drain ;;
  clear) ssc_clear ;;
  close) ssc_close ;;
esac
