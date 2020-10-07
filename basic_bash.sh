alias l='ls -lah'  ##custom list directory
alias ..="cd .."
alias ...="cd ../.."
alias ....="cd ../../.."
alias .....="cd ../../../.."

alias egrep='egrep --color=auto'
alias fgrep='fgrep --color=auto'
alias grep='grep --color=auto'

# add new alias
alias nalias="echo '\nalias$1' >> ~/.bashrc"

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