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

declare -a new_services=()
declare -a changed_services=()
unit_files_changed=false

for staged_unit in "$staged_services"/*.service; do
  unit="${staged_unit##*/}"
  live_unit="$live_services/$unit"
  changed=false
  is_new=false

  if [[ ! -e "$live_unit" ]]; then
    is_new=true
    new_services+=("$unit")
    unit_files_changed=true
  elif ! cmp -s "$staged_unit" "$live_unit"; then
    changed=true
    unit_files_changed=true
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
    changed_services+=("$unit")
  fi
done

mkdir -p "$live_scripts"
cp -a "$staged_scripts/." "$live_scripts/"
# Preserve the old sync behavior for top-level scripts, and do it before any
# service restart so directly executed shell scripts remain runnable.
chmod 770 "$live_scripts"/*

for staged_unit in "$staged_services"/*.service; do
  sudo install -m 0644 "$staged_unit" "$live_services/${staged_unit##*/}"
done

if [[ "$unit_files_changed" == true ]]; then
  sudo systemctl daemon-reload
fi

for unit in "${new_services[@]}"; do
  echo "NEW SERVICE: $unit (enabling and starting)"
  sudo systemctl enable --now "$unit"
done

for unit in "${changed_services[@]}"; do
  echo "UPDATED SERVICE: $unit"
  if sudo systemctl is-active --quiet "$unit"; then
    sudo systemctl restart "$unit"
    echo "RESTARTED SERVICE: $unit"
  else
    echo "NOT ACTIVE; NOT STARTED: $unit"
  fi
done
