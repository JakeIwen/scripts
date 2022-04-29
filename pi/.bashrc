#! /bin/bash
shopt -s expand_aliases # run aliased commands inline with ssh ...@.... 
alias sudo='sudo '
alias vi='/usr/bin/vim.tiny'

alias l='ls -lah'
alias lla='ls -ltu'
alias ..="cd .."
alias ...="cd ../.."
alias ....="cd ../../.."
alias .....="cd ../../../.."

alias ch7="sudo chmod -R 777" # usage: $ ch7 .
alias chme="sudo chown -R $(whoami)" # usage: $ chme .

alias ubnt='ssh ubnt@192.168.8.20'
alias ngear='ssh -R root@192.168.6.1'
alias rb='. /home/pi/scripts/umount_disks.sh; sudo reboot'
alias rball='ubnt reboot; ngear reboot; rb'

alias ipinfo="py /home/pi/scripts/python/ip_info.py"
alias active_ssh_sessions="sudo netstat -tnpa | grep 'ESTABLISHED.*sshd'"
alias num_ssh="active_ssh_sessions | wc -l"

alias rscr="sudo systemctl restart cron.service"
alias ct="sudo crontab -e"
alias killcron="sudo pkill -f cron"

alias cronlog="cd /var/log/cron"
alias rsynclog='cat /var/log/cron/rsync.log'

alias dirsize='sudo du -hsc .[^.]* *'
alias disku='df -u'
alias slp='xset s activate'
alias gtop="sudo /opt/vc/bin/vcdbg reloc stats"
alias bashp='vi ~/.bashrc'
alias rbash='exec bash'

alias init_rsa="ssh-copy-id -i ~/.ssh/id_rsa.pub" # init_rsa user@device
alias functions="cat ~/.bashrc | grep -E '^[[:space:]]*([[:alnum:]_]+[[:space:]]*\(\)|function[[:space:]]+[[:alnum:]_]+)'"

fndef() { sed -n -e "/$1()/,/}$/ p" ~/.bashrc; } # print function definition
als() { alias $1; fndef $1; }
tf() { tail ${2:-'-50'} $1; tail -f $1; }       # tail -f with more recent lines 
snh() { nohup bash -c $1 & tail -f ./nohup.out; }

s() { name=$1; shift; $HOME/scripts/$name.sh $*; }
sx() { sudo "$(history | perl -pe 's/^\s+[0-9]+\**\s+//g' | tail -1)"; }

alias iswl="tf /var/log/cron/internet_switches.log"
isw="$HOME/scripts/internet_switches.sh"
mconf="$HOME/mconf"

alias mconf="ls $mconf"
alias mreset="rm $mconf/*; nohup $isw &"
alias mdisk="rm $mconf/nodisk*; touch $mconf/mdisk; nohup $isw &"
alias mdiskx="rm $mconf/mdisk*; nohup $isw &"
alias idisk="rm $mconf/nodisk*; touch $mconf/idisk; nohup $isw &"
alias idiskx="rm $mconf/idisk*; nohup $isw &"
alias notor="rm $mconf/mtorrent*; touch $mconf/notorrent; nohup $isw &"
alias notorx="rm $mconf/notorrent*; nohup $isw &"
alias mtor="rm $mconf/notorrent* $mconf/nodisk*; touch $mconf/mtorrent; nohup $isw &"
alias mtorx="rm $mconf/mtorrent*; nohup $isw &"
alias nodisk="rm $mconf/mdisk*; touch $mconf/nodisk; nohup $isw &"
alias nodiskx="rm $mconf/nodisk*; nohup $isw &"

alias canhelp="sudo ip link add can0 type can help"
alias canup="sudo ip link set can0 up && ip -details link show can0"
alias candown="sudo ip link set can0 down 2>/dev/null"
alias canshow="ip -details link show can0"
alias cand="candump -c -axde can0"
alias canlog="cd $HOME/log/can; ls -lah"

alias canif="ifconfig can0"
alias mxmon="cand | grep -Pv '  02 7E 00|  02 3E 00 00 00 00 00 00' | grep 18DA"
excl_to_file() {
  orig=$1
  file2=$2
  grep -v -f "$orig" "$file2"
}

canmxid=18DAF140
mxgrep() { cat "$1" | grep $canmxid ; }
cans() {
  if [ -n "$2" ]; then
    canid=$1
    data=$(echo $2 | sed 's| ||g')
  else
    hex="[\d(A-F)]"
    canid=$(echo "$1" | grep -Po "  $hex{8}  " | sed 's| ||g')
    data="$(echo "$1" | grep -Po " ($hex$hex )+ " | sed 's| ||g')"
  fi
  echo "sending ${canid}#${data}"
  cansend can0 "${canid}#${data}"
}
canuids() {
  file="$1"
  hex="[\d(A-F)]"
  cat "$file" | grep -Po "\s$hex{8}" | sed 's| ||g' | sort -u
}
cansr() {
  hex="[\d(A-F)]"
  echo "$1" | while read line; do
    canid=$(echo "$line" | grep -Po "  $hex{8}  " | sed 's| ||g')
    data="$(echo "$line" | grep -Po " ($hex$hex )+ " | sed 's| ||g')"
    echo "sending ${canid}#${data}"
    cansend can0 "${canid}#${data}"
    sleep 1
  done
}
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

rsmp() {
  rm -rf /mnt/movingparts/links/
  sudo rsync -avH --exclude-from=/rsync-exclude-media.txt /mnt/movingparts/ /mnt/bigboi/mp_backup
  . /home/pi/scripts/alias_media.sh
}

ssh_copy_id_dropbear() {
  if [ "$#" -ne 1 ]; then
    echo "Example: ${0} root@192.168.1.1"
    exit 1
  fi
  cat /home/pi/.ssh/id_rsa.pub | ssh ${1} "cat >> /etc/dropbear/authorized_keys && chmod 0600 /etc/dropbear/authorized_keys && chmod 0700 /etc/dropbear"
}

# lsof | grep /mnt/mbbackup
# fuser -mv /mnt/mbbackup

alias disks='grep "dev/sd" /proc/mounts'
alias mounts='grep "dev/sd" /proc/mounts'
alias blk="sudo blkid | grep 'dev/sd'"
alias blkg="sudo blkid | grep -Pi"

alias sns='bash ~/sns.sh'
alias gpu_mem='vcgencmd get_mem gpu'

alias remount='sudo su -c "/home/pi/scripts/remount.sh"'

airupnp() {
  if [[ "$1" == "disable" ]]; then
    sudo systemctl stop airupnp.service
    # dont fuck with the service files
    # sudo perl -i -pe 's|^|\# |g' /etc/systemd/system/airupnp.service
  else 
    sudo systemctl start airupnp.service
    # dont fuck with the service files
    # sudo perl -i -pe 's|^(\# )*||g' /etc/systemd/system/airupnp.service
  fi
}

set_vfat_uuid() {
  UUID=$1 # 1234-ABCF  hex only
  BLKID=$2 # /dev/sdc1
  valid=`echo "$UUID" | grep -P "^\d{4}\-[A-F]{4}$"`
  vfat=`blkid $BLKID | grep 'TYPE="vfat"'`
  
  echo "Current UUID:"
  sudo dd bs=1 skip=67 count=4 if=$BLKID 2>/dev/❤️ \
    | xxd -plain -u \
    | sed -r 's/(..)(..)(..)(..)/\4\3-\2\1/' 
    
  if [ -n "$valid" ] && [ -n "$vfat" ]; then
    printf "\x${UUID:7:2}\x${UUID:5:2}\x${UUID:2:2}\x${UUID:0:2}" \
      | sudo dd bs=1 seek=67 count=4 conv=notrunc of=$BLKID
    
    echo "Updated UUID:"
    sudo dd bs=1 skip=67 count=4 if=$BLKID 2>/dev/null \
      | xxd -plain -u \
      | sed -r 's/(..)(..)(..)(..)/\4\3-\2\1/' 
      
  else
    echo "UUID does not match '1234-ABCD' form"
    echo "or '`blkid $BLKID | grep -o 'TYPE="[^"]*"'`' is not a vfat partition"
  fi
}

# mntdsk sd_card 0383-ABDF
mntdsk() {
  fname=$1
  pth="/mnt/$fname"
  uuid=$($HOME/scripts/fetch_disk_uuid.sh $fname)
  fstype=`blkid | grep $uuid | grep -Po '(?<=TYPE=")[^"]*'`
  if [[ "$fstype" == "hfsplus" ]]; then opts="-o force,rw"; else opts=""; fi
  sudo mkdir -p $pth && sudo chown pi $pth  && sudo chmod 777 $pth 
  sudo mount -U $uuid -t $fstype $opts $pth && echo "mounted $fname at $pth"
}

# MEDIA
export POSPATH="$HOME/vlc-positions.txt"
export VLCQTPATH="$HOME/.config/vlc/vlc-qt-interface.conf"
export VLCRPATH="$HOME/vlc-recent.txt"

incl() { val="$1"; shift; printf '%s\0' "${@}" | grep -F -x -z "$val"; }
escape_chars() { echo $1 | perl -ne 'chomp;print "\Q$_\E\n"'; }
uridecode() { : "${*//+/ }"; echo -e "${_//%/\\x}"; } 

uniqct() { 
  file="$1"
  if [ "$2" = '-f' ]; then
    ext="${file##*.}"
    uniqfile="$(echo "$file" | sed "s|$ext$|uniq\.$ext|g" )"
    uniqctfile="$(echo "$file" | sed "s|$ext$|uniqct\.$ext|g" )"
    cat "$file" | sort -u > "$uniqfile"; 
    cat "$file" | sort | uniq -c | sort -nr > "$uniqctfile"; 
  else
    cat "$file" | sort | uniq -c | sort -nr;
  fi
}

kill_media() {
  log_position
  pk chrom omxplayer vlc
  sleep 2
  pk chrom omxplayer vlc -9
}

resume() {
  pth=`tail -1 "$POSPATH" | cut -d' ' -f1`
  decoded=`uridecode "$pth"`
  echo "playing $decoded"
  play "$decoded" "$*"
}

log_position() {
  [[ -z "$(pgrep vlc)" ]] && return 0
  file=`py ~/scripts/python/vlc_property.py URL`
  nsecs=`py ~/scripts/python/vlc_property.py NS`
  if [[ $file && $nsecs ]]; then 
    # sub_status=`tail -1 "$POSPATH" | cut -d' ' -f3`
    sed -i "\|^$file|d" $POSPATH
    echo "$file $nsecs" >> $POSPATH
  else
    echo "could not extract position on file: $file"
  fi
}

sync_dirpath() {
  # syncpath=$1
  syncpath='/Users/jacobr/Library/Application Support/BetterTouchTool/'
  m1='jacobr@Jake-M113'
  i9='jacobr@Jake-Machine'
  locpath="/sync$syncpath"
  mkdir -p "$locpath"
  rsync -ur "$m1:'$syncpath'" "$locpath"
  rsync -ur "$i9:'$syncpath'" "$locpath"
  rsync -ur "$locpath" "$m1:'$syncpath'"
  rsync -ur "$locpath" "$i9:'$syncpath'"
}

get_last_position() {
  file=`echo $1 | sed 's|\/|\\/|g'`
  ns=`grep -Poa "(?<=${file} ).*" "$POSPATH"`
  echo "${ns:-0}"
}

playi() {
  subs='--sub-language=EN,US,en,us'
  nohup vlc -f "$subs" "$1" &
}

play() {
  echo "play $*"
  pth=$1
  dir=`dirname "$pth"`
  filename=`basename "$pth"` # TODO if DIR do ELSE play mkv
  shift
  name="$(escape_chars "$filename")"
  all_media=`find "$dir" -type l -not -iname nohup.out -print | sort -g`
  filenames="$pth"
  
  if [[ -z "$1" ]]; then
    decoded=`uridecode "$pth"`
    epnum=`parse_episode_num $decoded`
    [ -n "$epnum" ] && playf "$name" "$epnum" && return 0
    echo "couldnt parse ep num, playing single file"
  elif [[ "$1" == "-r" ]]; then # play random
    shift
    filenames=`echo "$all_media" | shuf`
  elif [[ "$1" == "-a" ]]; then
    shift
    filenames=`echo "$all_media" | awk "/$name/{y=1}y"`; # everything after & including match
  else
    echo "NO MATCH!?"
  fi
  
  [[ "$1" == "-ns" ]] && subs='--sub-track=20' || subs='--sub-language=EN,US,en,us'
  kill_media > /dev/null
  xset dpms force on # wake display
  
  echo -ne "filenames: \n$filenames\n"
  echo "subs: $subs"
  
  bash ~/sns.sh rear_movie &
  
  nohup vlc -f $subs $filenames &
  sudo renice -12 -g  `pgrep vlc`
  last_position=$(get_last_position "$pth")
  echo "last_position: $last_position"
  sleep 4 # wait seconds before jumping to resume position
  [ -n "$last_position" ] && [ "$last_position" -gt "0" ] && vlcmd Seek int64:"$last_position"
}

parse_episode_num() {
  pth=$1
  ep=$2
  cleanln=`echo $pth | perl -pe 's|\_?\d{4}\_?||g'`
  season=`echo $cleanln | grep  -oE '(S|s)[[:digit:]][[:digit:]]' | tail -1` 
  ep=`echo $cleanln | grep  -oE '(E|e)[[:digit:]][[:digit:]]' | tail -1` 
  if [[ "$ep" && "$season" ]] ; then
    parsed_ep_num=`echo "${season}${ep}" | perl -pe 's|\D||g' | perl -pe 's|^0||g'` # parsed numbers
  else
    parsed_ep_num=`echo $cleanln | perl -pe 's|\_?\d{4,}\_?||g' | grep '[[:digit:]][[:digit:]][[:digit:]]'`
  fi
  echo $parsed_ep_num
}

media_by_name() {
  bb_links='/mnt/bigboi/mp_backup/links'
  mp_links='/mnt/movingparts/links'
  find "$bb_links" -type l -ipath "*$1*" -print0 | find "$mp_links" -type l -ipath "*$1*" -print0 | sort -z
}

playf() {
  name=`echo $1 | perl -pe 's/ /_/g'`
  ep=$2 # episode number eg 304 (parsed from S03E04) or flag -a, -r

  readarray -d '' match_arr < <( media_by_name "$name"  )
  [ ${#match_arr[@]} -eq 0 ] && echo "No matches found" && return 1
  
  if [[ ! "$ep" && ${#match_arr[@]} -gt 1 ]]; then 
    printf '%s\n' "${match_arr[@]/*\//}"
    echo -ne "^^^ Available matches ^^^ \n use flag -a to play all"
    return 0
  fi
  
  if [ -z "$ep" ] || [[ "$ep" == "-r" ]] || [[ "$ep" == "-a" ]]; then 
    play "${match_arr[0]}" "$ep" "$3"
  else
    #match ep
    for line in "${match_arr[@]}"; do 
      ep_from_path=$(parse_episode_num $line $ep)
      if [[ "${ep_from_path,,}" == *"${ep,,}"* ]]; then # case-insensitive match
        play "$line" "$ep" "$3" && return 0
      fi
    done
  fi
}

# ct=0
# for line in "$(history)"; do 
#   echo "$line \n"
#   echo "LOOP"
#   # if [ "`echo "$line" | wc -c`" -gt 10 ]; then
#   #   ct+=1
#   # fi
#   # ofst=`echo "$line \n" | cut -d' ' -f2`
#   # hdel $ofst
# done

# echo $ct
#   ep_from_path=$(parse_episode_num $line $ep)
#   if [[ "${ep_from_path,,}" == *"${ep,,}"* ]]; then # case-insensitive match
#     play "$line" "$ep" "$3" && return 0
#   fi
# done
cv() {
  matches=`find . -maxdepth 1 -iname "*$1*"`
  echo "Matches: $matches"
  if [[ $matches ]]; then cd "$matches" || echo "multiple matches"; fi
}


vlcmd() {
  cmd=$1
  param=$2
  xset dpms force on # wake display
  dbus-send --type=method_call --dest=org.mpris.MediaPlayer2.vlc /org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player.$cmd $param
}

alias pp="vlcmd PlayPause"
vlcr() { grep -i "$1" "$VLCRPATH" | head -10; }
vlc_nosubs() { kill_media; resume -ns; }  
play_status() {
  omx_pos=`curl "http://0.0.0.0:2020/position"`
  if [[ $omx_pos ]]; then
    printf "$omx_pos"
  elif [[ `pgrep vlc` ]]; then
    keys="multi|REQ|Hi10p|ETRG|YTM\.AM|SKGTV|CaLLiOpeD|CtrlHD|Will1869|10\.?Bit|DTS|DL|SDC|Atmos|hdtv|EVO|WiKi|HMAX|IMAX|MA|VhsRip|HDRip|BDRip|iNTERNAL|True\.HD|1080p|1080i|720p|XviD|HD|AC3|AAC|REPACK|5\.1|2\.0|REMUX|PRiCK|AVC|HC|AMZN|HEVC|Blu(R|r)ay|(BR|web)(Rip)?|NF|DDP?(5\.1|2\.0)?|(x|h|X|H)\.?26[4-5]|\d+mb|\d+kbps"
    groups="d3g|CiNEFiLE|CTR|PRoDJi|regret|deef|POIASD|Cinefeel|NTG|NTb|monkee|YELLOWBiRD|Atmos|EPSiLON|cielos|ION10|MeGusta|METCON|x0r|xlf|S8RHiNO|NTG|btx|strife|DD|DBS|TEPES|pawe|ggezl2006"
    delims="\.|\+|\-"
    pattern="($delims)(\[?($keys)\]?(?=\.)|(($groups)\.)?\.?$)|\'"
    position=`py scripts/python/vlc_property.py Position`
    total=`py scripts/python/vlc_property.py TotalTime`
    title=`py scripts/python/vlc_property.py Title | perl -pe "s~$pattern|~~g"`
    [[ -n "$title" ]] && log_position
    printf "$title \r$position / $total"
  fi
}
fgp() { find /mnt/movingparts/links \( -type l \) -iname "*$1*"; }

tv() {
  cd /mnt/bigboi/links/TV || cd /mnt/movingparts/links/TV
  [[ "$#" = "1" ]] && cd "`find . -maxdepth 1 -name "*$1*"`"
  ls
}
alias movies='cd /mnt/movingparts/links/Movies && ls | sed "s|\.| |g" | sed "s| ...$||g"'
alias docu='cd /mnt/movingparts/links/Documentaries && ls | sed "s|\.| |g" | sed "s| ...$||g"'
alias torrent='cd /mnt/movingparts/torrent; ls;'
alias new='cd /mnt/movingparts/links/New; ls;'
alias links='cd /mnt/movingparts/links; ls;'
alias inc='cd /mnt/movingparts/torrent/incomplete/; ls'
alias mp='cd /mnt/movingparts/'
alias bb='cd /mnt/bigboi/'
alias mnt='cd /mnt'
alias tor='cd /mnt/movingparts/torrent/'
alias inc='cd /mnt/movingparts/torrent/incomplete; ls -lah'

alias am=". ~/scripts/alias_media.sh"
alias ifaces="ssh root@OpenWrt mwan3 interfaces | grep 'is online'"
alias cast="sudo pkill -f 'python3 server.py'; cd /home/pi/NativCast/; nohup python3 server.py &"
alias castnn="sudo pkill -f 'python3 server.py'; cd /home/pi/NativCast/; python3 server.py"
alias rpiplay='xset dpms force on; nohup /home/pi/RPiPlay/build/rpiplay -r 180 &'
# boost processess pushing netflix, may help with outher services
alias rechrome="sudo renice -12  \`ps aux --sort=%cpu | tail -3 | awk '{print \$2}'\`"
alias ngear="ssh root@OpenWrt"
alias ubnt="ssh ubnt@192.168.8.20"
alias pd='sudo /sbin/shutdown -r now'
alias py="python3"
alias pip3="python3 -m pip"

rhp() {
  if [[ `ps ax` == *"rpiplay"* ]]
  then sudo pkill -f rpiplay
  else rpiplay
  fi
}

ifonline() { # wan, clientwan, lifiwan, (nothing)
  ssh root@OpenWrt mwan3 interfaces | grep "$1 is online"
}

echo_if() { if [[ "$1" ]]; then echo $1; fi; }

van_is_running() {
  if test -f /home/pi/hooks/ignition_is_on; then echo "yes"; else echo "no"; fi
}

alias print_zrate="cat ~/log/zrate.txt"
alias zrate_hourly="awk 'NR % 60 == 0' ~/log/zrate.txt"
zrate_by_hour() { # usage: zrate_by_hour Fri 09 AM
  grep -Pa "$1.* $2:.*$3" ~/log/zrate.txt | grep -Poa '\-?\d?\d?\.\d+' | awk '{if(min==""){min=max=$1}; if($1>max) {max=$1}; if($1<min) {min=$1}; total+=$1; count+=1} END {print total/count," | max "max," | min " min}';
}
zrate_stat(){ grep -Poa '\-?\d?\d?\.\d+' ~/log/zrate.txt | awk '{if(min==""){min=max=$1}; if($1>max) {max=$1}; if($1<min) {min=$1}; total+=$1; count+=1} END {print "avg " total/count," | max "max," | min " min}'; }
zrate_less_than() {
  lzr=`tail -1 ~/log/zrate.txt | grep -Po '^\d?\d?\.\d+'`
  [[ "$lzr" ]] && (( $(echo "$lzr < $1" | bc -l) )) && echo "LOW ZRATE: $lzr"
}
alias last_zrate="tail -1 ~/log/zrate.txt"
alias bisqactive="cat ~/log/zuseractive.txt | grep JHYY1"

### GIT ###
alias gpo="git push origin"
alias gckm="git checkout master"
alias gac="git add .; git commit -m"
alias gp="git pull"
alias grao="git remote add origin"
alias gc='git clone'
alias gck='git checkout'

alias grep='grep --color=auto'
alias grepi='grep -i --color=auto'
alias g='grep'
alias egrep='egrep --color=auto'
alias fgrep="find . \( -type d -o -type f \) -iname"
alias psgrep='ps -aef | grep'
alias agrep="alias | grep" # search aliases
alias hist="history | perl -pe 's/^\s+[0-9]+\**\s+//g'"

gacp() { git add .; git commit -m "$1"; git push; }
rgrep() { grep -rni "$1" "${2:-.}" ; }                    # recursively search, fallback to pwd "."
hgrep() { hist | grep "$@" | grep -v 'hgrep' | uniq -u; } # howto: pass all args to a subfunction
hgrepn() { history | grep "$@" | grep -v 'hgrep'; } # howto: pass all args to a subxnction
hdel() { history -d $1 && history -w; }

killport() {
  ARGS=("$@")
  detail_list=`lsof ${ARGS[@]/#/-i:}`
  echo -ne "killing processes:\n$detail_list"
  [ "$detail_list" ] && lsof ${ARGS[@]/#/-ti:} | xargs kill
}

inuse() {
  port=`if [[ "$#" = "1" ]]; then echo "$1"; else echo '[0-9][0-9][0-9][0-9][0-9]? '; fi`
  sudo lsof -P -n | grep -E ":$port"
  echo "port $port"
}
# rec_find_rpl_in_files find_pattern repl_pattern
rec_find_rpl_in_files() { 
  find . -exec sed -i '' "s|$1|$2|g" {} \;
}

file_lines() { # file_lines './filename.txt' echo   
  fpath=$1
  command=$2
  while read f  
  do $command $f
  done < $fpath
}

sns_list() {
  fpath="$HOME/scripts/python/sonos_tasks.py"
  while read f; do
    helpers=`echo $f | grep '# helpers'`
    if [[ $helpers ]]; then break; fi
    echo $f | grep '^def' | sed "s|^def\s||g"
  done < $fpath
}

add_python3_path() {
  name=$1
  pypath=$2
  SITEDIR=$(python3 -m site --user-site)
  mkdir -p "$SITEDIR" # create if it doesn't exist
  echo "$pypath" > "$SITEDIR/$name.pth"
  echo "added $name containing $pypath to $SITEDIR"
  l $SITEDIR
}

# add or update alias
nalias() {
  new_line="alias $1='$2'"
  file=$(cat ~/.bashrc)
  if [[ $file =~ $1\=.* ]]; then
    old_line=$(grep -i "^alias $1=" ~/.bashrc)
    old_line=$(echo "${old_line}" | sed -e 's/[]$.*[\^]/\\&/g' )
    sed -i -e "s|^alias $1\=.*|${new_line}|g"  ~/.bashrc
    echo "found alias, replaced: /^alias $1=.*/" 
    echo "with: $(grep -i "^alias $1=" ~/.bashrc)"
  else
    echo "appending to rc: $new_line" 
    echo "$new_line" >> ~/.bashrc
  fi
  # echo 'sending to MacBook'
  # scp  "$dsc/pi/.bashrc" "$HOME/.bashrc"
  
  echo 'reloading shell'
  exec bash
}

join_by() { local IFS="$1"; shift; echo "$*"; }

pk() { # kill process by name match - append flag '-9' for SIGTERM
  search_terms=$*
  k9="${@: -1}" # remove k9 arg
  if [[ $k9 == '-9' ]]; then search_terms=${@:1:$#-1}; else k9=""; fi
  
  joined_terms=`join_by '|' $(echo $search_terms)`
  pids=`pgrep -fi "$joined_terms" | sed "s|$$||g"`
  if [[ ! $pids ]]; then echo "no match" && return 0; fi
  echo $pids | while read -r pid; do echo "`sudo kill $k9 $pid`"; done
  sleep 1
  alive=`pgrep -fi "$joined_terms"`
  if [[ $alive ]]; then 
    echo "still alive: $alive"
    echo "re run with '-9' to SIGKILL"
  else
    echo "terminated $pids"
  fi
}

source ~/.twilio/twilio_creds.sh

export DISPLAY=:0
export HISTSIZE=100000
export HISTFILESIZE=1000000
export PATH="$PATH:/home/pi/.local/bin"
# if [ -f ~/.mount_aliases ]; then . ~/.mount_aliases; fi
if [ -f ~/.bash_defaults ]; then . ~/.bash_defaults; fi