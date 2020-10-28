alias l="ls -lah"
alias ..="cd .."
alias ...="cd ../.."
alias ....="cd ../../.."
alias .....="cd ../../../.."

alias hgrep="history | grep"
alias pgrep="ps lT | grep"

mkcdir ()
{
  mkdir -p -- "$1" &&
  cd -P -- "$1"
}
save_profile ()
{
 ssid=$(iwgetid -r)
 cp /tmp/system.cfg "/etc/persistent/profiles/$ssid"
 chmod 755 "/etc/persistent/profiles/$ssid"
 cfgmtd -w -p /etc/
}

set_ap ()
{
  cp "/etc/persistent/profiles/$1" /tmp/system.cfg
  /usr/etc/rc.d/rc.softrestart save
}

reset () {
  set_ap "reset"
}
