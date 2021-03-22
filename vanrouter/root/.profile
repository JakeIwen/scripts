#! /bin/ash

alias l='ls -lah' #custom list directory
alias ..="cd .."
alias ...="cd ../.."
alias ....="cd ../../.."
alias .....="cd ../../../.."
alias rbash='exec ash'
alias bashp='vi /root/.profile'
alias dirsize='du -hsc *'
alias disku='df -u'
alias spd="/etc/config/betterspeedtest.sh -4 -t 10"
alias reload='/etc/init.d/network reload'
alias rl='reload'
alias functions="cat ~/.profile | grep -E '^[[:space:]]*([[:alnum:]_]+[[:space:]]*\(\)|function[[:space:]]+[[:alnum:]_]+)'"

alias cstat='l /tmp/run/mwan3track; cat /tmp/run/mwan3track/*/*'

fndef() { # print function definition
  sed -n -e "/$1()/,/}/ p" ~/.profile
}
rgrep() {
  grep -rni "$1" . # recursively search cwd
}
piface() { # wlan1=lion_fone, wlan1-1=lifi
  ping -I "$1" 8.8.8.8
}
mkcdir() {
  mkdir -p -- "$1" && cd -P -- "$1"
}
rec_find_rpl_in_files() {
  find . -type f | xargs sed -i "s|$1|$2|g"
}
rec_rename() {
  find . -exec rename "s/$1/$2/g" {} +
}
truncate_log(){
  filename=$1
  num_lines=$2
  tail -n$num_lines $filename > $filename.tmp && mv $filename.tmp $filename
}

cd /etc/config

