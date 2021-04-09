#! /bin/bash
shopt -s expand_aliases # run aliased commands inline with ssh ...@.... 
alias sudo='sudo '
alias ch7="sudo chmod -R 777" # usage: $ ch7 .
alias chme="sudo chown -R $(whoami)" # usage: $ chme .

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

alias bashp='vi ~/.bashrc'
alias rbash='exec bash'
alias init_rsa="ssh-copy-id -i ~/.ssh/id_rsa.pub" # init_rsa user@device
alias functions="cat ~/.bashrc | grep -E '^[[:space:]]*([[:alnum:]_]+[[:space:]]*\(\)|function[[:space:]]+[[:alnum:]_]+)'"
fndef() { # print function definition
  sed -n -e "/$1()/,/}/ p" ~/.bashrc
}

tf() {
  tail -50 $1; tail -f $1
}
snh() {
  nohup bash -c $1 &
  tail -f ./nohup.out
}
s() {
  . $HOME/scripts/$1.sh
}

alias isw="$HOME/scripts/internet_switches.sh"
alias iswl="tf /var/log/cron/internet_switches.log"
isw="$HOME/scripts/internet_switches.sh"
mconf="$HOME/mconf"

alias mconf="ls $mconf"
alias mreset="rm $mconf/*"
alias mdisk="rm $mconf/nodisk*; touch $mconf/mdisk; nohup $isw &"
alias mdiskx="rm $mconf/mdisk*; nohup $isw &"
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

# lsof | grep /mnt/mbbackup
# fuser -mv /mnt/mbbackup

alias disks='grep "dev/sd" /proc/mounts'
alias mounts='grep "dev/sd" /proc/mounts'
alias blk="sudo blkid | grep 'dev/sd'"
remount() {
  pk qbit
  /usr/sbin/service smbd stop
  s umount_all
  s mount_all
  s fix_hfs_fs
  /usr/sbin/service smbd start
  
  mounts
}

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

alias sns='bash ~/sns.sh'
alias gpu_mem='vcgencmd get_mem gpu'

# MEDIA

play() {
  dir=`dirname "$1"`
  filename=`basename "$1"`
  echo "play $1"
  cd "$dir"
  filenames=`ls | awk "/$filename/{y=1}y"` # everything after & including match
  if [[ "$2" == "-r" ]]; then filenames=`ls -I 'nohup.out' | shuf`; fi
  echo "filenames: $filenames"
  
  ! [ "$filenames" ] && echo "NO MATCH!" && return 0
  sudo pkill -f omxplayer
  sudo pkill -f vlc
  bash ~/sns.sh rear_movie
  xset s reset # wake display
  nohup vlc -f --sub-language=EN,US,en,us,any $filenames &
  # vlc -f $filenames
  sudo renice -12 -g  `pgrep vlc`

}

playf() {
  name=`echo $1 | perl -pe 's/ /_/g'`
  echo "name $name"
  ep=$2 # episode number eg 304 (parsed from S03E04)
  num_re='^[0-9]+$'
  # readarray -d '' match_arr < <(find /mnt/movingparts/links -type l -iname "*$name*" -print0)
  readarray -d '' match_arr < <(find /mnt/bigboi/mp_backup/links -type l -iname "*$name*" -print0 | find /mnt/movingparts/links -type l -iname "*$name*" -print0)
  # [[ ${#match_arr[@]} -eq 1 ]] && play "${match_arr[0]}" && return 0
  if [[ ! "$2" && ${#match_arr[@]} -gt 1 ]]; then 
    ls `dirname "${match_arr[0]}"`
    echo "^^^ Available matches ^^^"
    return 0;
  fi
  if [ -z "$2" ] || [[ "$2" == "-r" ]]; then play ${match_arr[0]} -r && return 0; fi
  for line in "${match_arr[@]}"; do 
    matcher=$line
    if [[ $ep =~ $num_re ]] ; then
      matcher=`echo $matcher | sed -e 's|[^0-9]*||g'` # parsed numbers
    fi
    echo "matcher $matcher"
    if [[ "${matcher,,}" == *"${ep,,}"* ]]; then # case-insensitive match
      echo "playing $line"
      play "$line" "$2"
      return 0
    fi
  done
}

cv() {
  matches=`find . -maxdepth 1 -iname "*$1*"`
  echo "Matches: $matches"
  if [[ $matches ]]; then 
    cd "$matches" || echo "multiple matches"
  else echo "no match" 
  fi
}

fgp() {
  find /mnt/movingparts/links \( -type l \) -iname "*$1*"
}

vlcmd() {
  cmd=$1
  param=$2
  # xset s reset # wake display
  dbus-send --type=method_call --dest=org.mpris.MediaPlayer2.vlc /org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player.$cmd $param
}

alias pp="vlcmd PlayPause"

vlcr() {
  grep -i "$1" "$HOME/vlc-recent.txt"
}

play_status() {
  omx_pos=`curl "http://0.0.0.0:2020/position"`
  if [[ $omx_pos ]]; then
    printf "$omx_pos"
  elif [[ `pgrep vlc` ]]; then
    position=`py scripts/python/vlc_property.py Position`
    total=`py scripts/python/vlc_property.py TotalTime`
    title=`py scripts/python/vlc_property.py Title`
    printf "$title \r$position / $total"
  fi
}
  
alias movies='cd /mnt/movingparts/links/Movies && ls | sed "s|\.| |g" | sed "s| ...$||g"'
alias docu='cd /mnt/movingparts/links/Documentaries && ls | sed "s|\.| |g" | sed "s| ...$||g"'
tv(){
  cd /mnt/movingparts/links/TV || cd /mnt/bigboi/mp_backup/links/TV;
  [[ "$#" = "1" ]] && cd "`find . -maxdepth 1 -name "*$1*"`"
  ls
}

alias torrent='cd /mnt/movingparts/torrent; ls -lh;'
alias links='cd /mnt/movingparts/links; ls -lh;'
alias mp='cd /mnt/movingparts/'
alias bb='cd /mnt/bigboi/'
alias mnt='cd /mnt'

alias am=". $HOME/scripts/alias_media.sh"

alias ifonline="ssh root@OpenWrt mwan3 interfaces | grep 'is online'"

alias cast="sudo pkill -f 'python3 server.py'; cd /home/pi/NativCast/; nohup python3 server.py &"
alias castnn="sudo pkill -f 'python3 server.py'; cd /home/pi/NativCast/; python3 server.py"

alias rpiplay='nohup /home/pi/RPiPlay/build/rpiplay -r 180 &'

alias pd='sudo /sbin/shutdown -r now'
alias rb='sudo reboot'

alias py="python3"
alias pip3="python3 -m pip"

rhp() {
  if [[ `ps ax` == *"rpiplay"* ]]
  then sudo pkill -f rpiplay
  else rpiplay
  fi
}

### GIT ###
alias gpo="git push origin"
alias gckm="git checkout master"
alias gac="git add .; git commit -m"
alias gp="git pull"
alias grao="git remote add origin"
alias gc='git clone'
alias gck='git checkout'

alias grep='grep --color=auto'
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
  echo "killing processes:\n$detail_list"
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


pk() { # kill process by name match - append flag '-9' for SIGTERM
  search_terms=$*
  last="${@: -1}" # remove last arg
  if [[ $last == '-9' ]]; then
    search_terms=${@:1:-1}
  else
    last=""
  fi
  pids=`pgrep -f $(echo $search_terms)`
  echo "found $pids"
  if [[ ! $pids ]]; then echo "no match" && return 0; fi
  sudo kill $last $(echo $pids)

  alive=`pgrep -f $(echo $search_terms)`
  if [[ $alive ]]; then 
    echo "still alive: $alive"
    echo "re run with '-9' to SIGKILL"
  else
    echo "all terminated"
  fi
}


export DISPLAY=:0
export HISTSIZE=100000
export HISTFILESIZE=1000000
export PATH="$PATH:/home/pi/.local/bin"
# if [ -f ~/.mount_aliases ]; then . ~/.mount_aliases; fi
if [ -f ~/.bash_defaults ]; then . ~/.bash_defaults; fi