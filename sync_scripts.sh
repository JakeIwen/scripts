#! /bin/bash
dsc="/Users/jacobr/dev/scripts"
scripts="$dsc/pi/scripts"
hooks="$dsc/pi/hooks"
twilio="$dsc/pi/.twilio"
vanrouter="$dsc/vanrouter"
configs="$dsc/pi/configs"
pi_ip='pi@vanpi.local'
vr_ip='root@openwrt'

# PREP PYTHONS
mkdir "$scripts/python-automation/" 
find "$dsc/automation/" -type f -name "*.py" -exec cp {} "$scripts/python-automation/" \;
# scp $rem_addr/Users/jacobr/Downloads
# RASPI
scp  "$dsc/pi/.bashrc" "$pi_ip:/home/pi/.bashrc" &
scp  "$dsc/pi/sns.sh"  "$pi_ip:/home/pi/sns.sh" &
scp  "$dsc/pi/keepalive.txt"  "$pi_ip:/home/pi/keepalive.txt" &

scp -r "$scripts" "$pi_ip:/home/pi/" &
scp -r "$hooks" "$pi_ip:/home/pi/" &
scp -r "$twilio" "$pi_ip:/home/pi/" &
  
scp  "$dsc/NativCast/process.py"  "$pi_ip:/home/pi/NativCast/process.py" &
scp  "$dsc/NativCast/server.py"  "$pi_ip:/home/pi/NativCast/server.py" &

# configs
scp  "$configs/smb.conf" "$pi_ip:/etc/samba/smb.conf" &
scp  "$configs/.bash_defaults" "$pi_ip:/home/pi/.bash_defaults" &
scp  "$configs/.mount_aliases" "$pi_ip:/home/pi/.mount_aliases" &
scp  "$configs/.disk_uuids" "$pi_ip:/home/pi/.disk_uuids" &
scp  "$configs/rsync-exclude.txt" "$pi_ip:/home/pi/rsync-exclude.txt" &
scp  "$configs/rsync-exclude-media.txt" "$pi_ip:/home/pi/rsync-exclude-media.txt" &

ssh $pi_ip 'sudo chmod 775 /home/pi/rsync-exclude-media.txt /home/pi/rsync-exclude.txt'

# ROUTER
# scp -r "$vr_ip:/etc/config" "$vanrouter/etc/" &
# scp -r "$vanrouter/etc/config" "$vr_ip:/etc/" &
# # scp -r "$vanrouter:/etc/config" "$vr_ip/etc/" &
# scp "$vanrouter/root/auto_dns.sh" "$vr_ip:/root/auto_dns.sh" &
# scp "$vanrouter/root/.profile" "$vr_ip:/root/.profile" &
# # scp "$vanrouter/root/dnsmasq.awk" "$vr_ip:/root/dnsmasq.awk" &

# CLEANUP
sleep 4
rm -rf $scripts/python-automation/
