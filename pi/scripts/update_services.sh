#! /bin/bash

new_svcs="$(comm -23 <(ls -1A "/tmp/systemd-tmp/services/" | sort) <(ls -1A "/etc/systemd/system/"))"
sudo mv /tmp/systemd-tmp/services/* /etc/systemd/system/
sudo systemctl daemon-reload

for filename in $new_svcs; do
  echo "NEW SERVICE: $filename"
  sudo systemctl enable $filename
  sudo systemctl start $filename
  sudo systemctl status $filename
done
