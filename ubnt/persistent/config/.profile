alias l="ls -lah"
alias ..="cd .."
alias ...="cd ../.."
alias ....="cd ../../.."
alias .....="cd ../../../.."

alias hgrep="history | grep"
alias pgrep="ps lT | grep"
alias sudo=''

mkcdir ()
{
  mkdir -p -- "$1" &&
  cd -P -- "$1"
}
save_profile ()
{
 ssid=$(iwgetid -r || iwgetid ath0 -r)
 cp /tmp/system.cfg "/etc/persistent/profiles/$ssid"
 chmod 755 "/etc/persistent/profiles/$ssid"
 cfgmtd -w -p /etc/
}

set_ap ()
{
  cp "/etc/persistent/profiles/safehouse" /tmp/system.cfg
  /usr/etc/rc.d/rc.softrestart save
  pkill -f crond
  sleep 120
  echo "AP set. Restarting cron at $(date)"
}

nh_set_ap() {
  set_ap "$1" &> RESULT.txt
}

delete_ap ()
{
  rm "/etc/persistent/profiles/$1"
  cfgmtd -w -p /etc/
}

reset () {
  set_ap "reset"
}
