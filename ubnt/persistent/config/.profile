alias l="ls -lah"
alias ..="cd .."
alias ...="cd ../.."
alias ....="cd ../../.."
alias .....="cd ../../../.."

alias hgrep="history | grep"
alias pgrep="ps lT | grep"
alias ccq="mca-status | grep ccq | cut -d= -f2"
alias is_connected="mca-status | grep ccq | cut -d= -f2 | grep -v 0"

mkcdir () {
  mkdir -p -- "$1" &&
  cd -P -- "$1"
}

save_current() {
 ssid=$(iwgetid -r || iwgetid ath0 -r)
 cp /tmp/system.cfg "/etc/persistent/profiles/$ssid"
 chmod 755 "/etc/persistent/profiles/$ssid"
 cfgmtd -w -p /etc/
}

set_ap() {
  cp "/etc/persistent/profiles/$1" /tmp/system.cfg
  pkill -f crond
  /usr/etc/rc.d/rc.softrestart save
  sleep 120
  echo "AP set. Restarting cron at $(date)"
}

nh_set_ap() {
  set_ap "$1" &> RESULT.txt
}

delete_ap() {
  rm "/etc/persistent/profiles/$1"
  cfgmtd -w -p /etc/
}

del_current() {
  ssid=$(iwgetid -r || iwgetid ath0 -r)
  rm "/etc/persistent/profiles/$ssid" &> del_current.txt
  reset &> reset.txt
}

reset() {
  set_ap "reset"
}
