#! /bin/bash
dsc="/Users/jacobr/dev/scripts"
scripts="$dsc/pi/scripts"
services="$dsc/pi/services"
hooks="$dsc/pi/hooks"
twilio="$dsc/pi/secrets/.twilio"
vanrouter="$dsc/vanrouter"
configs="$dsc/pi/configs"
secrets="$dsc/pi/secrets"
pi_ip='pi@vanpi.lan'
# pi_ip='pi@100.82.91.76'
vr_ip='root@openwrt'

# one multiplexed connection shared by every ssh/scp below: parallel transfers
# ride it as channels instead of separate connections, so sshd's MaxStartups
# limit (~10 concurrent handshakes) can't randomly drop any of them
mux="-o ControlMaster=auto -o ControlPath=$HOME/.ssh/mux-%C -o ControlPersist=120"
ssh $mux $pi_ip true || { echo "can't reach $pi_ip"; exit 1; }

cp_services() {
  ssh $mux $pi_ip "mkdir -p /tmp/systemd-tmp/"
  scp $mux -r "$services/" "$pi_ip:/tmp/systemd-tmp/"
  ssh $mux $pi_ip "/home/pi/scripts/update_services.sh"
}

pull_crontabs() {
  ssh $mux $pi_ip "sudo crontab -l > /tmp/crontab"
  scp $mux "$pi_ip:/tmp/crontab" "$dsc/pi/crontab"
  ssh $mux $pi_ip "crontab -l > /tmp/crontab"
  scp $mux "$pi_ip:/tmp/crontab" "$dsc/pi/crontab_pi"
}

cp_services &
pull_crontabs &

# PREP PYTHONS
mkdir "$scripts/python-automation/"
find "$dsc/automation/" -type f -name "*.py" -exec cp {} "$scripts/python-automation/" \;
# scp $rem_addr/Users/jacobr/Downloads

# RASPI — files grouped by destination, one scp per group
scp $mux "$dsc/pi/.bashrc" "$dsc/pi/canbus_funcs.sh" "$dsc/pi/sns.sh" "$dsc/pi/keepalive.txt" \
  "$configs/.bash_defaults" "$configs/.disk_uuids" \
  "$configs/rsync-exclude.txt" "$configs/rsync-exclude-media.txt" \
  "$pi_ip:/home/pi/" &
home_pid=$!

scp $mux -r "$scripts" "$hooks" "$secrets" "$twilio" "$pi_ip:/home/pi/" &
dirs_pid=$!

scp $mux "$dsc/NativCast/process.py" "$dsc/NativCast/server.py" "$pi_ip:/home/pi/NativCast/" &
scp $mux "$configs/smb.conf" "$pi_ip:/etc/samba/smb.conf" &

wait $home_pid
ssh $mux $pi_ip 'sudo chmod 770 /home/pi/rsync-exclude-media.txt /home/pi/rsync-exclude.txt' &
wait $dirs_pid
ssh $mux $pi_ip 'sudo chmod 770 /home/pi/scripts/*' &

# ROUTER
# scp -r "$vr_ip:/etc/config" "$vanrouter/etc/" &
# scp -r "$vanrouter/etc/config" "$vr_ip:/etc/" &
# # scp -r "$vanrouter:/etc/config" "$vr_ip/etc/" &
# scp "$vanrouter/root/auto_dns.sh" "$vr_ip:/root/auto_dns.sh" &
# scp "$vanrouter/root/.profile" "$vr_ip:/root/.profile" &
# # scp "$vanrouter/root/dnsmasq.awk" "$vr_ip:/root/dnsmasq.awk" &

# CLEANUP
wait
rm -rf $scripts/python-automation/
