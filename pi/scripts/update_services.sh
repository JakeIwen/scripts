#!/bin/bash
set -euo pipefail

stage="${1:-/tmp/systemd-tmp}"
if [[ ! "$stage" =~ ^/tmp/systemd-tmp(\.[0-9]+)?$ ]]; then
  echo "refusing unsafe service update staging path: $stage" >&2
  exit 1
fi

staged_services="$stage/services"
staged_scripts="$stage/scripts"
live_services="/etc/systemd/system"
live_scripts="/home/pi/scripts"

cleanup() {
  case "$stage" in
    /tmp/systemd-tmp | /tmp/systemd-tmp.*) sudo rm -rf -- "$stage" ;;
  esac
}
trap cleanup EXIT

if [[ ! -d "$staged_services" || ! -d "$staged_scripts" ]]; then
  echo "service update staging is incomplete: $stage" >&2
  exit 1
fi

shopt -s nullglob
staged_units=("$staged_services"/*.service "$staged_services"/*.path)
if (( ${#staged_units[@]} == 0 )); then
  echo "service update staging contains no systemd units" >&2
  exit 1
fi

declare -a new_units=()
declare -a changed_units=()

for staged_unit in "${staged_units[@]}"; do
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

  # Restart a service when a repository-managed file named in ExecStart has
  # changed. This catches interpreted programs such as audiobook_server.py,
  # where the executable itself (/usr/bin/python3) does not change.
  while IFS= read -r managed_path; do
    relative_path="${managed_path#/home/pi/scripts/}"
    staged_path="$staged_scripts/$relative_path"
    live_path="$live_scripts/$relative_path"
    if [[ -f "$staged_path" ]] && \
       { [[ ! -e "$live_path" ]] || ! cmp -s "$staged_path" "$live_path"; }; then
      changed=true
    fi
  done < <(
    sed -n 's/^ExecStart=//p' "$staged_unit" \
      | grep -oE '/home/pi/scripts/[^[:space:]\"]+' \
      || true
  )

  if [[ "$changed" == true && "$is_new" == false ]]; then
    changed_units+=("$unit")
  fi
done

mkdir -p "$live_scripts"
cp -a "$staged_scripts/." "$live_scripts/"
# Preserve the old sync behavior for top-level scripts, and do it before any
# service restart so directly executed shell scripts remain runnable.
chmod 770 "$live_scripts"/*

for staged_unit in "${staged_units[@]}"; do
  sudo install -m 0644 "$staged_unit" "$live_services/${staged_unit##*/}"
done

# systemd may still have a stale cached unit even when the staged and on-disk
# files match (for example, after an earlier copy that missed daemon-reload).
sudo systemctl daemon-reload

for unit in "${new_units[@]}"; do
  echo "NEW UNIT: $unit (enabling and starting)"
  sudo systemctl enable --now "$unit"
done

for unit in "${changed_units[@]}"; do
  echo "UPDATED UNIT: $unit"
  if sudo systemctl is-active --quiet "$unit"; then
    sudo systemctl restart "$unit"
    echo "RESTARTED UNIT: $unit"
  else
    echo "NOT ACTIVE; NOT STARTED: $unit"
  fi
done
