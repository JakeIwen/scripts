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

sync_preflight() {
  if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$pi_ip" \
    'test -f /home/pi/scripts/python-packages/activated.json'; then
    echo "Python package activation preflight failed; see pi/docs/deployment.md" >&2
    return 1
  fi
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
  /usr/bin/rsync -a \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    "$repo_scripts/" "$staged_scripts/" || return 1
  /usr/bin/rsync -a --exclude 'van-dashboard.service' "$services/" "$staged_services/" || return 1
  /usr/bin/rsync -a "$tmpfiles/" "$staged_tmpfiles/" || return 1
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

  scp $mux "$configs/smb.conf" "$pi_ip:/etc/samba/smb.conf" &
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
  python3 "$dsc/pi/deploy_python.py" --target "$pi_ip" --legacy-flatten || return 1
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
