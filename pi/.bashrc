#! /bin/bash
# codeshape: allow-size the Pi's single interactive shell profile, deployed as one file to ~/.bashrc; splitting it is a separate refactor
# COMMANDS are definitions and nonsecret defaults, safe for a noninteractive
# Codex BASH_ENV. Functions resolve other functions and variables at run time.
# Below the boundary: normal shell setup, exports, secrets and command shadows.
# SSH commands still load the full profile; only noninteractive Codex stops.
### COMMANDS: SHELL TRICKS ###
# Small helper set from the scripts repo (shared/sh, pi/scripts); `tricks`
# explains each one. Loaded before any alias here so none expands inside them.
if [ -f "$HOME/scripts/shell_tricks.bash" ]; then . "$HOME/scripts/shell_tricks.bash"; fi

# Media positions, and the APIs of the local media services: youtube-cast
# (Van TV), audiobooks and video-library.
POSPATH="$HOME/vlc-positions.txt"
VLCQTPATH="$HOME/.config/vlc/vlc-qt-interface.conf"
VANTVAPI="http://localhost:8795/api"
BOOKAPI="http://localhost:8787/api"
VIDEOAPI="http://localhost:8789/api"

S_REPOS=("$HOME/dev/obd-things") # repos whose tracked tools/ and projects/ .py scripts s finds by name
### CORE COMMANDS ###
l() { command ls -lah "$@"; }
lla() { command ls -ltu "$@"; }
..() { builtin cd .. "$@"; }
...() { builtin cd ../.. "$@"; }
....() { builtin cd ../../.. "$@"; }
.....() { builtin cd ../../../.. "$@"; }
ch7() { command sudo chmod -R 777 "$@"; } # usage: $ ch7 .
chme() { command sudo chown -R $(whoami) "$@"; } # usage: $ chme .
bashp() { command /usr/bin/vim.tiny ~/.bashrc "$@"; }

### GREP & HISTORY ###
grepi() { command grep -i --color=auto "$@"; }
g() { command grep --color=auto "$@"; }
psgrep() { command ps -aef | command grep --color=auto "$@"; }
agrep() { { alias; declare -f; } | command grep --color=auto "$@"; } # search aliases and function definitions
hist() { history | command perl -pe 's/^\s+[0-9]+\**\s+//g' "$@"; }
hgrep() { hist | command grep --color=auto "$@" | command grep --color=auto -v 'hgrep' | uniq -u; } # howto: pass all args to a subfunction
hgrepn() { history | command grep --color=auto "$@" | command grep --color=auto -v 'hgrep'; } # howto: pass all args to a subxnction

### PYTHON ###
# use py venv to run basically anything
pyv() { source /home/pi/pyvenv/bin/activate; python "$@"; deactivate; }
pipv() { source /home/pi/pyvenv/bin/activate; pip "$@"; deactivate; }
pipa() { source /home/pi/pyvenv/bin/activate; }
py() { command python3 "$@"; }

### SHELL UTILITIES ###
functions() { command cat ~/.bashrc | command grep --color=auto -E '^[[:space:]]*([[:alnum:]_]+[[:space:]]*\(\)|function[[:space:]]+[[:alnum:]_]+)' "$@"; }
fndef() { declare -f -- "$@"; } # print function definition
als() { type -- "$@"; }
tf() { tail ${2:-'-50'} $1; tail -f $1; }       # tail -f with more recent lines 
snh() { nohup bash -c $1 & tail -f ./nohup.out; }
sx() { sudo "$(history | perl -pe 's/^\s+[0-9]+\**\s+//g' | tail -1)"; }
incl() { val="$1"; shift; printf '%s\0' "${@}" | command grep --color=auto -F -x -z "$val"; }
escape_chars() { echo $1 | perl -ne 'chomp;print "\Q$_\E\n"'; }
uridecode() { : "${*//+/ }"; echo -e "${_//%/\\x}"; } 
trim_last_line() { sed -i '$ d' $1; } # filepath
join_by() { local IFS="$1"; shift; echo "$*"; }
rm_last_line() { sed -i '$ d' "$1"; }
rmlast() { tail -n 1 "$1" | wc -c | xargs -I {} truncate "$1" -s -{}; }
rm_last_n_lines() {
  file="$2"
  awk -v n=$1 'NR==FNR{total=NR;next} FNR==total-n+1{exit} 1' "$file" "$file"
  cat "$file"
}

rm_match_lines() {
  term="$1"
  file="$2"
  cp "$file" "$file.bak"
  cat "$file.bak" | command grep --color=auto -via "$term" > "$file"
  cat "$file"
}
rm_lines() {
  pattern="$1"
  file="$2"
  command grep --color=auto -Pav "$pattern" "$2" > tmpfile && mv tmpfile "$2" && rm tmpfile
}
# rec_find_rpl_in_files find_pattern repl_string
rec_find_rpl_in_files() { find . -exec sed -i '' "s|$1|$2|g" {} \;; }
file_lines() { # file_lines './filename.txt' echo   
  fpath=$1
  command=$2
  while read f
  do $command $f
  done < $fpath
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
get_global() { cat /home/pi/.$1; }
increment_global() {
  name=$1; min=$2; max=$3;
  val=`cat /home/pi/.$name`
  val=$(( $val + 1 ))
  if [ -z $val ] || [ $val -gt $max ]; then val=$min; fi;
  echo $val > /home/pi/.$name
  echo $val
}
rm_sm_dirs() { # remove small dirs by size `rm_sm_dirs 1.1G .`
  local threshold="$1"
  local target_dir="${2:-.}"

  if [[ -z "$threshold" ]]; then
    echo "Usage: remove_small_dirs <size> [directory]"
    echo "Example: remove_small_dirs 10M /path/to/dir"
    return 1
  fi

  for d in "$target_dir"/*/; do
    # Skip if no directories exist
    [[ -d "$d" ]] || continue

    dir_size_bytes=$(du -sb "$d" | awk '{print $1}')
    threshold_bytes=$(numfmt --from=iec "$threshold")

    if (( dir_size_bytes < threshold_bytes )); then
      echo "Removing: $d (size: $(numfmt --to=iec $dir_size_bytes))"
      rm -rf "$d"
    fi
  done
}
nalias() { echo '`nalias` replaced with `nfn`'; }
# nfn.py commits the function edit to pi/.bashrc on GitHub, then installs it here.
nfn() {
  local before
  before=$(/usr/bin/stat -c %i "$HOME/.bashrc") || return
  /usr/bin/python3 "$HOME/scripts/nfn.py" "$@" || return
  # reload only when nfn.py replaced the file (not on --dry-run or no-op)
  [[ $(/usr/bin/stat -c %i "$HOME/.bashrc") != "$before" ]] || return 0
  echo 'reloading shell'
  exec bash
}

### SCRIPTS ###
s() { # run a named ~/scripts .sh/.py (or a directory's main.sh/main.py), else a script from S_REPOS
  local name candidate f= root hits pythonpath
  if [ "$#" -lt 1 ]; then
    echo "usage: s <script-name|dir/script-name> [args...]" >&2
    return 2
  fi
  name=$1
  shift
  for candidate in \
    "$HOME/scripts/$name.sh" \
    "$HOME/scripts/$name.py" \
    "$HOME/scripts/$name/main.sh" \
    "$HOME/scripts/$name/main.py"; do
    if [ -f "$candidate" ]; then
      f=$candidate
      break
    fi
  done
  [ -n "$f" ] || f=$(find "$HOME/scripts" -mindepth 2 -type f \
    \( -name "$name.sh" -o -name "$name.py" \
       -o -path "*/$name/main.sh" -o -path "*/$name/main.py" \) \
    -not -path '*/site-packages/*' -not -path '*/node_modules/*' \
    -print -quit)
  if [ -n "$f" ]; then
    case "$f" in *.py) python3 "$f" "$@";; *) "$f" "$@";; esac
    return
  fi
  # Repo scripts run by path with every S_REPOS root importable, so they work from any
  # directory with or without their own sys.path bootstrap, wherever they move in the repo.
  pythonpath=$(IFS=:; echo "${S_REPOS[*]}")${PYTHONPATH:+:$PYTHONPATH}
  for root in "${S_REPOS[@]}"; do
    hits=$(_s_repo_scripts "$root" | awk -v n="${name%.py}.py" \
      '$0 == n || substr($0, length($0) - length(n)) == "/" n')
    [ -n "$hits" ] || continue
    if [ "$(printf '%s\n' "$hits" | wc -l)" -gt 1 ]; then
      echo "s: $name matches several scripts in $root; add a directory (s dir/$name):" >&2
      printf '  %s\n' $hits >&2
      return 2
    fi
    PYTHONPATH=$pythonpath python3 "$root/$hits" "$@"
    return
  done
  echo "s: no $name script found in ~/scripts or ${S_REPOS[*]}" >&2
  return 1
}
_s_repo_scripts() { # tracked tools/ and projects/ .py scripts of repo $1, repo-relative
  git -C "$1" ls-files -- 'tools/*.py' 'projects/*.py' 2>/dev/null |
    command grep --color=auto -vE '(^|/)(tests|dashboard)/|(^|/)__init__\.py$'
}
_s_complete() { # complete the script name; later arguments fall back to filenames (-o default)
  local root
  [ "$COMP_CWORD" -eq 1 ] || return
  COMPREPLY=($(compgen -W "$(
    find "$HOME/scripts" -type f \( -name '*.sh' -o -name '*.py' \) \
      -not -path '*/site-packages/*' -not -path '*/node_modules/*' -not -path '*/.git/*' 2>/dev/null |
      awk -F/ '$NF ~ /^main\.(sh|py)$/ { print $(NF-1); next } { sub(/\.(sh|py)$/, "", $NF); print $NF }'
    for root in "${S_REPOS[@]}"; do _s_repo_scripts "$root" | sed -E 's#.*/##; s/\.py$//'; done
  )" -- "${COMP_WORDS[1]}"))
}
add_pricecheck() { # add_pricecheck <parser> <threshold> <URL> [title]
  if [ "$#" -lt 3 ] || [ "$#" -gt 4 ]; then
    echo "usage: add_pricecheck <parser> <threshold> <URL> [title]" >&2
    return 2
  fi
  python3 "$HOME/scripts/price_check/main.py" \
    --db "$HOME/.local/share/price_check/price_check.sqlite3" \
    add "$@"
}
rm_pricecheck() { # rm_pricecheck <title-or-URL>
  if [ "$#" -ne 1 ]; then
    echo "usage: rm_pricecheck <title-or-URL>" >&2
    return 2
  fi
  python3 "$HOME/scripts/price_check/main.py" \
    --db "$HOME/.local/share/price_check/price_check.sqlite3" \
    remove "$1"
}

### GIT ###
gpo() { command git push origin "$@"; }
gckm() { command git checkout master "$@"; }
gac() { command git add .; command git commit -m "$@"; }
gp() { command git pull "$@"; }
grao() { command git remote add origin "$@"; }
gc() { command git clone "$@"; }
gck() { command git checkout "$@"; }
git_branch() {
  git symbolic-ref --quiet --short HEAD 2>/dev/null ||
    git rev-parse --quiet --short HEAD 2>/dev/null
}
gacp() { git add .; git commit -m "$1"; git push; }
# Print the GitHub page for opening a pull request from the current branch.
# Pass an optional base branch; otherwise use master.
cpull() {
  local base_branch="${1:-master}" branch url

  url=$(git config --get remote.origin.url) || {
    echo "cpull: this repository has no origin remote" >&2
    return 1
  }
  branch=$(git_branch) || {
    echo "cpull: not in a Git repository" >&2
    return 1
  }

  # Normalize common GitHub clone URLs into browser URLs.
  case "$url" in
    git@github.com:*) url="https://github.com/${url#git@github.com:}" ;;
    ssh://git@github.com/*) url="https://github.com/${url#ssh://git@github.com/}" ;;
  esac
  url="${url%.git}"
  printf '%s/compare/%s...%s?expand=1\n' "$url" "$base_branch" "$branch"
}

### SYSTEM ### (Pi hardware, display, power)
rb() { /home/pi/scripts/safe_reboot.sh "$@"; }
pd() { /home/pi/scripts/safe_power_down.sh "$@"; }
bootedit() { command sudo /usr/bin/vim.tiny /boot/config.txt "$@"; }
bootconf() { command sudo cat /boot/config.txt "$@"; }
dirsize() { command sudo du -hsc .[^.]* * "$@"; }
disku() { command df -h "$@"; }
gtop() { command sudo /opt/vc/bin/vcdbg reloc stats "$@"; }
corefreq() { command cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq; command vcgencmd measure_volts "$@"; }
gpu_mem() { command vcgencmd get_mem gpu "$@"; }
gpu_mem_fix() { command vcgencmd cache_flush && command sudo vcdbg reloc "$@"; }
gpu_stat() { command sudo vcdbg reloc "$@"; }
_wl_out() { WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0} XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)} command wlopm "$1" '*' 2>/dev/null; }
wake_display() { _wl_out --on || command xset dpms force on "$@"; }  # labwc: wlopm; X11 fallback
slp() { _wl_out --off || command xset s activate "$@"; }
keepon() { while psgrep vlc && sleep 300; do wake_display; done; }
sysvol() { sudo amixer cset numid=1 ${1:-"90"}%; }
svc() { sudo systemctl $2 $1.service; } # svc home-assistant start
set_date() { sudo date -s "$(wget -qSO- --max-redirect=0 google.com 2>&1 | command grep --color=auto Date: | cut -d' ' -f5-8)Z"; }
speed() { ~/scripts/speedtest.sh "$@"; }

### CRON & BACKUPS ###
rscr() { command sudo systemctl restart cron.service "$@"; }
ct() { command sudo crontab -e "$@"; }
killcron() { command sudo pkill -f cron "$@"; }
cronlog() { builtin cd /var/log/cron "$@"; }
bkuplog() { command cat /var/log/cron/pi_backup.log "$@"; }
bkuplast() { bkuplog | command grep --color=auto 'backup complete' "$@"; }
logdir() { builtin cd /var/log/cron "$@"; }
iswl() { tf /var/log/cron/internet_switches.log "$@"; }
cronp() { /home/pi/scripts/parse_cron.sh "$@"; }
bkupnow() { sudo /home/pi/scripts/backup/backup_window.sh --force; } # run every daily backup now
sparesync() { # refresh a hot spare from the live system: sparesync a | sparesync b
  local l=${1:?usage: sparesync a|b}
  [[ "$l" == hotspare-* ]] || l="hotspare-$l"
  sudo /home/pi/scripts/backup/clone_to_sd.sh "$l"
}
stopcron() {
  sudo systemctl stop cron
  sudo systemctl disable cron
  sudo systemctl mask cron
}

startcron() {
  sudo systemctl unmask cron
  sudo systemctl enable cron
  sudo systemctl start cron
}

### PROCESSES & PORTS ###
active_ssh_sessions() { command sudo netstat -tnpa | command grep --color=auto 'ESTABLISHED.*sshd' "$@"; }
num_ssh() { active_ssh_sessions | command wc -l "$@"; }
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
killport() {
  ARGS=("$@")
  detail_list=`lsof ${ARGS[@]/#/-i:}`
  echo -ne "killing processes:\n$detail_list"
  [ "$detail_list" ] && lsof ${ARGS[@]/#/-ti:} | xargs kill
}
inuse() {
  port=`if [[ "$#" = "1" ]]; then echo "$1"; else echo '[0-9][0-9][0-9][0-9][0-9]? '; fi`
  sudo lsof -P -n | command grep --color=auto -E ":$port"
  echo "port $port"
}

### NETWORK & SSH ###
ngear() { command ssh root@OpenWrt "$@"; }
ubnt() { command ssh -i ~/.ssh/id_rsa ubnt@192.168.8.20 "$@"; }
rball() { ubnt reboot & ngear reboot; rb "$@"; }
ifaces() { command ssh root@OpenWrt mwan3 interfaces | command grep --color=auto 'is online' "$@"; }
ifonline() { ssh root@OpenWrt mwan3 interfaces | command grep --color=auto "$1 is online"; }
init_rsa() { command ssh-copy-id -i ~/.ssh/id_rsa.pub "$@"; } # init_rsa user@device
ssh_copy_id_dropbear() {
  if [ "$#" -ne 1 ]; then echo "Example: ${0} root@192.168.1.1" && exit 1; fi
  cat /home/pi/.ssh/id_rsa.pub | ssh ${1} "cat >> /etc/dropbear/authorized_keys && chmod 0600 /etc/dropbear/authorized_keys && chmod 0700 /etc/dropbear"
}
# How this ssh session reached the Pi: LAN (192.168.6.x) or Tailscale (100.x,
# which may ride the metered uplink when the client is off the van LAN).
sshroute() {
  local srv; srv=$(cut -d' ' -f3 <<<"${SSH_CONNECTION:-}")
  case $srv in
    192.168.6.*) echo "session via LAN ($srv)" ;;
    100.*) echo "session via Tailscale ($srv)" ;;
    "") echo "not an ssh session" ;;
    *) echo "session via $srv" ;;
  esac
}
tspath() { # Tailscale's path to a peer (default m4mac): direct 192.168.6.x = LAN; a public IP or relay = metered uplink
  tailscale status | command grep --color=auto -w "${1:-m4mac}" | sed 's/, tx.*//'
}
ipinfo() { py /home/pi/scripts/python-packages/current/pi/scripts/python/ip_info.py "$@"; }

### VAN STATE ### (storage/torrent policy, ignition)
# Requested storage/torrent policy. policyctl writes atomically and requests
# reconciliation; the dashboard uses the same command.
policyctl() { $HOME/scripts/policyctl "$@"; }
mconf() { $HOME/scripts/policyctl status "$@"; }
nodisk() { $HOME/scripts/policyctl disks off --wait "$@"; }
nodiskx() { $HOME/scripts/policyctl disks on "$@"; }
mdisk() { nodiskx "$@"; }
notor() { $HOME/scripts/policyctl torrents off "$@"; }
notorx() { $HOME/scripts/policyctl torrents on "$@"; }
startor() { $HOME/scripts/policyctl starlink-torrents on "$@"; }
startorx() { $HOME/scripts/policyctl starlink-torrents off "$@"; }

# status hooks
ignitmonoff() { "$HOME/scripts/ignitionmonctl" disable "$@"; }
ignitmonon() { "$HOME/scripts/ignitionmonctl" enable "$@"; }
ignitmonconf() { "$HOME/scripts/ignitionmonctl" status "$@"; }
# Also accept the service's full spelling while preserving the old short names.
ignitionmonoff() { ignitmonoff "$@"; }
ignitionmonon() { ignitmonon "$@"; }
ignitionmonconf() { ignitmonconf "$@"; }
van_is_running() { if test -f /home/pi/hooks/ignition_is_on; then echo "yes"; else echo "no"; fi; }

### DISKS & MOUNTS ###
mounts() { command grep --color=auto "dev/sd" /proc/mounts "$@"; }
blklbl() { command ls -1 /dev/disk/by-label "$@"; }
blk() { command sudo blkid | command grep --color=auto 'dev/sd' "$@"; }
drives() { command sudo lsblk -o NAME,FSTYPE,SIZE,MOUNTPOINT,LABEL "$@"; }
md() { . /home/pi/scripts/mount_disks.sh "$@"; }
remount() {
  . /home/pi/scripts/umount_disks.sh "$@"
  . /home/pi/scripts/mount_disks.sh "$@"
  sudo service smbd start
}
mountbb() { s mount_disks bigboi; }
mountmp() { s mount_disks movingparts; }
mountex() { s mount_disks EXFAT512; }
umountbb() { s umount_disks bigboi; }
umountmp() { s umount_disks movingparts; }
umountex() { s umount_disks EXFAT512; }
print_vfat_uuid() {
  BLKID=$1 # /dev/sdc1
  sudo dd bs=1 skip=67 count=4 if=$BLKID 2>/dev/null \
    | xxd -plain -u \
    | sed -r 's/(..)(..)(..)(..)/\4\3-\2\1/'
}
set_vfat_uuid() {
  UUID=$1 # 1234-ABCF  hex only
  BLKID=$2 # /dev/sdc1
  valid=`echo "$UUID" | command grep --color=auto -P "^\d{4}\-[A-F]{4}$"`
  vfat=`blkid $BLKID | command grep --color=auto 'TYPE="vfat"'`
  
  echo "Current UUID: $(print_vfat_uuid $BLKID)"
    
  if [ -n "$valid" ] && [ -n "$vfat" ]; then
    printf "\x${UUID:7:2}\x${UUID:5:2}\x${UUID:2:2}\x${UUID:0:2}" \
      | sudo dd bs=1 seek=67 count=4 conv=notrunc of=$BLKID
    
    echo "Updated UUID: $(print_vfat_uuid $BLKID)"
  else
    [ -z "$valid" ] && echo "New UUID '$UUID' does not match '1234-ABCD' form"
    [ -z "$vfat" ] && echo "'`blkid $BLKID | command grep --color=auto -o 'TYPE="[^"]*"'`' is not a vfat partition"
  fi
}
fsprop() { 
  prop=$1 #  LABEL UUID TYPE PARTUUID PARTLABEL
  sterm=$2
  match="$(blkln "$sterm" | command grep --color=auto -Po "(?<= $prop=\")[^\"]*")"
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
  match="$(/sbin/blkid | command grep --color=auto "$sterm")"
  echo "$match"
}
sync_comps() {
  sync_dirpath '/Users/jacobr/Library/Application Support/BetterTouchTool/'
  sync_dirpath '/Users/jacobr/router configs'
}

sync_new_torrents(){
  locpath=/Users/jacobr/Desktop/vidtmp
  pi_user='pi@vanpi.lan'
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

### AUDIO & SONOS ###
sns() { command bash ~/sns.sh "$@"; }
sns_list() {
  fpath="$HOME/scripts/python-packages/current/shared/python/sonos_tasks.py"
  while read f; do       
    helpers=`echo $f | command grep --color=auto '# helpers'`
    if [[ $helpers ]]; then break; fi
    echo $f | command grep --color=auto '^def' | sed "s|^def\s||g"
  done < $fpath
}
airupnp() {
  [[ "$1" == "disable" ]] && cmd=stop || cmd=start
  sudo systemctl $cmd airupnp.service
}
rpiplay() { wake_display; command nohup /home/pi/RPiPlay/build/rpiplay -r 180 "$@" & }
rhp() {
  if [[ `ps ax` == *"rpiplay"* ]]
  then sudo pkill -f rpiplay
  else rpiplay
  fi
}

### MEDIA LIBRARY ###
movies() { builtin cd /mnt/movingparts/links/Movies && command ls | command sed "s|\.| |g" | command sed "s| ...$||g" "$@"; }
docu() { builtin cd /mnt/movingparts/links/Documentaries && command ls | command sed "s|\.| |g" | command sed "s| ...$||g" "$@"; }
torrent() { builtin cd /mnt/movingparts/torrent; ls "$@"; }
new() { builtin cd /mnt/movingparts/links/New; ls "$@"; }
links() { builtin cd /mnt/movingparts/links; ls "$@"; }
mp() { builtin cd /mnt/movingparts/ "$@"; }
bb() { builtin cd /mnt/bigboi/ "$@"; }
mnt() { builtin cd /mnt "$@"; }
tor() { builtin cd /mnt/movingparts/torrent/ "$@"; }
inc() { builtin cd /mnt/movingparts/torrent/incomplete; command ls -lah "$@"; }
backup_home() { builtin cd /mnt/bigboi/pi_backup_git/pi_backup/home/pi "$@"; }
am() { ~/scripts/alias_media.sh "$@"; }
an() { ~/scripts/alias_media.sh new "$@"; }
avail_drive_path() {
  bb_links='/mnt/bigboi/mp_backup'
  mp_links='/mnt/movingparts'
  if [ -n "$drivepath" ] && [ "$drivepath" = "bb" ]; then echo $bb_links; fi
  if [ -e "$mp_links/torrent" ]; then echo $mp_links; else echo $bb_links; fi
}
media_all() {
  find "$(avail_drive_path)/links" -type l -not -iname nohup.out -print | sort -g
}

media_docs_movies() {
  base="$(avail_drive_path)/links"
  find "$base/Movies" "$base/Documentaries" | command grep --color=auto -Ev  '/Movies$|/Documentaries$'
}
media_by_name() { find "$(avail_drive_path)/links" -type l -ipath "*$1*" -print0 | sort -z; }
fgp() { find /mnt/movingparts/links \( -type l \) -iname "*$1*"; }
tv() { cd /mnt/movingparts/links/TV || cd /mnt/bigboi/links/TV; ls; }

### VLC PLAYBACK ###
vlcr() { command grep --color=auto -ia "$1" "$POSPATH" | tail -10; }
vlc_nosubs() { increment_global vlc_sub_track -1 2; resume ; }
vlc_subs_off() { echo '-1' > /home/pi/.vlc_sub_track; resume; }
vlcnice() { sudo renice ${1:-"12"} `pgrep vlc`; }
lastpos() { command grep --color=auto -Pai "$1" "$POSPATH"; }
log_position() { /home/pi/scripts/log_position.sh; }

get_last_position() {
  if [ -n "$1" ]; then
    # parse the big integer from the line after the filepath
    command grep --color=auto -Poai "(?<=$1 )\d{4,}" "$POSPATH"
  else
    echo 0
  fi
}
resume() {
  if [ $# -eq 1 ] && [ -z "$( echo "$1" | command grep --color=auto -Po '^-')" ]; then
    # if first arg is not a switch ie starts with '-'
    pth=`command grep --color=auto -ia "$1" $POSPATH | tail -1 | cut -d' ' -f1`
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
playp() {
  if [[ "$#" -ne 1 || -z "$1" ]]; then
    echo "usage: playp <local-media-file>" >&2
    return 2
  fi
  local path="$1"
  [[ "$path" = /* ]] || path="$(pwd -P)/$path"

  # Identity is best-effort inside the manager: an ordinary file still plays
  # when qBittorrent is offline or cannot recognize it.  If the manager itself
  # is unavailable, retain playp's original direct VLC behavior.
  local response_file http_code curl_status manager_timeout curl_metrics num_connects
  manager_timeout="${VAN_VIDEO_PLAYP_MAX_TIME:-90}"
  response_file="$(mktemp "${TMPDIR:-/tmp}/van-play-local.XXXXXX")" || {
    run_vlc "$path"
    return $?
  }
  curl_metrics="$(
    /usr/bin/curl -sS --connect-timeout 1 --max-time "$manager_timeout" \
      -o "$response_file" -w '%{http_code} %{num_connects}' \
      -H 'X-Van-Video: 1' -X POST \
      "${VIDEOAPI:-http://localhost:8789/api}/play-local" \
      --data-urlencode "path=$path" \
      --data-urlencode 'subtitles=auto'
  )"
  curl_status=$?
  read -r http_code num_connects <<<"$curl_metrics"
  if [[ "$curl_status" -eq 0 && "$http_code" = 2* ]]; then
    /bin/cat "$response_file"
    /bin/rm -f -- "$response_file"
    echo
    return 0
  fi
  # curl 7 proves that no connection was made, so direct VLC is safe.  Other
  # transport failures (especially a response timeout) are ambiguous: the
  # manager may already have launched VLC, and retrying would double-launch.
  if [[ "$curl_status" -eq 7 || ( "$curl_status" -eq 28 && "${num_connects:-0}" -eq 0 ) ]]; then
    /bin/rm -f -- "$response_file"
    run_vlc "$path"
    return $?
  fi
  if [[ -s "$response_file" ]]; then
    /bin/cat "$response_file" >&2
  else
    echo "video manager request ended without a definitive response (curl $curl_status); not launching VLC again" >&2
  fi
  /bin/rm -f -- "$response_file"
  echo >&2
  return 1
}
play() {
  echo "play $*"
  sleep 1
  pth=$1
  # gpu_mem_fix > /dev/null
  # decoded=`uridecode "$pth"`=[]
  dir=`echo "$pth" | command grep --color=auto -Po '.*(New|TV|Movies|Documentaries)'`
  filename=`basename "$pth"` # TODO if DIR do ELSE play mkv
  if [ -z $dir ]; then
    unset epnum  
    unset name  
  else
    epnum=`parse_episode_num $pth`
    name="$(echo "$filename" | command grep --color=auto -Poi ".*(?=[_\.]S\d\d.*$)" | sed -E 's|[\\\.]|_|g' )"
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
prep_for_media() {
  kill_media > /dev/null
  wake_display
}
kill_media() {
  vantv_active && vantv_ctl stop > /dev/null
  log_position
  pk chrom omxplayer vlc
  sleep 2
  pk chrom omxplayer vlc -9
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
    pth="$(echo $filepaths | command grep --color=auto -Po '^\S+')"
  else
    echo "no path or filepaths provided"
    return 0
  fi
  
  trupath="$( [ -L "$pth" ] && readlink "$pth" || echo "$pth" )"
  echo "TRU PATH $trupath"
  json="$(mkvmerge -J "$trupath")"
  subtrack=`node ~/scripts/parse_sub_track.js "$json"`
  echo "playing subtrack $subtrack"
  
  subs="--sub-track=$subtrack"
  audio="--audio-language=en"
  ctrl="--control=dbus"
  av="--avcodec-hw=v4l2-request"
  
  # so --qt-minimal-view was a workaround to enforce the 180 rotation since the display
  # preference was being unrespected by VLC. $av seems to remedy this
  # nohup vlc --qt-minimal-view $ctrl $subs $audio $av "$decoded" &
  if [[ "$#" = "1" ]]; then # shitty workaround here
    # handles spaces in filename ie /incomplete, playp()
    nohup vlc $ctrl $subs $audio $av "$decoded" &
  else
    # assuming multiple $filepaths are space-delimited (paths contain no individual spaces)
    nohup vlc $ctrl $subs $audio $av $decoded &
  fi

  
  bash "$HOME/scripts/rear_movie_check.sh" </dev/null >/dev/null 2>&1 &

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
  season=`echo $cleanln | command grep --color=auto -Po '(?<=(S|s))\d\d' | head -1`
  epp=`echo $cleanln | command grep --color=auto -Po '(?<=(E|e))\d\d' | tail -1`
  if [[ "$epp" && "$season" ]] ; then
    echo "${season}${epp}" | perl -pe 's|^0||g' # parsed numbers
  else
    echo $cleanln | perl -pe 's|\_?\d{4,}\_?||g' | command grep --color=auto '\d\d\d'
  fi
}
playf() {
  name=`basename $1 | perl -pe 's/ /_/g' | perl -pe 's/\..*$//g'`

  if [ "$2" = "-l" ]; then # list files
    echo "available files"
    media_all | command grep --color=auto -i "$name"
    return 0
  elif [ "$2" = "-s" ]; then # start
    ep=101
  elif [ "$2" = "-r" ]; then
    filepaths=`media_all | command grep --color=auto -i "$name" | shuf`
    run_vlc
    return 0
  elif [ -z "$2" ]; then # movie
    filepaths=`media_docs_movies | command grep --color=auto -i "$name" | sort`
    run_vlc
    return 0
  fi
  
  ep=$2 # episode number eg 304 (parsed from S03E04) or flag -a, -r
  echo "name: $name, ep: $ep"
  
  readarray -d '' match_arr < <( media_by_name "$name"  )
    
  if [ ${#match_arr[@]} -eq 0 ]; then echo "No matches found" && return 1; fi
  # match ep
  for idx in "${!match_arr[@]}"; do
    dirplusname="$(echo ${match_arr[$idx]} | command grep --color=auto -Po '[^\/]+\/[^\/]+$')"
    ep_from_path=$(parse_episode_num $dirplusname $ep)
    if [[ "${ep_from_path,,}" == *"${ep,,}"* ]]; then # case-insensitive match
      filepaths="${match_arr[*]:$idx}" # slice to the end of the array
      run_vlc
      return 0
    fi
  done
}
### VAN TV ### (youtube-cast.service, port 8795 — http://vanpi.lan:8795)
# While a cast is loaded, the transport commands the Mac's BTT media buttons send
# over ssh (vlcmd, pp, play_status, kill_media) drive Van TV instead of VLC.
vantv_status() { curl -s -m 1 "$VANTVAPI/status" 2>/dev/null; }
vantv_active() {
  [[ $(vantv_status | jq -r '.player // empty' 2>/dev/null) =~ ^(LOADING|PLAYING|PAUSED)$ ]]
}
vantv_ctl() { # vantv_ctl <pause|resume|toggle|stop|next|previous|seek> [json fields, e.g. '"offset":-20']
  curl -sS -m 10 -X POST -H 'Content-Type: application/json' \
    -d "{\"action\":\"$1\"${2:+,$2}}" "$VANTVAPI/control"
  echo
}
vantv_vlcmd() { # MPRIS-style vlcmd arguments, as the BTT buttons send them
  case "$1" in
    PlayPause) vantv_ctl toggle ;;
    Play) vantv_ctl resume ;;
    Pause) vantv_ctl pause ;;
    Next) vantv_ctl next ;;
    Previous) vantv_ctl previous ;;
    Stop) vantv_ctl stop ;;
    Seek) vantv_ctl seek "\"offset\":$(( ${2#int64:} / 1000000 ))" ;;
    *) echo "Van TV has no $1" >&2; return 1 ;;
  esac
}
### AUDIOBOOKS ### (audiobooks.service, port 8787 — web UI at http://vanpi.lan:8787)
book() { # book <fuzzy title> | book (status) | -p pause/play | -b/-f skip 60s | -l list | -B/-F chapter
  case "${1:-}" in
    "")   curl -s "$BOOKAPI/status" | python3 -m json.tool ;;
    -p)   curl -s "$BOOKAPI/toggle" ;;
    -b)   curl -s "$BOOKAPI/skip?s=-60" ;;
    -f)   curl -s "$BOOKAPI/skip?s=60" ;;
    -B)   curl -s "$BOOKAPI/chapter?d=-1" ;;
    -F)   curl -s "$BOOKAPI/chapter?d=1" ;;
    -l)   curl -s "$BOOKAPI/books" | python3 -c 'import json,sys
for b in json.load(sys.stdin)["books"]: print(b["name"])' ;;
    *)    curl -sG "$BOOKAPI/play" --data-urlencode "q=$*" | python3 -c 'import json,sys
print(json.load(sys.stdin).get("message",""))' ;;
  esac
  echo
}
### MOVIES & TV ### (video-library.service, port 8789 — web UI at http://vanpi.lan:8789)
vid() { # vid <fuzzy title> | -p play/pause | -b/-f 20s | -B/-F 5m | -n/-N next/prev | -x stop | -l <query>
  case "${1:-}" in
    "")   curl -sS "$VIDEOAPI/status" | python3 -m json.tool ;;
    -p)   curl -sS -X POST "$VIDEOAPI/control" --data-urlencode "action=toggle" ;;
    -b)   curl -sS -X POST "$VIDEOAPI/seek" --data-urlencode "seconds=-20" ;;
    -f)   curl -sS -X POST "$VIDEOAPI/seek" --data-urlencode "seconds=20" ;;
    -B)   curl -sS -X POST "$VIDEOAPI/seek" --data-urlencode "seconds=-300" ;;
    -F)   curl -sS -X POST "$VIDEOAPI/seek" --data-urlencode "seconds=300" ;;
    -n)   curl -sS -X POST "$VIDEOAPI/control" --data-urlencode "action=next" ;;
    -N)   curl -sS -X POST "$VIDEOAPI/control" --data-urlencode "action=previous" ;;
    -x)   curl -sS -X POST "$VIDEOAPI/control" --data-urlencode "action=stop" ;;
    -l)   shift; curl -sSG "$VIDEOAPI/search" --data-urlencode "q=$*" | python3 -m json.tool ;;
    -r)   shift; curl -sS -X POST "$VIDEOAPI/play" --data-urlencode "q=$*" --data-urlencode "shuffle=true" ;;
    *)    curl -sS -X POST "$VIDEOAPI/play" --data-urlencode "q=$*" ;;
  esac
  echo
}

### MEDIA TRANSPORT ### (vlcmd, pp, play_status: the Mac's BTT media buttons over ssh)
vlcmd() {
  cmd=$1
  param=$2
  if vantv_active; then vantv_vlcmd "$cmd" "$param"; return $?; fi
  case "$cmd" in
    PlayPause) video_action="toggle" ;;
    Play) video_action="play" ;;
    Pause) video_action="pause" ;;
    Next) video_action="next" ;;
    Previous) video_action="previous" ;;
    Stop) video_action="stop" ;;
    *) video_action="" ;;
  esac
  if [[ -n "$video_action" ]]; then
    # The manager schedules the delayed Sonos check after Play/PlayPause resumes
    # VLC (including pp). Do not race it with a second shell-side repair.
    curl -sS -X POST "${VIDEOAPI:-http://localhost:8789/api}/control" \
      --data-urlencode "action=$video_action"
    return $?
  fi
  wake_display
  dbus-send --type=method_call --dest=org.mpris.MediaPlayer2.vlc /org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player.$cmd $param
}
pp() { vlcmd PlayPause "$@"; }
play_status() {
  local cast
  cast=$(vantv_status)
  if [[ $(jq -r '.player // empty' <<< "$cast" 2>/dev/null) =~ ^(LOADING|PLAYING|PAUSED)$ ]]; then
    jq -rj 'def clock: floor | "\(./3600 | floor):\(. % 3600 / 60 | floor | tostring | if length < 2 then "0" + . else . end):\(. % 60 | tostring | if length < 2 then "0" + . else . end)";
      "\(.video.title // "Van TV") \n\(.position | clock) / \(.duration | clock)"' <<< "$cast"
  elif [[ `pgrep vlc` ]]; then
    keys="multi|REQ|WEBRip|Hi10p|ETRG|YTM\.AM|SKGTV|CaLLiOpeD|CtrlHD|Will1869|10\.?Bit|DTS|DL|SDC|Atmos|hdtv|EVO|WiKi|HMAX|IMAX|MA|VhsRip|HDRip|BDRip|iNTERNAL|True\.HD|1080p|1080i|720p|XviD|HDR10|HD|AC3|AAC|REPACK|5\.1|2\.0|REMUX|PRiCK|AVC|HC|AMZN|HEVC|Blu(R|r)ay|(BR|web)(Rip)?|NF|DDP?(5\.1|2\.0)?|(x|h|X|H)\.?26[4-5]|\d+mb|\d+kbps"
    groups="d3g|CiNEFiLE|CTR|PRoDJi|regret|deef|POIASD|Cinefeel|NTG|NTb|monkee|YELLOWBiRD|Atmos|EPSiLON|cielos|ION10|MeGusta|METCON|x0r|xlf|S8RHiNO|NTG|btx|strife|DD|DBS|TEPES|pawel2006"
    delims="\.|\+|\-"
    pattern="$delims|$keys|$groups"
    # pattern="($delims)(\[?($keys)\]?(?=\.)|(($groups)\.)?\.?$)|\'"
    py_vlc_path='/home/pi/scripts/python-packages/current/pi/scripts/python/vlc_property.py'
    position=`py $py_vlc_path Position`
    total=`py $py_vlc_path TotalTime`
    title=`py $py_vlc_path Title | perl -pe "s~$pattern|~~g"`
    # [[ -n "$title" ]] && log_position > /dev/null
    printf "$title \n$position / $total"
  else
    omx_pos=`curl "http://0.0.0.0:2020/position"`
    if [[ $omx_pos ]]; then printf "$omx_pos"; fi
  fi
}

### LLM TOOLS ###
# Claude Code opens previews and links with xdg-open, which the DISPLAY=:0
# export in ENVIRONMENT sends to the Pi's own desktop (it launched Firefox there).
# Claude helpers explicitly clear DISPLAY; `command claude` skips that guard.
# The codex helpers below use `env -u DISPLAY codex` for the same reason
# (Codex's login flow opened Firefox there too).
codexcfg() { command /usr/bin/vim.tiny ~/.codex/config.toml "$@"; }

# Permissions come from config.toml's default workspace-git-network profile:
# -c, --profile, and --strict-config all force embedded mode (no shared
# background server). Validate config edits with `codex --strict-config`.
# The TUI runs under the capacity-retry wrapper (scripts repo
# shared/codex_autocontinue, installed by its deploy_pi.zsh), which types
# `continue` with backoff after "Selected model is at capacity"; plain codex
# when it isn't installed.
_codex_tui() {
  local wrap=~/.local/share/codex-autocontinue/shared/codex_autocontinue/run
  if [[ -x $wrap ]]; then
    env -u DISPLAY "$wrap" -- codex "$@"
  else
    env -u DISPLAY codex "$@"
  fi
}
codexp() {
  _codex_tui \
    --ask-for-approval never \
    "$@"
}

codexpr() { codexp resume "$@"; }

codexpcan() { codexpr --sandbox danger-full-access --add-dir /mnt/EXFAT512/obd-things --add-dir /dev/bus/usb "$@"; }

# codexpm <ME> [prompt]: M = a astra, s sol, l luna; E = l low, m medium,
# h high, x xhigh, M max, U ultra. `codexpm am "fix it"` = astra at medium effort.
# Mirrors the Mac's ~/.zshrc.
codexpm() {
  local spec=${1-} m e
  [[ ${#spec} -eq 2 ]] || { echo "usage: codexpm <model><effort> [prompt]  (a|s|l)(l|m|h|x|M|U)" >&2; return 2; }
  case ${spec:0:1} in
    a) m=gpt-6-astra ;; s) m=gpt-6.1-sol ;; l) m=gpt-5.6-luna ;;
    *) echo "codexpm: model '${spec:0:1}' not a|s|l" >&2; return 2 ;;
  esac
  case ${spec:1:1} in
    l) e=low ;; m) e=medium ;; h) e=high ;; x) e=xhigh ;; M) e=max ;; U) e=ultra ;;
    *) echo "codexpm: effort '${spec:1:1}' not l|m|h|x|M|U" >&2; return 2 ;;
  esac
  shift
  codexp -m "$m" -c "model_reasoning_effort=\"$e\"" "$@"
}

claudep() { env -u DISPLAY claude --permission-mode bypassPermissions "$@"; }
# claudepm <ME> [prompt]: M = a astra, s sol, l luna, f fable, o opus,
# n sonnet, h haiku; E = l low, m medium, h high, x xhigh, M max.
# Mirrors the Mac's ~/.zshrc.
claudepm() {
  local spec=${1-} m e
  [[ ${#spec} -eq 2 ]] || { echo "usage: claudepm <model><effort> [prompt]  (a|s|l|f|o|n|h)(l|m|h|x|M)" >&2; return 2; }
  case ${spec:0:1} in
    a) m='claude-gpt-6-astra[1m]' ;; s) m='claude-gpt-6.1-sol[1m]' ;; l) m='claude-gpt-5.6-luna[1m]' ;;
    f) m=fable ;; o) m=opus ;; n) m=sonnet ;; h) m=haiku ;;
    *) echo "claudepm: model '${spec:0:1}' not a|s|l|f|o|n|h" >&2; return 2 ;;
  esac
  case ${spec:1:1} in
    l) e=low ;; m) e=medium ;; h) e=high ;; x) e=xhigh ;; M) e=max ;;
    *) echo "claudepm: effort '${spec:1:1}' not l|m|h|x|M" >&2; return 2 ;;
  esac
  shift
  env -u DISPLAY claude --permission-mode bypassPermissions --model "$m" --effort "$e" "$@"
}
# Fresh config-maintenance session; ~/config/AGENTS.md is its only memory.
claudecfg() { (cd ~/config && env -u DISPLAY claude --permission-mode bypassPermissions "$@"); }
clauderm() {
    if [ -z "$1" ] || [ -z "$2" ]; then
        echo "usage: clauderm <project-folder> <conversation-id>" >&2
        return 2
    fi
    rm -v "/home/pi/.claude/projects/$1/$2.jsonl"
}
# Resume via Claude Code's built-in picker (Ctrl+A all projects, Space preview,
# Ctrl+R rename, Ctrl+B branch). A session ID as the first arg resumes directly.
# Mirrors the Mac's ~/.zshrc functions.
clauder() {
    env -u DISPLAY claude --resume "$@"
}

claudepr() {
    env -u DISPLAY claude --permission-mode bypassPermissions --resume "$@"
}

# Prints the pid of a running claude that has session $1 open. Returns 0 when
# one does, 1 when none does, and 2 when the session registry can't be read, so
# callers can refuse to delete rather than guess.
_claude_session_live_pid() {
    local registry="$HOME/.claude/sessions" files status f pid
    [ -d "$registry" ] && [ -r "$registry" ] && [ -x "$registry" ] || return 2
    files=$(command grep --color=auto -rlF --include='*.json' -- "$1" "$registry")
    status=$?
    [ "$status" -gt 1 ] && return 2
    for f in $files; do
        pid=$(basename "$f" .json)
        if kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return 0
        fi
    done
    return 1
}

# Delete sessions (the built-in picker can't). The y/N prompt runs after fzf
# exits because fzf's execute() doesn't render reliably over SSH. Refuses
# sessions still open in a running claude, since it would recreate the
# transcript; the check repeats after confirmation in case one resumed meanwhile.
claudeclean() {
    local out key result sid jsonl confirm live status
    while true; do
        out=$(python3 "$HOME/.claude/session-picker.py" | fzf \
            --ansi \
            --delimiter=$'\t' \
            --with-nth=1 \
            --border=rounded \
            --prompt='Delete session › ' \
            --header=$'Ctrl-D: delete  ·  Type to search  ·  Esc: done' \
            --bind='enter:ignore' \
            --expect=ctrl-d)
        # With --expect, line 1 is the key pressed, line 2 the row.
        key=$(sed -n 1p <<<"$out")
        result=$(sed -n 2p <<<"$out")
        [[ "$key" == "ctrl-d" && -n "$result" ]] || return 0
        sid=$(cut -f2 <<<"$result")
        jsonl=$(cut -f3 <<<"$result")
        live=$(_claude_session_live_pid "$sid"); status=$?
        if [ "$status" -eq 1 ]; then
            printf 'Delete %s ? [y/N] ' "$jsonl"
            read -r confirm < /dev/tty
            if [[ "$confirm" != [yY] ]]; then
                echo "Cancelled."
            else
                live=$(_claude_session_live_pid "$sid"); status=$?
                [ "$status" -eq 1 ] && rm -f -- "$jsonl" && echo "Deleted."
            fi
        fi
        [ "$status" -eq 0 ] && echo "Still open in claude pid $live; exit that session first."
        [ "$status" -eq 2 ] && echo "Can't read ~/.claude/sessions to check for open sessions; nothing deleted."
        read -r -p 'Press Enter to continue...' < /dev/tty
    done
}
codexsearch() { /home/pi/scripts/codexsearch.sh "$@"; }
claudesearch() { /home/pi/scripts/claudesearch.sh "$@"; }
llmsearch() { /home/pi/scripts/llmsearch.sh "$@"; }
# Reopen the Claude/Codex sessions open before a reboot (skips open ones);
# --cron on|off toggles the every-minute snapshot, --dry-run previews.
llmrestore() { /usr/bin/python3 -I -B /home/pi/scripts/llm_restore.py "$@"; }

### CANBUS ###
obd() { builtin cd ~/dev/obd-things; command ls -lah "$@"; }

# Codex command shells need helpers, not secrets, DISPLAY or command shadows.
[[ -n ${CODEX_THREAD_ID-} && $- != *i* ]] && return
shopt -s expand_aliases # preserve inline aliases in noninteractive ssh commands
alias sudo='sudo ' # parse-time expansion of the next word; sudo cannot run functions
vi() { /usr/bin/vim.tiny "$@"; }
rbash() { exec bash "$@"; }
grep() { command grep --color=auto "$@"; }
egrep() { command egrep --color=auto "$@"; }
fgrep() { command find . \( -type d -o -type f \) -iname "$@"; }
pip3() { command python3 -m pip "$@"; }
rgrep() { grep -rni "$1" "${2:-.}" ; }                    # recursively search, fallback to pwd "."
hdel() { history -d $1 && history -w; }
claude() { env -u DISPLAY claude "$@"; }
# Restricted sessions, as plain `codex` was before codexp's permissions moved
# into config.toml's default profile: --sandbox workspace-write (Codex's
# built-in workspace sandbox, network off) keeps the shared background server,
# where -c would not. An explicit sandbox choice, or a non-session
# subcommand, passes through untouched; `command codex` skips the wrapper.
codex() {
  local arg
  for arg in "$@"; do
    case $arg in
      -s|--sandbox|--sandbox=*|--dangerously-bypass-approvals-and-sandbox)
        env -u DISPLAY codex "$@"
        return ;;
    esac
  done
  case ${1-} in
    exec|e|resume|fork)
      env -u DISPLAY codex "$1" --sandbox workspace-write "${@:2}" ;;
    review|login|logout|mcp|plugin|app-server|remote-control|app|completion|update|doctor|sandbox|debug|apply|a|queue|archive|delete|migrate-rollouts|unarchive|cloud|exec-server|features|help|agents)
      env -u DISPLAY codex "$@" ;;
    *)
      env -u DISPLAY codex --sandbox workspace-write "$@" ;;
  esac
}
shopt -s dotglob # match all items in directory except . and ..
shopt -s histappend                      # append to history, don't overwrite it
# Debian's stock interactive defaults supply history control and completion.
# This file returns early when the shell isn't interactive.
# Command definitions above do not depend on those interactive settings.
if [ -f ~/.bash_defaults ]; then . ~/.bash_defaults; fi

### ENVIRONMENT ###
export ITERM_ENABLE_SHELL_INTEGRATION_WITH_TMUX=1
export DISPLAY=:0
export HISTSIZE=1000000
export HISTFILESIZE=10000000
export PYTHONDONTWRITEBYTECODE=1
# PATH and PYTHONPATH are exported, so a nested shell (rbash, tmux, bash -c)
# inherits them: add each entry only when it isn't there yet.
case ":$PATH:" in
  *:/home/pi/.local/bin:*) ;;
  *) export PATH="$PATH:/home/pi/.local/bin" ;;
esac
pi_python_packages=/home/pi/scripts/python-packages/current
case ":${PYTHONPATH:-}:" in
  *:"$pi_python_packages":*) ;;
  *) export PYTHONPATH="$pi_python_packages:$pi_python_packages/shared/python:$pi_python_packages/pi/scripts/python${PYTHONPATH:+:$PYTHONPATH}" ;;
esac
unset pi_python_packages
source ~/secrets/.bash_variables

export POSPATH VLCQTPATH
complete -o default -F _s_complete s
[[ $- == *i* && $(sshroute) == *Tailscale* ]] && echo "vanpi: connected over Tailscale (metered uplink?)"
# Host-local agent settings kept outside this repo.
if [ -f "$HOME/config/pi/agent.bashrc" ]; then . "$HOME/config/pi/agent.bashrc"; fi
if [ -f ~/canbus_funcs.sh ]; then . ~/canbus_funcs.sh; fi
### PROMPT ###
# Match the Mac's skwp Zsh prompt: branch plus one colored circle for each
# kind of working-tree change (deleted, added, modified, then untracked).
__git_prompt_update() {
  local line x y
  __git_prompt_branch=
  __git_prompt_deleted=
  __git_prompt_added=
  __git_prompt_modified=
  __git_prompt_untracked=
  __git_prompt_open=
  __git_prompt_close=

  __git_prompt_branch=$(git_branch) || return 0
  __git_prompt_open='('
  __git_prompt_close=')'
  while IFS= read -r line; do
    x=${line:0:1}
    y=${line:1:1}
    case "$x$y" in
      '??') __git_prompt_untracked=●; continue ;;
      '!!') continue ;;
    esac
    [[ "$x" == A ]] && __git_prompt_added=●
    [[ "$x" == D || "$y" == D ]] && __git_prompt_deleted=●
    [[ "$x" == M || "$y" == M || "$x" == T || "$y" == T ||
       "$x" == U || "$y" == U ]] && __git_prompt_modified=●
  done < <(git status --porcelain=v1 2>/dev/null)
}

# PROMPT_COMMAND is exported, so a nested shell inherits it: prepend only once.
case "${PROMPT_COMMAND:-}" in
  *__git_prompt_update*) ;;
  *) export PROMPT_COMMAND="__git_prompt_update; history -a; history -c; history -r; ${PROMPT_COMMAND:-}" ;;
esac
PS1='\[\e[38;5;81m\]\u\[\e[0m\]@\[\e[38;5;81m\]\h\[\e[0m\] \[\e[38;5;118m\]\w\[\e[0m\] ${__git_prompt_open}\[\e[38;5;81m\]${__git_prompt_branch}\[\e[38;5;166m\]${__git_prompt_deleted}\[\e[38;5;118m\]${__git_prompt_added}\[\e[38;5;161m\]${__git_prompt_modified}\[\e[38;5;135m\]${__git_prompt_untracked}\[\e[0m\]${__git_prompt_close}$ '
