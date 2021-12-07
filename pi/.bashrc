#! /bin/bash
shopt -s expand_aliases # run aliased commands inline with ssh ...@.... 
alias sudo='sudo '
alias ch7="sudo chmod -R 777" # usage: $ ch7 .
alias chme="sudo chown -R $(whoami)" # usage: $ chme .

alias ubnt='ssh ubnt@192.168.8.20'
alias ngear='ssh -R root@192.168.6.1'
alias rb='. /home/pi/scripts/umount_all.sh; sudo reboot'
alias rball='ubnt reboot & ngear reboot & rb'

alias l='ls -lah'  ##custom list directory
alias lla='ls -ltu'
alias ..="cd .."
alias ...="cd ../.."
alias ....="cd ../../.."
alias .....="cd ../../../.."

alias vi='/usr/bin/vim.tiny'
alias rsynclog='cat /var/log/cron/rsync.log'
alias rscr="sudo systemctl restart cron.service"
alias ct="sudo crontab -e"

alias cronlog="cd /var/log/cron"
alias killcron="sudo pkill -f cron"
alias dirsize='sudo du -hsc .[^.]* *'
alias disku='df -u'
alias slp='xset s activate'
alias gtop="sudo /opt/vc/bin/vcdbg reloc stats"
alias ipinfo="py /home/pi/scripts/python/ip_info.py"
alias bashp='vi ~/.bashrc'
alias rbash='exec bash'
alias init_rsa="ssh-copy-id -i ~/.ssh/id_rsa.pub" # init_rsa user@device
alias functions="cat ~/.bashrc | grep -E '^[[:space:]]*([[:alnum:]_]+[[:space:]]*\(\)|function[[:space:]]+[[:alnum:]_]+)'"

fndef() { sed -n -e "/$1()/,/}/ p" ~/.bashrc; } # print function definition
tf() { tail ${2:-'-50'} $1; tail -f $1; }       # tail -f with more recent lines 
snh() { nohup bash -c $1 & tail -f ./nohup.out; }
s() { $HOME/scripts/$1.sh; }
alias iswl="tf /var/log/cron/internet_switches.log"
isw="$HOME/scripts/internet_switches.sh"
mconf="$HOME/mconf"


alias mconf="ls $mconf"
alias mreset="rm $mconf/*"
alias mdisk="rm $mconf/nodisk*; touch $mconf/mdisk; nohup $isw &"
alias mdiskx="rm $mconf/mdisk*; nohup $isw &"
alias idisk="rm $mconf/nodisk*; touch $mconf/idisk; nohup $isw &"
alias idiskx="rm $mconf/idisk*; nohup $isw &"
alias notor="rm $mconf/mtorrent*; touch $mconf/notorrent; nohup $isw &"
alias notorx="rm $mconf/notorrent*; nohup $isw &"
alias mtor="rm $mconf/notorrent*; touch $mconf/mtorrent; nohup $isw &"
alias mtorx="rm $mconf/mtorrent*; nohup $isw &"
alias nodisk="rm $mconf/mdisk*; touch $mconf/nodisk; nohup $isw &"
alias nodiskx="rm $mconf/nodisk*; nohup $isw &"

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

# MEDIA
export POSPATH="$HOME/vlc-positions.txt"
export VLCQTPATH="$HOME/.config/vlc/vlc-qt-interface.conf"
export VLCRPATH="$HOME/vlc-recent.txt"

escape_chars() { echo $1 | perl -ne 'chomp;print "\Q$_\E\n"'; }

kill_media() {
  log_position
  pk chrom omxplayer vlc
  sleep 2
  pk chrom omxplayer vlc -9
}

uridecode() { : "${*//+/ }"; echo -e "${_//%/\\x}"; }

resume() {
  path=`tail -1 "$POSPATH" | cut -d' ' -f1`
  decoded=`uridecode "$path"`
  echo "playing $decoded"
  play "$decoded" "$*"
}

res() {
  name=$1
  posline=`grep -ai "$name" "$POSPATH" | tail -1`
  path=`echo "$posline" | cut -d' ' -f1`"
  position=`echo "$posline" | cut -d' ' -f2`"
  decoded=`uridecode "$path"`
  epnum=`parse_episode_num $decoded`
  echo "playing $decoded $epnum"
  playf "$name" "$epnum"
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
# 
# sync_dirpath() {
#   # syncpath=$1
#   rempath='/Users/jacobr/Library/Application Support/BetterTouchTool'
#   syncpath=`echo $rempath | sed 's| |___|g'`
#   m1='jacobr@Jake-M113'
#   i9='jacobr@Jake-Machine'
#   locpath="/sync$syncpath"
#   mkdir -p "$locpath"
#   rsync -ur "$m1:$rempath" "$locpath"
#   rsync -ur "$i9:$rempath" "$locpath"
#   rsync -ur "$locpath" "$m1:$rempath"
#   rsync -ur "$locpath" "$i9:$rempath"
# }

get_last_position() {
  file=`echo $1 | sed 's|\/|\\/|g'`
  ns=`grep -Poa "(?<=${file} ).*" "$POSPATH"`
  echo "${ns:-0}"
}

playi() {
  subs='--sub-language=EN,US,en,us,any'
  nohup vlc -f "$subs" "$1" &
}

play() {
  path=$1
  dir=`dirname "$1"`
  filename=`basename "$1"`
  shift
  filename="$(escape_chars "$filename")"x 
  all_media=`find "$dir" -type l -not -iname nohup.out -print | sort -g`
  
  if [[ "$1" == "-r" ]]; then 
    filenames=`echo "$all_media" | shuf`;
    shift
  else 
    [[ "$1" == "-a" ]] && shift
    filenames=`echo "$all_media" | awk "/$filename/{y=1}y"`; # everything after & including match
  fi
  subs='--sub-language=EN,US,en,us,any'
  [[ "$1" == "-ns" ]] && subs='--sub-track=20'
  
  echo -ne "filenames: \n$filenames\n"
  echo "subs: $subs"
  
  ! [ "$filenames" ] && echo "NO MATCH!" && return 0
  kill_media
  bash ~/sns.sh rear_movie
  xset s reset # wake display
  nohup vlc -f $subs $filenames &
  sudo renice -12 -g  `pgrep vlc`
  last_position=$(get_last_position "$path")
  echo "last_position: $last_position"
  sleep 4
  [[ -n $last_position ]] && vlcmd Seek int64:${last_position}
}

parse_episode_num() {
  path=$1
  ep=$2
  cleanln=`echo $path | perl -pe 's|\_?\d{4}\_?||g'`
  season=`echo $cleanln | grep  -oE '(S|s)[[:digit:]][[:digit:]]' | tail -1` 
  ep=`echo $cleanln | grep  -oE '(E|e)[[:digit:]][[:digit:]]' | tail -1` 
  if [[ "$ep" && "$season" ]] ; then
    parsed_ep_num=`echo "${season}${ep}" | perl -pe 's|\D||g' | perl -pe 's|^0||g'` # parsed numbers
  else
    parsed_ep_num=`echo $cleanln | perl -pe 's|\_?\d{4,}\_?||g' | grep '[[:digit:]][[:digit:]][[:digit:]]'`
  fi
  echo $parsed_ep_num
}

playf() {
  name=`echo $1 | perl -pe 's/ /_/g'`
  ep=$2 # episode number eg 304 (parsed from S03E04) or flag -a, -r
  bb_links='/mnt/bigboi/mp_backup/links'
  mp_links='/mnt/movingparts/links'
  readarray -d '' match_arr < <( find "$bb_links" -type l -ipath "*$name*" -print0 | find "$mp_links" -type l -ipath "*$name*" -print0 | sort -z )
  if [ ${#match_arr[@]} -eq 0 ]; then echo "No matches found" && return 1; fi
  unset match_arr[-1] > /dev/null
  
  if [ -z "$ep" ]; then echo "no ep supplied"; fi
  
  if [[ ! "$ep" && ${#match_arr[@]} -gt 1 ]]; then 
    printf '%s\n' "${match_arr[@]/*\//}"
    echo -ne "^^^ Available matches ^^^ \n use flag -a to play all"
    return 0
  fi
  
  if [[ "$ep" == "-a" ]]; then play ${match_arr[0]} "$3" && return 0; fi
  if [ -z "$2" ] || [[ "$2" == "-r" ]]; then play ${match_arr[0]} -r "$3" && return 0; fi
  for line in "${match_arr[@]}"; do 
    ep_from_path=$(parse_episode_num $line $ep)
    # echo "path: $line, ep_from_path: $ep_from_path"
    if [[ "${ep_from_path,,}" == *"${ep,,}"* ]]; then # case-insensitive match
      echo "playing $line $2 $3"
      play "$line" "$2" "$3"
    fi
  done
}

cv() {\

  matches=`find . -maxdepth 1 -iname "*$1*"`
  echo "Matches: $matches"
  if [[ $matches ]]; then cd "$matches" || echo "multiple matches"; fi
}

fgp() {
  find /mnt/movingparts/links \( -type l \) -iname "*$1*"
}

vlcmd() {
  cmd=$1
  param=$2
  xset s reset # wake display
  dbus-send --type=method_call --dest=org.mpris.MediaPlayer2.vlc /org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player.$cmd $param
}

alias pp="vlcmd PlayPause"
vlcr() { grep -i "$1" "$VLCRPATH"; }
vlc_nosubs() { kill_media; resume -ns; }  
play_status() {
  omx_pos=`curl "http://0.0.0.0:2020/position"`
  if [[ $omx_pos ]]; then
    printf "$omx_pos"
  elif [[ `pgrep vlc` ]]; then
    position=`py scripts/python/vlc_property.py Position`
    total=`py scripts/python/vlc_property.py TotalTime`
    title=`py scripts/python/vlc_property.py Title`
    [[ -n "$title" ]] && log_position
    printf "$title \r$position / $total"
  fi
}

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
alias rpiplay='xset s reset; nohup /home/pi/RPiPlay/build/rpiplay -r 180 &'
# boost processess pushing netflix, may help with outher services
alias rechrome="sudo renice -12  \`ps aux --sort=%cpu | tail -3 | awk '{print \$2}'\`"
alias ngear="ssh root@OpenWrt"
alias ubnt="ssh ubnt@192.168.8.20"
alias pd='sudo /sbin/shutdown -r now'
alias am=''
alias py="python3"
alias pip3="python3 -m pip"

rhp() {
  if [[ `ps ax` == *"rpiplay"* ]]
  then sudo pkill -f rpiplay
  else rpiplay
  fi
}

ifonline() { # wan, clientwan, lifiwan
  ssh root@OpenWrt mwan3 interfaces | grep "$1 is online"
}

echo_if() { if [[ "$1" ]]; then echo $1; fi; }

van_is_running() {
  if test -f /home/pi/hooks/ignition_is_on; then echo "yes"; else echo "no"; fi
}

alias print_zrate="cat ~/log/zrate.txt"
zrate_stat(){ grep -Po '\d?\d?\.\d+' ~/log/zrate.txt | awk '{if(min==""){min=max=$1}; if($1>max) {max=$1}; if($1<min) {min=$1}; total+=$1; count+=1} END {print "avg " total/count," | max "max," | min " min}'; }
zrate_less_than() {
  lzr=`tail -1 ~/log/zrate.txt | grep -Po '^\d?\d?\.\d+'`
  if [[ "$lzr" ]] && (( $(echo "$lzr < $1" | bc -l) )); then 
    echo "LOW ZRATE: $lzr"
  fi
}
alias last_zrate="tail -1 ~/log/zrate.txt"
alias bisqactive="cat ~/log/zuseractive.txt | grep 'BISQ USER JHYY1 IS ACTIVE'"

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
alias hist="history | sed 's/ [0-9]*  //g'"

gacp() { git add .; git commit -m "$1"; git push; }
rgrep() { # recursively search, fallback to pwd "."
  grep -rni "$1" "${2:-.}" 
}
hgrep() {
  # howto: pass all args to a subfunction
  hist | grep "$@" | grep -v 'hgrep' | uniq -u
}
hcp() {
  val=$(hist | grep $1 | tail -1)
  echo $val | pbcopy
  echo "copied: $val"
}

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

rec_find_rpl_in_files() { # rec_find_rpl_in_files find_pattern repl_pattern
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

export DISPLAY=:0
export HISTSIZE=100000
export HISTFILESIZE=1000000
export PATH="$PATH:/home/pi/.local/bin"
# if [ -f ~/.mount_aliases ]; then . ~/.mount_aliases; fi
if [ -f ~/.bash_defaults ]; then . ~/.bash_defaults; fi