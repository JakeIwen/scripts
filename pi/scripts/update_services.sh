#!/bin/bash
set -euo pipefail

stage="${1:-/tmp/systemd-tmp}"
if [[ ! "$stage" =~ ^/tmp/systemd-tmp(\.[0-9]+)?$ ]]; then
  echo "refusing unsafe service update staging path: $stage" >&2
  exit 1
fi

[[ -d "$stage" && ! -L "$stage" && -d "$stage/.backup-sync" && ! -L "$stage/.backup-sync" ]] || {
  echo 'Unsafe or incomplete service update stage.' >&2; exit 1;
}
boundary="$stage/.backup-sync/backup_deploy_sync.py"
[[ -f "$boundary" && ! -L "$boundary" ]] || { echo 'Missing independent backup sync boundary.' >&2; exit 1; }
# The root supervisor holds the existing legacy inode shared while this receiver
# keeps its original user identity. No sudo descriptor-preservation is required.
sync_sudo=${UPDATE_SERVICES_SUDO:-/usr/bin/sudo}
if [[ "$#" == 1 ]]; then
  exec "$sync_sudo" -n /usr/bin/python3 -B "$boundary" run "$stage" /bin/bash "$0" "$stage" --backup-fenced
fi
[[ "$#" == 2 && "$2" == --backup-fenced ]] || exit 2
/usr/bin/python3 -B "$stage/.backup-sync/backup_locks.py" --check-legacy-hold
cleanup_relative=scripts/setup_router_policy_trigger.sh
"$sync_sudo" -n /usr/bin/python3 -B "$boundary" check-stage "$stage" --remove "$cleanup_relative"

staged_services="$stage/services"
staged_scripts="$stage/scripts"
staged_tmpfiles="$stage/tmpfiles.d"
# Overridable only so tests can run this against a temporary tree.
live_services=${UPDATE_SERVICES_LIVE_SERVICES:-$(/usr/bin/python3 -B "$boundary" root services)}
live_scripts=${UPDATE_SERVICES_LIVE_SCRIPTS:-$(/usr/bin/python3 -B "$boundary" root scripts)}
live_tmpfiles=${UPDATE_SERVICES_LIVE_TMPFILES:-$(/usr/bin/python3 -B "$boundary" root tmpfiles.d)}

cleanup() {
  case "$stage" in
    /tmp/systemd-tmp | /tmp/systemd-tmp.*) sudo rm -rf -- "$stage" ;;
  esac
}
trap cleanup EXIT

if [[ ! -d "$staged_services" || ! -d "$staged_scripts" ||
      ! -d "$staged_tmpfiles" ]]; then
  echo "service update staging is incomplete: $stage" >&2
  exit 1
fi

shopt -s nullglob
staged_units=(
  "$staged_services"/*.service
  "$staged_services"/*.path
  "$staged_services"/*.timer
)
staged_tmpfile_configs=("$staged_tmpfiles"/*.conf)
# Canonical backup and storage exclusions can legitimately empty these roots.

declare -a new_units=()
declare -a changed_units=()

for staged_unit in "${staged_units[@]:-}"; do
  [[ -n "$staged_unit" ]] || continue
  unit="${staged_unit##*/}"
  live_unit="$live_services/$unit"
  changed=false
  is_new=false

  if [[ ! -e "$live_unit" ]]; then
    is_new=true
    new_units+=("$unit")
  elif ! cmp -s "$staged_unit" "$live_unit"; then
    changed=true
  fi

  # Restart a service when a repository-managed file named in ExecStart or an
  # ExecStartPre validation has changed. This catches interpreted programs and
  # their explicitly declared runtime assets, where the executable itself
  # (/usr/bin/python3 or /usr/bin/test) does not change.
  while IFS= read -r managed_path; do
    relative_path="${managed_path#/home/pi/scripts/}"
    staged_path="$staged_scripts/$relative_path"
    live_path="$live_scripts/$relative_path"
    if [[ -f "$staged_path" ]] && \
       { [[ ! -e "$live_path" ]] || ! cmp -s "$staged_path" "$live_path"; }; then
      changed=true
    fi
  done < <(
    sed -n -e 's/^ExecStart=//p' -e 's/^ExecStartPre=//p' "$staged_unit" \
      | grep -oE '/home/pi/scripts/[^[:space:]\"]+' \
      || true
  )

  if [[ "$changed" == true && "$is_new" == false ]]; then
    changed_units+=("$unit")
  fi
done

# Runtime locks must exist with their cross-user ownership before updated
# scripts can reference them. systemd-tmpfiles creates missing files and
# reconciles owner/mode without replacing an inode held by a running process.
sudo install -d -m 0755 "$live_tmpfiles"
for staged_tmpfile in "${staged_tmpfile_configs[@]:-}"; do
  [[ -n "$staged_tmpfile" ]] || continue
  tmpfile_name="${staged_tmpfile##*/}"
  live_tmpfile="$live_tmpfiles/$tmpfile_name"
  sudo install -m 0644 "$staged_tmpfile" "$live_tmpfile"
  sudo systemd-tmpfiles --create "$live_tmpfile"
done

# Preserve the old sync behavior for top-level scripts (mode 770), applied to the
# staged copies so cp -a installs them already executable. A copy that fails
# partway (set -e) can then never leave a live script non-executable, and the
# mode is in place before any service restart. Directory modes are preserved
# from the staged tree.
for staged_script in "$staged_scripts"/*; do
  if [[ -f "$staged_script" && ! -L "$staged_script" ]]; then
    chmod 770 "$staged_script"
  fi
done
mkdir -p "$live_scripts"
/usr/bin/python3 -B "$boundary" copy-scripts "$staged_scripts" "$live_scripts"
# Legacy clone cleanup belongs to backup maintenance, not this non-backup sync.
rm -f -- "$live_scripts/${cleanup_relative#scripts/}"

for staged_unit in "${staged_units[@]:-}"; do
  [[ -n "$staged_unit" ]] || continue
  sudo install -m 0644 "$staged_unit" "$live_services/${staged_unit##*/}"
done

# systemd may still have a stale cached unit even when the staged and on-disk
# files match (for example, after an earlier copy that missed daemon-reload).
sudo systemctl daemon-reload

for unit in "${new_units[@]:-}"; do
  [[ -n "$unit" ]] || continue
  echo "NEW UNIT: $unit (enabling and starting)"
  sudo systemctl enable --now "$unit"
done

for unit in "${changed_units[@]:-}"; do
  [[ -n "$unit" ]] || continue
  echo "UPDATED UNIT: $unit"
  if sudo systemctl is-active --quiet "$unit"; then
    sudo systemctl restart "$unit"
    echo "RESTARTED UNIT: $unit"
  else
    echo "NOT ACTIVE; NOT STARTED: $unit"
  fi
done
