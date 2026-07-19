#! /bin/bash
dsc="/Users/jacobr/dev/scripts"
repo_scripts="$dsc/pi/scripts"
services="$dsc/pi/services"
hooks="$dsc/pi/hooks"
twilio="$dsc/pi/secrets/.twilio"
vanrouter="$dsc/vanrouter"
configs="$dsc/pi/configs"
secrets="$dsc/pi/secrets"
pi_apps="$dsc/pi/apps"
pi_python="$dsc/pi/scripts/python"
shared_python="$dsc/shared/python"
pi_ip='pi@vanpi.lan'
# pi_ip='pi@100.82.91.76'
vr_ip='root@openwrt'

local_stage="$(mktemp -d "${TMPDIR:-/tmp}/vanpi-sync.XXXXXX")" || exit 1
staged_scripts="$local_stage/scripts"

cleanup_local_stage() {
  rm -rf -- "$local_stage"
}
trap cleanup_local_stage EXIT

cp -a "$repo_scripts" "$staged_scripts"
python_stage="$staged_scripts/python-automation"
mkdir -p "$python_stage"
find "$pi_apps" "$pi_python" "$shared_python" -type f -name "*.py" \
  -exec cp {} "$python_stage/" \;
cp -R "$pi_apps/van_dashboard/templates" "$python_stage/"
cp -R "$pi_apps/van_dashboard/static" "$python_stage/"

# one multiplexed connection shared by every ssh/scp below: parallel transfers
# ride it as channels instead of separate connections, so sshd's MaxStartups
# limit (~10 concurrent handshakes) can't randomly drop any of them
mux="-o ControlMaster=auto -o ControlPath=$HOME/.ssh/mux-%C -o ControlPersist=120"
ssh $mux $pi_ip true || { echo "can't reach $pi_ip"; exit 1; }
ssh $mux $pi_ip 'mkdir -p /home/pi/configs'

cp_services() {
  local remote_stage="/tmp/systemd-tmp.$$"
  ssh $mux $pi_ip "mkdir -p '$remote_stage'" || return 1
  scp $mux -r "$services" "$staged_scripts" "$pi_ip:$remote_stage/" || return 1
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

scp $mux "$configs/price_checks.tsv" "$pi_ip:/home/pi/configs/" &
price_config_pid=$!

scp $mux "$configs/smb.conf" "$pi_ip:/etc/samba/smb.conf" &

wait $home_pid
ssh $mux $pi_ip 'sudo chmod 770 /home/pi/rsync-exclude-media.txt' &
wait $dirs_pid
wait $price_config_pid
wait $services_pid || { echo "script/service deployment failed" >&2; exit 1; }

# ROUTER
# scp -r "$vr_ip:/etc/config" "$vanrouter/etc/" &
# scp -r "$vanrouter/etc/config" "$vr_ip:/etc/" &
# # scp -r "$vanrouter:/etc/config" "$vr_ip/etc/" &
# scp "$vanrouter/root/auto_dns.sh" "$vr_ip:/root/auto_dns.sh" &
# scp "$vanrouter/root/.profile" "$vr_ip:/root/.profile" &
# # scp "$vanrouter/root/dnsmasq.awk" "$vr_ip:/root/dnsmasq.awk" &

wait
