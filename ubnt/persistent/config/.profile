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
 cfgmtd -w -p /etc/
}
