#! /bin/bash
shopt -s expand_aliases

alias l='ls -lah'  ##custom list directory
alias ..="cd .."
alias ...="cd ../.."
alias ....="cd ../../.."
alias .....="cd ../../../.."

alias vi='/usr/bin/vim.tiny'
alias rscr="sudo systemctl restart cron.service"
alias ct="sudo crontab -e"
alias cronlog="cd /var/log/cron"
alias rsynclog='cat /var/log/cron/rsync.log'
alias killcron="sudo pkill -f cron"

alias bashp='vi ~/.bashrc'
alias rbash='source ~/.bashrc'

alias dirsize='du -hsc *'
alias disku='df -u'

alias mdisk="touch $HOME/mconf/mdisk; nohup $HOME/scripts/internet_switches.sh &"
alias mdiskx="rm $HOME/mconf/mdisk; nohup $HOME/scripts/internet_switches.sh &"
alias mtor="touch $HOME/mconf/mtorrent; nohup $HOME/scripts/internet_switches.sh &"
alias mtorx="rm $HOME/mconf/mtorrent; nohup $HOME/scripts/internet_switches.sh &"
alias nodisk="touch $HOME/mconf/nodisk; nohup $HOME/scripts/internet_switches.sh &"
alias nodiskx="rm $HOME/mconf/nodisk; nohup $HOME/scripts/internet_switches.sh &"

alias sns='bash ~/sns.sh'

# MEDIA
alias movies='cd /mnt/movingparts/torrent/Movies; ls -lh;'
alias tv='cd /mnt/movingparts/torrent/TV; ls -lh;'
alias docu='cd /mnt/movingparts/torrent/Documentaries; ls -lh;'
alias torrent='cd /mnt/movingparts/torrent; ls -lh;'
alias mp='cd /mnt/movingparts/'
alias sg='cd /mnt/seegayte/'

alias alias_media='bash $HOME/scripts/alias_media.sh'

alias cast="sudo pkill -f 'python3 server.py'; cd /home/pi/NativCast/; nohup python3 server.py &"

alias rpiplay="/home/pi/RPiPlay/build/rpiplay"
alias rhp='nohup /home/pi/RPiPlay/build/rpiplay -r 180 &'


alias pd='sudo /sbin/shutdown -r now'
alias rb='sudo reboot'

alias py="python3"
alias pip3="python3 -m pip"

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


alias egrep='egrep --color=auto'
alias fgrep='fgrep --color=auto'
alias grep='grep --color=auto'
alias hist="history | sed 's/^ [[:digit:]]* //g'"
alias hgrep="hist | grep" # search shell history
alias pgrep="ps ax | grep" # search processes
alias agrep="alias | grep" # search aliases
rgrep() {
  grep -rni "$1" . # recursively search pwd
}

hcp() {
  val=$(hist | grep $1 | tail -1)
  echo $val | pbcopy
  echo "copied: $val"
}

killport() {
  lsof -ti:"$1" | xargs kill
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
  filename=`ls | grep $1`
  echo "Opening: $filename"
  sh ~/sns.sh rear_movie
  xset s reset # wake display
  nohup vlc -f $filename &
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
  echo 'reloading shell'
  exec bash
}

export DISPLAY=:0
export HISTSIZE=10000
export HISTFILESIZE=100000
export PATH="$PATH:/home/pi/.local/bin"

# ~/.bashrc: executed by bash(1) for non-login shells.
# see /usr/share/doc/bash/examples/startup-files (in the package bash-doc)
# for examples

# If not running interactively, don't do anything
case $- in
    *i*) ;;
      *) return;;
esac



# don't put duplicate lines or lines starting with space in the history.
# See bash(1) for more options
HISTCONTROL=ignoreboth

# append to the history file, don't overwrite it
shopt -s histappend

# check the window size after each command and, if necessary,
# update the values of LINES and COLUMNS.
shopt -s checkwinsize

# If set, the pattern "**" used in a pathname expansion context will
# match all files and zero or more directories and subdirectories.
#shopt -s globstar

# make less more friendly for non-text input files, see lesspipe(1)
#[ -x /usr/bin/lesspipe ] && eval "$(SHELL=/bin/sh lesspipe)"

# set variable identifying the chroot you work in (used in the prompt below)
if [ -z "${debian_chroot:-}" ] && [ -r /etc/debian_chroot ]; then
    debian_chroot=$(cat /etc/debian_chroot)
fi

# set a fancy prompt (non-color, unless we know we "want" color)
case "$TERM" in
    xterm-color|*-256color) color_prompt=yes;;
esac

# uncomment for a colored prompt, if the terminal has the capability; turned
# off by default to not distract the user: the focus in a terminal window
# should be on the output of commands, not on the prompt
force_color_prompt=yes

if [ -n "$force_color_prompt" ]; then
    if [ -x /usr/bin/tput ] && tput setaf 1 >&/dev/null; then
	# We have color support; assume it's compliant with Ecma-48
	# (ISO/IEC-6429). (Lack of such support is extremely rare, and such
	# a case would tend to support setf rather than setaf.)
	color_prompt=yes
    else
	color_prompt=
    fi
fi

if [ "$color_prompt" = yes ]; then
    PS1='${debian_chroot:+($debian_chroot)}\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w \$\[\033[00m\] '
else
    PS1='${debian_chroot:+($debian_chroot)}\u@\h:\w\$ '
fi
unset color_prompt force_color_prompt

# If this is an xterm set the title to user@host:dir
case "$TERM" in
xterm*|rxvt*)
    PS1="\[\e]0;${debian_chroot:+($debian_chroot)}\u@\h: \w\a\]$PS1"
    ;;
*)
    ;;
esac

# enable color support of ls and also add handy aliases
# if [ -x /usr/bin/dircolors ]; then
#     test -r ~/.dircolors && eval "$(dircolors -b ~/.dircolors)" || eval "$(dircolors -b)"
#     alias ls='ls --color=auto'
#     #alias dir='dir --color=auto'
#     #alias vdir='vdir --color=auto'
# 
#     alias grep='grep --color=auto'
#     alias fgrep='fgrep --color=auto'
#     alias egrep='egrep --color=auto'
# fi

# colored GCC warnings and errors
#export GCC_COLORS='error=01;31:warning=01;35:note=01;36:caret=01;32:locus=01:quote=01'

# some more ls aliases
#alias ll='ls -l'
#alias la='ls -A'
#alias l='ls -CF'

# Alias definitions.
# You may want to put all your additions into a separate file like
# ~/.bash_aliases, instead of adding them here directly.
# See /usr/share/doc/bash-doc/examples in the bash-doc package.

if [ -f ~/.bash_aliases ]; then
    . ~/.bash_aliases
fi

# enable programmable completion features (you don't need to enable
# this, if it's already enabled in /etc/bash.bashrc and /etc/profile
# sources /etc/bash.bashrc).
if ! shopt -oq posix; then
  if [ -f /usr/share/bash-completion/bash_completion ]; then
    . /usr/share/bash-completion/bash_completion
  elif [ -f /etc/bash_completion ]; then
    . /etc/bash_completion
  fi
fi
