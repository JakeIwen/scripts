#! /bin/bash
shopt -s expand_aliases
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
alias tf='tail -f'
alias dirsize='du -hsc *'
alias disku='df -u'
alias pk='sudo pkill -f'

alias bashp='vi ~/.bashrc'
alias rbash='exec bash'


s() {
  . $HOME/scripts/$1.sh
}

snh() {
  nohup bash -c $1 &
  tail -f ./nohup.out
}

alias isw="$HOME/scripts/internet_switches.sh"
alias iswl="tail -50 /var/log/cron/internet_switches.log; tail -f /var/log/cron/internet_switches.log"
mconf="$HOME/mconf"

alias mconf="ls $mconf"
alias mdisk="rm $mconf/nodisk; touch $mconf/mdisk; nohup $isw &"
alias mdiskx="rm $mconf/mdisk; nohup $isw &"
alias notor="rm $mconf/mtorrent; touch $mconf/notorrent; nohup $isw &"
alias notorx="rm $mconf/notorrent; nohup $isw &"
alias mtor="touch $mconf/mtorrent; nohup $isw &"
alias mtorx="rm $mconf/mtorrent; nohup $isw &"
alias nodisk="rm $mconf/mdisk; touch $mconf/nodisk; nohup $isw &"
alias nodiskx="rm $mconf/nodisk; nohup $isw &"

# lsof | grep /mnt/mbbackup
# fuser -mv /mnt/mbbackup

alias inuse='sudo lsof | grep'
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

alias sns='bash ~/sns.sh'
alias gpu_mem='vcgencmd get_mem gpu'

# MEDIA
alias movies='cd /mnt/bigboi/mp_backup/links/Movies && ls | sed "s|\.| |g" | sed "s| ...$||g"'
alias docu='cd /mnt/bigboi/mp_backup/links/Documentaries && ls | sed "s|\.| |g" | sed "s| ...$||g"'
tv(){
  cd /mnt/bigboi/mp_backup/links/TV;
  [[ "$#" = "1" ]] && cd "`find . -maxdepth 1 -name "*$1*"`"
  ls | sed "s|\.| |g" | sed "s| ...$||g"
}

alias torrent='cd /mnt/bigboi/mp_backup/torrent; ls -lh;'
alias links='cd /mnt/bigboi/mp_backup/links; ls -lh;'
alias mp='cd /mnt/movingparts/'
alias bb='cd /mnt/bigboi/'
alias mnt='cd /mnt'

alias alias_media='bash $HOME/scripts/alias_media.sh'

alias cast="sudo pkill -f 'python3 server.py'; cd /home/pi/NativCast/; nohup python3 server.py &"


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
gacp() { 
  git add .; git commit -m "$1"; git push 
}

alias grep='grep --color=auto'
alias egrep='egrep --color=auto'
alias fgrep="find . \( -type d -o -type f \) -iname"
alias psgrep='ps -aef | grep'
alias agrep="alias | grep" # search aliases
alias hist="history | sed 's/ [0-9]*  //g'"
hgrep() {
  hist | grep "$1" | grep -v 'hgrep' | uniq -u
}
rgrep() {
  grep -rni "$1" . # recursively search pwd
}

alias vrc="cat $HOME/vlc-recent.txt | grep -i"

hcp() {
  val=$(hist | grep $1 | tail -1)
  echo $val | pbcopy
  echo "copied: $val"
}



killport() {
  lsof -ti:"$1" | xargs kill
}

rec_find_rpl_in_files() { # rec_find_rpl_in_files find_pattern repl_pattern
  find . -type f | xargs sed -i "s|$1|$2|g"
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

play() {
  filenames=`ls | awk "/$1/{y=1}y"`
  echo "Opening: $filenames"
  sudo pkill -f "vlc"
  bash ~/sns.sh rear_movie
  xset s reset # wake display
  nohup vlc -f $filenames &
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

export DISPLAY=:0
export HISTSIZE=10000
export HISTFILESIZE=100000
export PATH="$PATH:/home/pi/.local/bin"
# if [ -f ~/.mount_aliases ]; then . ~/.mount_aliases; fi
if [ -f ~/.bash_defaults ]; then . ~/.bash_defaults; fi