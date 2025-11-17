### CANBUS ###
alias pcanf='cd /home/pi/pcan; ls -lah;'

alias canhelp="sudo ip link add can0 type can help"
alias canup="sudo ip link set can0 up && ip -details link show can0"
alias candown="sudo ip link set can0 down 2>/dev/null"
alias canshow="ip -details link show can0"
alias cand="candump -ade can0"
alias candd='candump -dL -t A can0'
alias canlog="cd $HOME/log/can; ls -lah"

alias canif="ifconfig can0"
alias mxmon="cand | grep -Pv '  02 7E 00|  02 3E 00 00 00 00 00 00' | grep 18DA"
fdiag() { echo "$1" | grep 18DA; }
ffdiag() { grep 18DA "$1"; }
uniq_to_file() { orig=$1; file2=$2; grep -v -f "$orig" "$file2"; }
hex="[\d(A-F)]"

canmxid=18DAF140
mxgrep() { cat "$1" | grep $canmxid ; }
cans() {
  if [ -n "$2" ]; then
    canid=$1
    data=$(echo $2 | sed 's| ||g')
  else
    canid=$(echo "$1" | sed 's|#| |g' | grep -Po "$hex{8} " | sed 's| ||g')
    data="$(echo "$1" | sed 's|#| |g' | grep -Po " ($hex$hex )+" | sed 's| ||g')"
  fi
  echo "sending ${canid}#${data}"
  cansend can0 "${canid}#${data}"
}


canlive() { cand | grep -Poi "\s[\d(A-F)]{8}" | sed 's| ||g' | sort -u; }
canuniq() { cat "$1" | sort -u; }
canuids() { cat "$1" | grep -Poi "\s[\d(A-F)]{8}" | sed 's| ||g' | sort -u; }
canuids_count_all() { canuids "$1" | while read uid; do echo -e "`grep -Po "$uid.*" $1` | `grep -c $uid $1`"; done; }
canuids_count() { canuids_count_all "$1" | grep \|; }
cansr() {
  while read line; do
    echo "$line"
    cans "$line"
    sleep 1
  done < "$1"
}
can_find() { candd | grep -Pq "$1"; }

cansec() {
  while true; do cansr /home/pi/pcan/every_second.txt; done
}

canspam() { echo "$1" | while read line; do cans "$line"; done; }
caninit() {
  if [[ "$1" == "b" ]]; then br=50000; 
  elif [[ "$1" == "c" ]]; then br=500000; 
  else return 1; fi;
  shift 
  if incl "lo" $@; then lo="listen-only on"; else lo="listen-only off"; fi;
  if incl "lb" $@; then lb="loopback on"; else lb=""; fi;
  if incl "fd" $@; then fd="fd on"; else fd=""; fi;
  if incl "os" $@; then os="one-shot on"; else os=""; fi;
  echo "options $lo $lb $fd $os"
  candown
  sudo ip link set can0 type can bitrate $br restart-ms 100 $lo $lb $fd
  canup
  canshow
}

play_alert() {
  sns rear_movie
  sns vol_eql_all 50
  vlc "/home/pi/soundbytes/$1" &
  sleep 10
  pk vlc
}
cwait() {
  until can_find "2200[B-F]${hex}0000000${1}00"; do sleep 0.4 ; done
  echo FOUND
  candd > /home/pi/pcan/def_test_begin &
  play_alert emergency-alarm-with-reverb-29431.mp3
  sleep 6000
  pk candump
}
canotif() {
  sns rear_movie
  while can_find "2200[B-F]${hex}0000000${1}00"; do 
    echo FOUNDNOTIF
    sns vol_eql_all 50
    vlc "/home/pi/soundbytes/call-to-attention-123107.mp3" &
    sleep 5
    pk vlc
  done
}
