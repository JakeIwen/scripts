#! /bin/bash
dsc="/Users/jacobr/dev/scripts"
repo_scripts="$dsc/pi/scripts"
services="$dsc/pi/services"
tmpfiles="$dsc/pi/tmpfiles.d"
hooks="$dsc/pi/hooks"
twilio="$dsc/pi/secrets/.twilio"
configs="$dsc/pi/configs"
secrets="$dsc/pi/secrets"
shared_sh="$dsc/shared/sh"
pi_ip='pi@vanpi.lan'
# pi_ip='pi@100.82.91.76'
package_units=""

sync_preflight() {
  # Refuse before staging or updating anything: the dashboard's new unit and
  # imports need the supervised, coupled compute cutover to have completed.
  if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$pi_ip" \
    /usr/bin/python3 -B - < "$dsc/pi/check_compute_provider.py"; then
    echo "Compute provider preflight refused sync; run ./macbook/scripts/install_van_compute_worker.zsh from the owner's Terminal in the reviewed checkout first." >&2
    return 1
  fi

  if ! package_units="$(python3 "$dsc/pi/deploy_python.py" --list-units)" ||
    [[ -z "$package_units" ]]; then
    echo "Python package unit discovery failed; see pi/deploy_python.py" >&2
    return 1
  fi

  if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$pi_ip" \
    'test -f /home/pi/scripts/python-packages/activated.json'; then
    echo "Python package activation preflight failed; see pi/docs/deployment.md" >&2
    return 1
  fi

  while IFS= read -r unit; do
    [[ -n "$unit" ]] || continue
    if [[ ! "$unit" =~ ^[A-Za-z0-9_.@:-]+\.service$ ]]; then
      echo "invalid Python package unit from deploy_python.py: $unit" >&2
      return 1
    fi
    if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$pi_ip" \
      "test -f '/home/pi/scripts/python-packages/service-state/$unit.json'"; then
      echo "Python package service activation preflight failed: $unit" >&2
      return 1
    fi
  done <<< "$package_units"
}

sync_non_python() {
  local_stage="$(mktemp -d "/tmp/vanpi-sync.XXXXXX")" || return 1
  staged_scripts="$local_stage/scripts"
  staged_services="$local_stage/services"
  staged_tmpfiles="$local_stage/tmpfiles.d"

  cleanup_local_stage() {
    rm -rf -- "$local_stage"
  }
  trap cleanup_local_stage EXIT

  /bin/mkdir -p "$staged_scripts" "$staged_services" "$staged_tmpfiles"
  # deploy_network_storage.py owns the recorder files it installs; never stage them.
  storage_managed="$dsc/pi/sync_storage_managed.exclude"
  package_units_exclude="$local_stage/python-package-units.exclude"
  if ! {
    while IFS= read -r unit; do
      [[ -n "$unit" ]] || continue
      printf '/%s\n' "$unit"
    done <<< "$package_units"
  } > "$package_units_exclude"; then
    return 1
  fi
  if [[ ! -s "$package_units_exclude" ]]; then
    echo "Python package unit exclusion list is empty" >&2
    return 1
  fi
  /usr/bin/rsync -a \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude-from="$storage_managed" \
    "$repo_scripts/" "$staged_scripts/" || return 1
  /usr/bin/rsync -a --exclude-from="$package_units_exclude" \
    --exclude-from="$storage_managed" \
    "$services/" "$staged_services/" || return 1
  /usr/bin/rsync -a --exclude-from="$storage_managed" \
    "$tmpfiles/" "$staged_tmpfiles/" || return 1
  cp -a "$shared_sh/." "$staged_scripts/"
  # deploy_python.py owns allowlisted Python deployment.

  # one multiplexed connection shared by every ssh/scp below: parallel transfers
  # ride it as channels instead of separate connections, so sshd's MaxStartups
  # limit (~10 concurrent handshakes) can't randomly drop any of them
  mux="-o ControlMaster=auto -o ControlPath=$local_stage/ssh-%C -o ControlPersist=120"
  ssh $mux $pi_ip true || { echo "can't reach $pi_ip"; return 1; }

  cp_services() {
    local remote_stage="/tmp/systemd-tmp.$$"
    ssh $mux $pi_ip "mkdir -p '$remote_stage'" || return 1
    scp $mux -r "$staged_services" "$staged_scripts" "$staged_tmpfiles" \
      "$pi_ip:$remote_stage/" || return 1
    ssh $mux $pi_ip "bash '$remote_stage/scripts/update_services.sh' '$remote_stage'"
  }

  # crontabs are no longer pulled here — repo is the source of truth now:
  # use pi/push_crontabs.sh to deploy, pi/pull_crontabs.sh to snapshot
  # Stage scripts and units together so services are restarted only after their
  # updated programs have been installed.
  cp_services &
  services_pid=$!

  # RASPI — files grouped by destination, one scp per group
  scp $mux "$dsc/pi/.bashrc" "$dsc/pi/canbus_funcs.sh" "$dsc/pi/sns.sh" "$dsc/pi/keepalive.txt" \
    "$configs/.bash_defaults" \
    "$configs/rsync-exclude-media.txt" \
    "$pi_ip:/home/pi/" &
  home_pid=$!

  scp $mux -r "$hooks" "$secrets" "$twilio" "$pi_ip:/home/pi/" &
  dirs_pid=$!

  # /etc/samba/smb.conf is root-owned; install it with sudo, and only when changed.
  cp_smb_conf() {
    local remote_tmp
    remote_tmp="$(ssh $mux $pi_ip 'mktemp /tmp/vanpi-smb.conf.XXXXXX')" || return 1
    scp $mux "$configs/smb.conf" "$pi_ip:$remote_tmp" || return 1
    ssh $mux $pi_ip "cmp -s '$remote_tmp' /etc/samba/smb.conf ||
      sudo install -m 0644 -o root -g root -- '$remote_tmp' /etc/samba/smb.conf
      status=\$?; rm -f -- '$remote_tmp'; exit \$status"
  }
  cp_smb_conf &
  smb_pid=$!

  sync_failed=0
  chmod_pid=""
  if wait "$home_pid"; then
    ssh $mux $pi_ip 'sudo chmod 770 /home/pi/rsync-exclude-media.txt' &
    chmod_pid=$!
  else
    echo "home-file deployment failed" >&2
    sync_failed=1
  fi
  if ! wait "$dirs_pid"; then
    echo "directory/secret deployment failed" >&2
    sync_failed=1
  fi
  if ! wait "$services_pid"; then
    echo "script/service deployment failed" >&2
    sync_failed=1
  fi
  if ! wait "$smb_pid"; then
    echo "Samba configuration deployment failed" >&2
    sync_failed=1
  fi
  if [[ -n "$chmod_pid" ]] && ! wait "$chmod_pid"; then
    echo "rsync-exclude permission update failed" >&2
    sync_failed=1
  fi
  if (( sync_failed )); then
    return 1
  fi
}

sync_python_packages() {
  python3 "$dsc/pi/deploy_python.py" --target "$pi_ip" --update || return 1
}

sync_compute() {
  # van_compute owns a coupled Mac/Pi deployment and must never be copied through
  # the generic script staging above. Its installer performs a cheap fingerprint
  # and health check, returning immediately when current. Keep it in the
  # foreground so this one-shot updater cannot report success before a required
  # compute upgrade has actually completed.
  compute_installer="$dsc/macbook/scripts/install_van_compute_worker.zsh"
  if ! "$compute_installer" --if-needed; then
    echo "conditional van_compute deployment failed" >&2
    return 1
  fi
}

source "$dsc/pi/sync_workflow.sh" || exit 1
run_sync_workflow
