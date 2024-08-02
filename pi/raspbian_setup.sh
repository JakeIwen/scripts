vi ~/.bash # not sudo
shopt -s dotglob 

sudo rpi-update
sudo cp -r /media/pi/rootfs/etc/ssh/* /etc/ssh/
sudo cp -r /media/pi/rootfs/home/pi/* /home/pi/
sudo chown -R pi /home/pi /etc/ssh

cd /mnt
sudo mkdir movingparts bigboi usbhfs
sudo chown -R pi .

sudo touch /home/pi/rsync-exclude-media.txt /home/pi/rsync-exclude.txt
sudo chmod 775 /home/pi/rsync-exclude-media.txt /home/pi/rsync-exclude.txt

exec bash
sudo systemctl start ssh

# from macbook: 
ssh-copy-id -i ~/.ssh/id_rsa.pub jacobr@mpro

sudo apt update
sudo apt-get install  bc gparted libusb-dev libdbus-1-dev libglib2.0-dev libudev-dev libical-dev libreadline-dev samba samba-common-bin dnsmasq hostapd bridge-utils qbittorrent-nox hfsutils hfsprogs python3-full

mkdir -p ~/scripts
cd ~/scripts
python -m venv python-automation
cd python-automation
pip3 install soco

mkdir -p /var/log/cron
sudo chmod 750 /var/log/cron
# (paste crontab)

# set samba password for user pi_backup
sudo smbpasswd -a pi

sudo raspi-config
  - interfacing options
  - vnc > yes
  
sudo curl -o /usr/local/bin/rmate https://raw.githubusercontent.com/aurora/rmate/master/rmate
sudo chmod +x /usr/local/bin/rmate
mv /usr/local/bin/rmate /usr/local/bin/ratom



ratom /etc/rc.local
 - replace resolution

wget https://static.adguard.com/adguardhome/release/AdGuardHome_linux_arm.tar.gz
tar xvf AdGuardHome_linux_arm.tar.gz

cd AdGuardHome
sudo ./AdGuardHome -s install


# mount a disk drive:
# prevent udisks2 automount
systemctl mask udisks2

sudo blkid

sudo mkdir /mnt/movingparts
sudo chmod 770 /mnt/movingparts
sudo mount /dev/sda2 /mnt/movingparts

sudo cp /etc/fstab /etc/fstab.backup
sudo nano /etc/fstab
add this text: /dev/sda1 /mnt/seegayte ext4 defaults,nofail,x-systemd.device-timeout=1 0 0

sudo reboot
sudo crontab -e

# backup sd card 
rsync -aHv --delete --exclude-from=/home/pi/rsync-exclude.txt / /mnt/movingparts/pi_backup/ 2>&1

# Widevine - Chromium "Media" Launcher. (Netflix etc)
curl -fsSL https://pi.vpetkov.net -o ventz-media-pi
sh ventz-media-pi

# disable automount for dives
# /home/pi/.config/pcmanfm/LXDE-pi/pcmanfm.conf
# should contain: 
# [volume]
# mount_on_startup=0
# mount_removable=0

# startup programs

sharing a network disk drive with osx netatalk
https://www.instructables.com/How-to-share-files-between-Mac-OSX-and-Raspberry-P/

sudo vi /etc/rc.local

# screen rotation
sudo vi /etc/xdg/lxsession/LXDE-pi/autostart
# add
@xrandr --output HDMI-1 --rotate inverted


# case insensitive completion:
echo 'set completion-ignore-case On' | sudo tee -a /etc/inputrc
