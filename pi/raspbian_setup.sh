vi ~/.bash # not sudo

exec bash

sudo systemctl enable ssh
sudo systemctl start ssh

# from macbook: 
ssh-copy-id -i ~/.ssh/id_rsa.pub 192.168.6.103

sudo apt update
sudo apt install realvnc-vnc-server realvnc-vnc-viewer

sudo raspi-config
 - interfacing options
  - vnc > yes
  
curl -o /usr/local/bin/rmate https://raw.githubusercontent.com/aurora/rmate/master/rmate
sudo chmod +x /usr/local/bin/rmate
mv /usr/local/bin/rmate /usr/local/bin/ratom


apt install qbittorrent
apt install qbittorrent-nox


ratom /etc/rc.local
 - replace resolution

cd $HOME
wget https://static.adguard.com/adguardhome/release/AdGuardHome_linux_arm.tar.gz
tar xvf AdGuardHome_linux_arm.tar.gz

cd AdGuardHome
sudo ./AdGuardHome -s install


# mount a disk drive:
sudo blkid

sudo mkdir /mnt/movingparts
sudo chmod 770 /mnt/movingparts
sudo mount /dev/sda2 /mnt/movingparts

sudo cp /etc/fstab /etc/fstab.backup
sudo nano /etc/fstab
add this text: /dev/sda2 /mnt/movingparts ext4 defaults,nofail,x-systemd.device-timeout=1 0 0

sudo reboot

sudo crontab -e


# backup sd card 
rsync -aHv --delete --exclude-from=~/rsync-exclude.txt / /mnt/movingparts/pi_backup/ 2>&1

# Widevine - Chromium "Media" Launcher. (Netflix etc)
curl -fsSL https://pi.vpetkov.net -o ventz-media-pi
sh ventz-media-pi

# startup programs

sudo vi /etc/rc.local