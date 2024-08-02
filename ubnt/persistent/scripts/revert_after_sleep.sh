#!/bin/bash
should_reset_path="/etc/persistent/should_reset"
save_current_apmode() 
{
  ssid=$(iwgetid -r || iwgetid ath0 -r)
  cp /tmp/system.cfg "/etc/persistent/profiles/_AP_MODE_$ssid"
  chmod 750 "/etc/persistent/profiles/_AP_MODE_$ssid"
  cfgmtd -w -p /etc/
}

set_ap() 
{
  profile_path="/etc/persistent/profiles/$1"
  if test -f "$profile_path"; then
    cp "$profile_path" /tmp/system.cfg
    pkill -f crond
    /usr/etc/rc.d/rc.softrestart save
    sleep 120
    echo "AP set. Restarting cron at $(date)"
  else
    echo missing profile: "$profile_path"
  fi
}

if test -f "$should_reset_path"; then
  save_current_apmode
  set_ap "reset"
  rm "$should_reset_path"
fi