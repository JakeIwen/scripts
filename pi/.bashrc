alias l='ls -lah'  ##custom list directory
alias ..="cd .."
alias ...="cd ../.."
alias ....="cd ../../.."
alias .....="cd ../../../.."

alias crlog="cd /var/log/cron"

alias vi=/usr/bin/vim.tiny
alias rscr="sudo systemctl restart cron.service"
alias ct="sudo crontab -e"

alias egrep='egrep --color=auto'
alias fgrep='fgrep --color=auto'
alias grep='grep --color=auto'
alias pgrep='ps ax | grep'

alias mdisk='touch ~/mdisk'
alias mdiskx='rm ~/mdisk'
alias mtor='touch ~/mtorrent'
alias mtorx='rm ~/mtorrent'

# add new alias
nalias()
{
  echo "alias $1='$2'" >> ~/.bashrc
}

alias bashp='vi ~/.bashrc'
alias rbash='source ~/.bashrc'

alias dirsize='du -hsc *'

alias disku='df -u'

rgrep()
{
  grep -rni "$1" . # recursively search cwd
}

alias hgrep='history | grep'
export HISTSIZE=8000
export HISTFILESIZE=80000

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
if [ -x /usr/bin/dircolors ]; then
    test -r ~/.dircolors && eval "$(dircolors -b ~/.dircolors)" || eval "$(dircolors -b)"
    alias ls='ls --color=auto'
    #alias dir='dir --color=auto'
    #alias vdir='vdir --color=auto'

    alias grep='grep --color=auto'
    alias fgrep='fgrep --color=auto'
    alias egrep='egrep --color=auto'
fi

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

alias movies='cd /mnt/movingparts/torrent/Movies; ls -lh;'
alias tv='cd /mnt/movingparts/torrent/TV; ls -lh;'
alias docu='cd /mnt/movingparts/torrent/Documentaries; ls -lh;'
alias torrent='cd /mnt/movingparts/torrent; ls -lh;'

alias synclog='cat /var/log/cron/rsync.log'
alias omx='omxplayer --timeout 60 udp://192.168.6.231:1234'
alias rpiplay='~/RPiPlay/build/rpiplay'
alias pd='sudo /sbin  /shutdown -r now'
alias rb='sudo reboot'

play() {
  filename=`ls | grep $1`
  echo "Opening: $filename"
  python3 ~/scripts/python/sonos_audio_source.py vonRear line
  xset s reset # wake display
  nohup vlc -f $filename &
}  

alias py="python3"
alias pip3="python3 -m pip"
  
add_python3_path() {
  name=$1
  pypath=$2
  SITEDIR=$(python3 -m site --user-site)
  mkdir -p "$SITEDIR" # create if it doesn't exist
  echo "$pypath" > "$SITEDIR/$name.pth"
  echo "added $name containing $pypath to $SITEDIR"
  l $SITEDIR
}

export DISPLAY=:0