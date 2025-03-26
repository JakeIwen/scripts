#! /bin/bash
shopt -s expand_aliases # run aliased commands inline with ssh ...@.... 
shopt -s dotglob # match all items in directory except . and ..

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
alias rball='ubnt reboot & ngear reboot; rb'

alias bootedit='sudo vi /boot/config.txt'
alias bootconf='sudo cat /boot/config.txt'

alias dirsize='sudo du -hsc .[^.]* *'
alias disku='df -h'
alias ipinfo="py /home/pi/scripts/python-automation/ip_info.py"
alias active_ssh_sessions="sudo netstat -tnpa | grep 'ESTABLISHED.*sshd'"
alias num_ssh="active_ssh_sessions | wc -l"

alias rscr="sudo systemctl restart cron.service"
alias ct="sudo crontab -e"
alias killcron="sudo pkill -f cron"
alias cronlog="cd /var/log/cron"
alias rsynclog='cat /var/log/cron/rsync.log'

alias gtop="sudo /opt/vc/bin/vcdbg reloc stats"
alias bashp='vi ~/.bashrc'
alias rbash='exec bash'

alias wake_display='xset dpms force on'
alias slp='xset s activate'

alias init_rsa="ssh-copy-id -i ~/.ssh/id_rsa.pub" # init_rsa user@device

alias corefreq='cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq; vcgencmd measure_volts'
set_date() { sudo date -s "$(wget -qSO- --max-redirect=0 google.com 2>&1 | grep Date: | cut -d' ' -f5-8)Z"; }
ssh_copy_id_dropbear() {
  if [ "$#" -ne 1 ]; then echo "Example: ${0} root@192.168.1.1" && exit 1; fi
  cat /home/pi/.ssh/id_rsa.pub | ssh ${1} "cat >> /etc/dropbear/authorized_keys && chmod 0600 /etc/dropbear/authorized_keys && chmod 0700 /etc/dropbear"
}

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

sync_comps() {
  sync_dirpath '/Users/jacobr/Library/Application Support/BetterTouchTool/'
  sync_dirpath '/Users/jacobr/router configs'
}

sync_new_torrents(){
  locpath=/Users/jacobr/Desktop/vidtmp
  pi_user='pi@vanpi.local'
  rempath="$pi_user:/mnt/movingparts/torrent/New"
  rsync -ur "$rempath/**/*.mkv" "$locpath"
}

sync_dirpath() {
  syncpath=$1
  m1='jacobr@Jake-M113'
  i9='jacobr@Jake-Machine'
  locpath="/sync$syncpath"
  mkdir -p "$locpath"
  rsync -ur "$m1:'$syncpath'" "$locpath"
  rsync -ur "$i9:'$syncpath'" "$locpath"
  rsync -ur "$locpath" "$m1:'$syncpath'"
  rsync -ur "$locpath" "$i9:'$syncpath'"
}

alias mconf="ls $mconf"
alias mreset="rm $mconf/*; nohup $isw &"
alias mdisk="rm $mconf/nodisk*; rm $mconf/notorrent*; touch $mconf/mdisk; nohup $isw &"
alias mdiskx="rm $mconf/mdisk*; nohup $isw &"
alias idisk="rm $mconf/nodisk*; touch $mconf/idisk; nohup $isw &"
alias idiskx="rm $mconf/idisk*; nohup $isw &"
alias notor="rm $mconf/mtorrent*; touch $mconf/notorrent; nohup $isw &"
alias notorx="rm $mconf/notorrent*; nohup $isw &"
alias mtor="rm $mconf/notorrent* $mconf/nodisk*; touch $mconf/mtorrent; nohup $isw &"
alias mtorx="rm $mconf/mtorrent*; nohup $isw &"
alias nodisk="rm $mconf/mdisk*; touch $mconf/nodisk; nohup $isw &"
alias nodiskx="rm $mconf/nodisk*; nohup $isw &"
umex() { . $HOME/scripts/umount_disks.sh EXFAT512; }

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

j() {
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

alias sns='bash ~/sns.sh'
alias gpu_mem='vcgencmd get_mem gpu'
alias gpu_mem_fix="vcgencmd cache_flush && sudo vcdbg reloc"
alias gpu_stat="sudo vcdbg reloc"   

incl() { val="$1"; shift; printf '%s\0' "${@}" | grep -F -x -z "$val"; }
escape_chars() { echo $1 | perl -ne 'chomp;print "\Q$_\E\n"'; }
uridecode() { : "${*//+/ }"; echo -e "${_//%/\\x}"; } 
trim_last_line() { sed -i '$ d' $1; } # filepath
join_by() { local IFS="$1"; shift; echo "$*"; }

### MEDIA/DISK ###
export POSPATH="$HOME/vlc-positions.txt"
export VLCQTPATH="$HOME/.config/vlc/vlc-qt-interface.conf"
alias mounts='grep "dev/sd" /proc/mounts'
alias blk="sudo blkid | grep 'dev/sd'"
alias drives="sudo lsblk -o NAME,FSTYPE,SIZE,MOUNTPOINT,LABEL"
alias pp="vlcmd PlayPause"
rmlast() { tail -n 1 "$1" | wc -c | xargs -I {} truncate "$1" -s -{}; }
vlcr() { grep -ia "$1" "$POSPATH" | tail -10; }
vlc_nosubs() { increment_global vlc_sub_track -1 2; resume ; }
vlc_subs_off() { echo '-1' > /home/pi/.vlc_sub_track; resume; }
vlcnice() { sudo renice ${1:-"12"} `pgrep vlc`; }
vlcnice2() { parent=`pgrep -p vlc`; ncn=${1:-"12"}; sudo renice $ncn ${parent##*( )}; }
get_global() { cat /home/pi/.$1; }
media_by_name() { find "$(avail_drive_path)/links" -type l -ipath "*$1*" -print0 | sort -z; }
fgp() { find /mnt/movingparts/links \( -type l \) -iname "*$1*"; }
tv() { cd /mnt/movingparts/links/TV || cd /mnt/bigboi/links/TV; ls; }

lastpos() { grep -Pai "$1" "$POSPATH"; }

md() { . /home/pi/scripts/mount_disks.sh "$@"; }
sysvol() { sudo amixer cset numid=1 ${1:-"90"}%; }
remount() {
  . /home/pi/scripts/umount_disks.sh "$@"
  . /home/pi/scripts/mount_disks.sh "$@"
  sudo service smbd start
}

rm_last_line() { sed -i '$ d' "$1"; }

rm_last_n_lines() {
  file="$2"
  awk -v n=$1 'NR==FNR{total=NR;next} FNR==total-n+1{exit} 1' "$file" "$file"
  cat "$file"
}

rm_match_lines() {
  term="$1"
  file="$2"
  cp "$file" "$file.bak"
  cat "$file.bak" | grep -via "$term" > "$file"
  cat "$file"
}

kill_media() {
  log_position
  pk chrom omxplayer vlc
  sleep 2
  pk chrom omxplayer vlc -9
}

print_vfat_uuid() {
  BLKID=$1 # /dev/sdc1
  sudo dd bs=1 skip=67 count=4 if=$BLKID 2>/dev/null \
    | xxd -plain -u \
    | sed -r 's/(..)(..)(..)(..)/\4\3-\2\1/'
}

set_vfat_uuid() {
  UUID=$1 # 1234-ABCF  hex only
  BLKID=$2 # /dev/sdc1
  valid=`echo "$UUID" | grep -P "^\d{4}\-[A-F]{4}$"`
  vfat=`blkid $BLKID | grep 'TYPE="vfat"'`
  
  echo "Current UUID: $(print_vfat_uuid $BLKID)"
    
  if [ -n "$valid" ] && [ -n "$vfat" ]; then
    printf "\x${UUID:7:2}\x${UUID:5:2}\x${UUID:2:2}\x${UUID:0:2}" \
      | sudo dd bs=1 seek=67 count=4 conv=notrunc of=$BLKID
    
    echo "Updated UUID: $(print_vfat_uuid $BLKID)"
  else
    [ -z "$valid" ] && echo "New UUID '$UUID' does not match '1234-ABCD' form"
    [ -z "$vfat" ] && echo "'`blkid $BLKID | grep -o 'TYPE="[^"]*"'`' is not a vfat partition"
  fi
}

fsprop() { 
  prop=$1 #  LABEL UUID TYPE PARTUUID PARTLABEL
  sterm=$2
  match="$(blkln "$sterm" | grep -Po "(?<= $prop=\")[^\"]*")"
  if [ -n "$sterm" ] && [ "$(echo "$match" | wc -l)" -gt 1 ]; then
    echo "multiple matches tosearch"
    echo "prop $prop"
    echo "sterm $sterm"
    echo "match $match"
  fi
  echo "$match"
}

blkln() { 
  sterm=$1
  match="$(/sbin/blkid | grep "$sterm")"
  echo "$match"
}

print_vfat_uuid() {
  BLKID=$1 # /dev/sdc1
  sudo dd bs=1 skip=67 count=4 if=$BLKID 2>/dev/null \
    | xxd -plain -u \
    | sed -r 's/(..)(..)(..)(..)/\4\3-\2\1/'
}

mntdsk() { # mntdsk sd_card 0383-ABDF
  fname=$1
  pth="/mnt/$fname"
  uuid=$($HOME/scripts/fetch_disk_uuid.sh $fname)
  fstype=`blkid | grep $uuid | grep -Po '(?<=TYPE=")[^"]*'`
  if [[ "$fstype" == "hfsplus" ]]; then opts="-o force,rw"; else opts=""; fi
  sudo mkdir -p $pth && sudo chown pi $pth  && sudo chmod 777 $pth 
  sudo mount -U $uuid -t $fstype $opts $pth && echo "mounted $fname at $pth"
}

resume() {
  if [ $# -eq 1 ] && [ -z "$( echo "$1" | grep -Po '^-')" ]; then
    # if first arg is not a switch ie starts with '-'
    pth=`grep -ia "$1" $POSPATH | tail -1 | cut -d' ' -f1`
    shift
  else
    pth=`tail -1 "$POSPATH" | cut -d' ' -f1` 
  fi
  
  if [ -z $pth ]; then
    playf "$1" 101
  else
    full_pth="$(avail_drive_path)/links$pth"
    echo "playing $full_pth"
    play "$full_pth"
  fi
}

log_position() { /home/pi/scripts/log_position.sh; }

get_last_position() {
  if [ -n "$1" ]; then
    # parse the big integer from the line after the filepath
    grep -Poai "(?<=$1 )\d{4,}" "$POSPATH"
  else
    echo 0
  fi
}

playp() {
  path="$(pwd)/$1"
  run_vlc "$path" 
}

media_all() {
  find "$(avail_drive_path)/links" -type l -not -iname nohup.out -print | sort -g
}

media_docs_movies() {
  base="$(avail_drive_path)/links"
  find "$base/Movies" "$base/Documentaries" | grep -Ev  '/Movies$|/Documentaries$'
}

play() {
  echo "play $*"
  sleep 1
  pth=$1
  # gpu_mem_fix > /dev/null
  # decoded=`uridecode "$pth"`=[]
  dir=`echo "$pth" | grep -Po '.*(New|TV|Movies|Documentaries)'`
  filename=`basename "$pth"` # TODO if DIR do ELSE play mkv
  if [ -z $dir ]; then
    unset epnum  
    unset name  
  else
    epnum=`parse_episode_num $pth`
    name="$(echo "$filename" | grep -Poi ".*(?=[_\.]S\d\d.*$)" | sed -E 's|[\\\.]|_|g' )"
  fi
  
  shift
  if [ -n "$epnum" ] && [ -n "$name" ] ; then
    echo "parsed $name $epnum"
    playf "$name" "$epnum"
    return 0
  elif [[ "$1" == "-r" ]]; then # play random
    shift
    filepaths=`echo "$(media_all)" | shuf`
  elif [[ "$1" == "-a" ]]; then
    shift
    filepaths=`echo "$(media_all)" | awk "/$name/{y=1}y"`; # everything a fter & including match
  elif [ -n "$pth" ]; then
    echo "couldnt parse ep num or name , playing single file: $pth"
    filepaths="$pth"
  else
    unset filename 
    unset filepaths  
    echo "NO MATCH!?"
    return 0
  fi
  
  run_vlc
}

increment_global() {
  name=$1; min=$2; max=$3;
  val=`cat /home/pi/.$name`
  val=$(( $val + 1 ))
  if [ -z $val ] || [ $val -gt $max ]; then val=$min; fi;
  echo $val > /home/pi/.$name
  echo $val
}

prep_for_media() {
  kill_media > /dev/null
  wake_display
  bash ~/sns.sh rear_movie &
}

run_vlc() {
  pth="$1"
  prep_for_media
  # subs="--sub-language=en"

  # subs="--sub-language=eng --sub-track=$(get_global vlc_sub_track)"

  if [ -n "$pth" ]; then
    decoded=`uridecode $pth`
  elif [ -n "$filepaths" ]; then
    decoded=`uridecode $filepaths`
    pth="$(echo $filepaths | grep -Po '^\S+')"
  else
    echo "no path or filepaths provided"
    return 0
  fi
  
  trupath="$(readlink "$pth")"
  echo "TRU PATH $trupath"
  json="$(mkvmerge -J "$trupath")"
  subtrack=`node ~/scripts/parse_sub_track.js "$json"`
  echo "playing subtrack $subtrack"
  
  subs="--sub-track=$subtrack"
  audio="--audio-language=en"
  ctrl="--control=dbus"
  
  nohup vlc --qt-minimal-view $ctrl $subs $audio $decoded &
  
  vlcnice -12 # give process priority to VLC
  vlc_jump_to_position "$pth"
}

vlc_jump_to_position() {
  path="$1"
  filename=`basename "$path"`
  last_position=$(get_last_position "$filename")
  
  echo "basename: $filename"
  echo "last_position: $last_position"
  sleep 4 # play for a few seconds before jumping to resume position
  [ -n "$last_position" ] && [ "$last_position" -gt "0" ] && vlcmd Seek int64:"$last_position"
}

parse_episode_num() {
  pth=$1
  epp=$2
  cleanln=`basename "$(echo $pth | tail -1)" | perl -pe 's|\_?\d{4}\_?||g'`
  season=`echo $cleanln | grep -Po '(?<=(S|s))\d\d' | head -1`
  epp=`echo $cleanln | grep -Po '(?<=(E|e))\d\d' | tail -1`
  if [[ "$epp" && "$season" ]] ; then
    echo "${season}${epp}" | perl -pe 's|^0||g' # parsed numbers
  else
    echo $cleanln | perl -pe 's|\_?\d{4,}\_?||g' | grep '\d\d\d'
  fi
}

avail_drive_path() {
  bb_links='/mnt/bigboi/mp_backup'
  mp_links='/mnt/movingparts'
  if [ -n "$drivepath" ] && [ "$drivepath" = "bb" ]; then echo $bb_links; fi
  if [ -e "$mp_links/torrent" ]; then echo $mp_links; else echo $bb_links; fi
}

playf() {
  name=`basename $1 | perl -pe 's/ /_/g' | perl -pe 's/\..*$//g'`

  if [ "$2" = "-l" ]; then # list files
    echo "available files"
    media_all | grep -i "$name"
    return 0
  elif [ "$2" = "-s" ]; then # start
    ep=101
  elif [ "$2" = "-r" ]; then
    filepaths=`media_all | grep -i "$name" | shuf`
    run_vlc
    return 0
  elif [ -z "$2" ]; then # movie
    filepaths=`media_docs_movies | grep -i "$name" | sort`
    run_vlc
    return 0
  fi
  
  ep=$2 # episode number eg 304 (parsed from S03E04) or flag -a, -r
  echo "name: $name, ep: $ep"
  
  readarray -d '' match_arr < <( media_by_name "$name"  )
    
  if [ ${#match_arr[@]} -eq 0 ]; then echo "No matches found" && return 1; fi
  # match ep
  for idx in "${!match_arr[@]}"; do
    dirplusname="$(echo ${match_arr[$idx]} | grep -Po '[^\/]+\/[^\/]+$')"
    ep_from_path=$(parse_episode_num $dirplusname $ep)
    if [[ "${ep_from_path,,}" == *"${ep,,}"* ]]; then # case-insensitive match
      filepaths="${match_arr[*]:$idx}" # slice to the end of the array
      run_vlc
      return 0
    fi
  done
}

vlcmd() {
  cmd=$1
  param=$2
  wake_display
  dbus-send --type=method_call --dest=org.mpris.MediaPlayer2.vlc /org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player.$cmd $param
}

play_status() {
  if [[ `pgrep vlc` ]]; then
    keys="multi|REQ|WEBRip|Hi10p|ETRG|YTM\.AM|SKGTV|CaLLiOpeD|CtrlHD|Will1869|10\.?Bit|DTS|DL|SDC|Atmos|hdtv|EVO|WiKi|HMAX|IMAX|MA|VhsRip|HDRip|BDRip|iNTERNAL|True\.HD|1080p|1080i|720p|XviD|HDR10|HD|AC3|AAC|REPACK|5\.1|2\.0|REMUX|PRiCK|AVC|HC|AMZN|HEVC|Blu(R|r)ay|(BR|web)(Rip)?|NF|DDP?(5\.1|2\.0)?|(x|h|X|H)\.?26[4-5]|\d+mb|\d+kbps"
    groups="d3g|CiNEFiLE|CTR|PRoDJi|regret|deef|POIASD|Cinefeel|NTG|NTb|monkee|YELLOWBiRD|Atmos|EPSiLON|cielos|ION10|MeGusta|METCON|x0r|xlf|S8RHiNO|NTG|btx|strife|DD|DBS|TEPES|pawel2006"
    delims="\.|\+|\-"
    pattern="$delims|$keys|$groups"
    # pattern="($delims)(\[?($keys)\]?(?=\.)|(($groups)\.)?\.?$)|\'"
    py_vlc_path='/home/pi/scripts/python-automation/vlc_property.py'
    position=`py $py_vlc_path Position`
    total=`py $py_vlc_path TotalTime`
    title=`py $py_vlc_path Title | perl -pe "s~$pattern|~~g"`
    # [[ -n "$title" ]] && log_position > /dev/null
    printf "$title \r$position / $total"
  else
    omx_pos=`curl "http://0.0.0.0:2020/position"`
    if [[ $omx_pos ]]; then printf "$omx_pos"; fi
  fi
}

alias movies='cd /mnt/movingparts/links/Movies && ls | sed "s|\.| |g" | sed "s| ...$||g"'
alias docu='cd /mnt/movingparts/links/Documentaries && ls | sed "s|\.| |g" | sed "s| ...$||g"'
alias torrent='cd /mnt/movingparts/torrent; ls;'
alias new='cd /mnt/movingparts/links/New; ls;'
alias links='cd /mnt/movingparts/links; ls;'
alias inc='cd /mnt/movingparts/torrent/incomplete/; ls'
alias mp='cd /mnt/movingparts/'
alias bb='cd /mnt/bigboi/'
mountbb() { s mount_disks bigboi; }
mountmp() { s mount_disks movingparts; }
umountbb() { s umount_disks bigboi; }
umountmp() { s umount_disks movingparts; }
alias mnt='cd /mnt'
alias tor='cd /mnt/movingparts/torrent/'
alias inc='cd /mnt/movingparts/torrent/incomplete; ls -lah'
alias backup_home='cd /mnt/bigboi/pi_backup_git/pi_backup/home/pi'

alias am=". ~/scripts/alias_media.sh"
alias an=". ~/scripts/alias_media.sh new"
alias ifaces="ssh root@OpenWrt mwan3 interfaces | grep 'is online'"
alias cast="sudo pkill -f 'python3 server.py'; cd /home/pi/NativCast/; nohup python3 server.py &"
alias castnn="sudo pkill -f 'python3 server.py'; cd /home/pi/NativCast/; python3 server.py"
alias rpiplay='wake_display; nohup /home/pi/RPiPlay/build/rpiplay -r 180 &'
# boost processess pushing netflix, may help with outher services
alias rechrome="sudo renice -12  \`ps aux --sort=%cpu | tail -3 | awk '{print \$2}'\`"
alias ngear="ssh root@OpenWrt"
alias ubnt="ssh ubnt@192.168.8.20"
alias pd='sudo /sbin/shutdown -r now'
alias py="python3"
alias pip3="python3 -m pip"

van_is_running() {
  if test -f /home/pi/hooks/ignition_is_on; then echo "yes"; else echo "no"; fi
}

### GIT ###
alias gpo="git push origin"
alias gckm="git checkout master"
alias gac="git add .; git commit -m"
alias gp="git pull"
alias grao="git remote add origin"
alias gc='git clone'
alias gck='git checkout'

### GREP ###
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
# rec_find_rpl_in_files find_pattern repl_pattern
rec_find_rpl_in_files() { find . -exec sed -i '' "s|$1|$2|g" {} \;; }
rm_lines() {
  pattern="$1"
  file="$2"
  grep -Pav "$pattern" "$2" > tmpfile && mv tmpfile "$2" && rm tmpfile
}
keepon() { while psgrep vlc && sleep 300; do xset dpms force on; done; }

file_lines() { # file_lines './filename.txt' echo   
  fpath=$1
  command=$2
  while read f
  do $command $f
  done < $fpath
}

### NETWORK/PROCESS ###

ifonline() { ssh root@OpenWrt mwan3 interfaces | grep "$1 is online"; }
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

rhp() {
  if [[ `ps ax` == *"rpiplay"* ]]
  then sudo pkill -f rpiplay
  else rpiplay
  fi
}

airupnp() {
  [[ "$1" == "disable" ]] && cmd=stop || cmd=start
  sudo systemctl $cmd airupnp.service
}
sns_list() {
  fpath="$HOME/scripts/python-automation/sonos_tasks.py"
  while read f; do       
    helpers=`echo $f | grep '# helpers'`
    if [[ $helpers ]]; then break; fi
    echo $f | grep '^def' | sed "s|^def\s||g"
  done < $fpath
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


source ~/.twilio/twilio_creds.sh

shopt -s histappend                      # append to history, don't overwrite it
export DISPLAY=:0
export HISTSIZE=1000000
export HISTFILESIZE=10000000
export PATH="$PATH:/home/pi/.local/bin"
export PYTHONPATH="/home/pi/scripts/python-automation"
export PROMPT_COMMAND="history -a; history -c; history -r; $PROMPT_COMMAND"

# if [ -f ~/.mount_aliases ]; then . ~/.mount_aliases; fi
if [ -f ~/.bash_defaults ]; then . ~/.bash_defaults; fi