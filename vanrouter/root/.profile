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
alias spd='t=$(date +"%s"); wget http://speedtest.tele2.net/10MB.zip -O ->/dev/null ; echo -n "MBit/s: "; expr 8 \* 10 / $(($(date +"%s")-$t))'
alias spdwan="spd -H netperf-west.bufferbloat.net"
alias spdclient="spd -H netperf-east.bufferbloat.net"
alias reload='/etc/init.d/network reload'
alias rl='reload'
alias functions="cat ~/.profile | grep -E '^[[:space:]]*([[:alnum:]_]+[[:space:]]*\(\)|function[[:space:]]+[[:alnum:]_]+)'"
alias cronlog='cd /root/log'
dnslog='/root/log/auto_dns.sh'
alias cstat='l /tmp/run/mwan3track; cat /tmp/run/mwan3track/*/*'
tf() { tail ${2:-'-50'} $1; tail -f $1; } # tail --follow with 50 or "$2" recent lines 
fndef() { sed -n -e "/$1()/,/}/ p" ~/.profile; }
rgrep() { grep -rni "$1" .; } # recursively search cwd 
piface() { ping -I "$1" 8.8.8.8; } # wlan1=lion_fone, wlan1-1=lifi eth0.2=wan
mkcdir() { mkdir -p -- "$1" && cd -P -- "$1"; }
rec_find_rpl_in_files() { find . -type f | xargs sed -i "s|$1|$2|g"; }
rec_rename() { find . -exec rename "s/$1/$2/g" {} +; }
truncate_log(){
  filename=$1
  num_lines=$2
  tail -n$num_lines $filename > $filename.tmp && mv $filename.tmp $filename
}

active_iface_scores() {
  find /tmp/run/mwan3track/*/SCORE | while read file; do
    printf "%d:%s\n" "$(cat $file)" "$(basename $(dirname $file))"
  done
}

active_iface() {
  active_iface_scores | sort | tail -1 | cut -d ':' -f2-
}


cd /etc/config

# find /tmp/run/mwan3track/*/ONLINE | xargs tail -n +1 | sed -e 's/==>/\n/g' -e 's/<==//g' -ern 's|(.*ONLINE \n)(\d+)|\2\1|g'

