#! /bin/bash
shopt -s expand_aliases

alias l='ls -lah'  ##custom list directory
alias ..="cd .."
alias ...="cd ../.."
alias ....="cd ../../.."
alias .....="cd ../../../.."

alias bashp='vi ~/.bashrc'
alias rbash='exec bash'

alias dirsize='du -hsc *'
alias disku='df -u'

alias egrep='egrep --color=auto'
alias fgrep='fgrep --color=auto'
alias grep='grep --color=auto'
alias hgrep='history | grep'

### GIT ###
alias gpo="git push origin"
alias gckm="git checkout master"
alias gac="git add .; git commit -m"
alias gp="git pull"
alias grao="git remote add origin"
alias gc='git clone'
alias gck='git checkout'
gacp() {
  git add . && git commit -m "$1" && git push
}


# alias alias="alias; cat ~/.bashrc | grep -E '^[[:space:]]*([[:alnum:]_]+[[:space:]]*\(\)|function[[:space:]]+[[:alnum:]_]+)'"

rgrep() {
  grep -rni "$1" . # recursively search cwd
}

# add new alias
nalias()
{
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

mkcdir ()
{
    mkdir -p -- "$1" &&
      cd -P -- "$1"
}

rec_find_rpl_in_files() {
  find . -type f | xargs sed -i "s|$1|$2|g"
}

rec_rename() {
  find . -exec rename "s/$1/$2/g" {} +
}

truncate_log(){
  filename=$1
  num_lines=$2
  tail -n$num_lines $filename > $filename.tmp && mv $filename.tmp $filename
}

rec_files() {  # rec_files *.sh
  for f in "$@"
  do 
    echo $f
  done 
}

file_lines() { # file_lines 'truncate_file.sh' echo  
  fpath=$1
  command=$2
  while read f
  do 
    $command $f
  done < $fpath
}


export HISTSIZE=8000
export HISTFILESIZE=80000
