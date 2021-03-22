#! /bin/bash
dsc="/Users/jacobr/dev/scripts"
scripts="$dsc/pi/scripts"
vanrouter="$dsc/vanrouter"
configs="$dsc/pi/configs"
pi_ip='pi@raspberrypi.local'
vr_ip='root@192.168.6.1'
# 
mkdir "$scripts/python/" 
find "$dsc/automation/" -type f -name "*.py" -exec cp {} "$scripts/python/" \;
# 
scp -r "$scripts" "$pi_ip:/home/pi/"
scp  "$dsc/pi/.bashrc" "$pi_ip:/home/pi/.bashrc"
scp  "$dsc/pi/sns.sh"  "$pi_ip:/home/pi/sns.sh"

# configs
scp  "$configs/smb.conf" "$pi_ip:/etc/samba/smb.conf"
scp  "$configs/.bash_defaults" "$pi_ip:/home/pi/.bash_defaults"
scp  "$configs/.mount_aliases" "$pi_ip:/home/pi/.mount_aliases"
scp  "$configs/rsync-exclude.txt" "$pi_ip:/rsync-exclude.txt"
scp  "$configs/rsync-exclude-media.txt" "$pi_ip:/rsync-exclude-media.txt"
# scp "$scripts/mount_all.sh" "$pi_ip:/home/pi/scripts/mount_all.sh"

# scp "$dsc/pi/configs/pcmanfm.conf" "$pi_ip:/home/pi/.config/pcmanfm/LXDE/pcmanfm.conf"

rm -rf $scripts/python/

# scp -r "$vr_ip:/etc/config" "$vanrouter/etc/"
scp "$vanrouter/root/auto_dns.sh" "$vr_ip:/root/auto_dns.sh"
scp "$vanrouter/root/.profile" "$vr_ip:/root/.profile"
