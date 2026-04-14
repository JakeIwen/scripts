#! /bin/ash

ssid=$(iwgetid -r)
profile_path="/etc/persistent/profiles/$ssid"
if [ "$(cat "$profile_path")" = "$(cat /tmp/system.cfg)" ]; then
  echo "profile unchanged, done."
else
  echo "saving/updating profile: $ssid"
  cp /tmp/system.cfg "$profile_path"
  chmod 750 "$profile_path"
  cfgmtd -w -p /etc/
fi