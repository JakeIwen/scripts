alias l="ls -lah"
alias ..="cd .."
alias ...="cd ../.."
alias ....="cd ../../.."
alias .....="cd ../../../.."

alias hgrep="history | grep"
alias pgrep="ps lT | grep"
alias ccq="mca-status | grep ccq | cut -d= -f2"
alias is_connected="mca-status | grep ccq | cut -d= -f2 | grep -v 0"
 
mkcdir () 
{
  mkdir -p -- "$1" &&
  cd -P -- "$1"
}

save_current() 
{
  ssid=$(iwgetid -r || iwgetid ath0 -r)
  cp /tmp/system.cfg "/etc/persistent/profiles/$ssid"
  chmod 750 "/etc/persistent/profiles/$ssid"
  cfgmtd -w -p /etc/
}

save_current_apmode() 
{
  ssid=$(iwgetid -r || iwgetid ath0 -r)
  cp /tmp/system.cfg "/etc/persistent/profiles/_AP_MODE_$ssid"
  chmod 750 "/etc/persistent/profiles/_AP_MODE_$ssid"
  cfgmtd -w -p /etc/
}

set_ap() 
{
  ssid="$1"
  profile_path="/etc/persistent/profiles/$ssid"
  scripts="/etc/persistent/scripts"
  cur_ssid="$(cat /tmp/system.cfg | grep 'wireless.1.ssid' | cut -d= -f2)"
  if [ "$cur_ssid" = "$ssid" ]; then
    echo "already using ssid $ssid, make sure the profile is named correctly"
  elif test -f "$profile_path"; then
    echo "updating AP to $ssid"
    $scripts/update_wifi.sh "$ssid" 
  else
    echo missing profile: "$profile_path"
  fi
}

cronbreak()
{
  pkill -f crond
  echo "cronbreak: Restarting cron 120s from $(date)"
  sleep 120
  crond
}

nh_set_ap() 
{
  set_ap "$1" &> RESULT.txt
}

delete_ap() 
{
  rm "/etc/persistent/profiles/$1"
  cfgmtd -w -p /etc/
}

del_current() 
{
  ssid=$(iwgetid -r || iwgetid ath0 -r)
  rm "/etc/persistent/profiles/$ssid" &> del_current.txt
  reset &> reset.txt
}

reset() 
{
  set_ap "reset"
}
