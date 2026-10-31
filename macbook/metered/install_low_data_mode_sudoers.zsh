#!/bin/zsh
set -eux -o pipefail
sudo -v
( set +x; while kill -0 $$; do sudo -n true; sleep 50; done 2>/dev/null ) &
trap "kill $! 2>/dev/null" EXIT
keepalive=$!
cd "${0:A:h}/../.."
probe=(/usr/bin/python3 -B -m macbook.metered.sudo_test)
info=( ${(s: :)$("${probe[@]}" preflight)} )
interface=$info[1]
destination=$info[2]
network=$info[3]
[[ $interface =~ '^en[0-9]+$' ]]
prior=$("${probe[@]}" flag "$interface")
changed=0
stage=$(/usr/bin/mktemp -d)
root_stage=''
cleanup() {
  result=$?
  trap - EXIT
  set +e
  if (( changed )); then
    if [[ $("${probe[@]}" identity) == "$network" ]]; then
      if [[ $prior == 0 ]]; then
        sudo -n /sbin/ifconfig "$interface" -constrained || result=1
      else
        sudo -n /sbin/ifconfig "$interface" constrained || result=1
      fi
      if [[ $("${probe[@]}" flag "$interface") == "$prior" ]]; then
        print -r -- "Prior constrained flag restored: $prior"
      else
        result=1
      fi
    else
      print -u2 'Network changed; refusing to restore a flag on another network.'
      result=1
    fi
  fi
  [[ -z $root_stage ]] || sudo /bin/rm -f "$root_stage"
  /bin/rm -f "$stage/rule"
  /bin/rmdir "$stage"
  kill "$keepalive" 2>/dev/null || true
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
rule=/etc/sudoers.d/metered-low-data-mode
if sudo /bin/test -e "$rule"; then
  sudo /usr/bin/grep -qx '# Managed by macbook/metered' "$rule"
fi
print -r -- '# Managed by macbook/metered' > "$stage/rule"
print -r -- "jacobr ALL=(root) NOPASSWD: /sbin/ifconfig $interface constrained, /sbin/ifconfig $interface -constrained" >> "$stage/rule"
sudo /bin/mkdir -p /etc/sudoers.d
root_stage=$(sudo /usr/bin/mktemp /etc/sudoers.d/.metered-low-data-mode.XXXXXX)
sudo /usr/bin/install -o root -g wheel -m 0440 "$stage/rule" "$root_stage"
sudo /usr/sbin/visudo -cf "$root_stage"
sudo /bin/mv "$root_stage" "$rule"
root_stage=''
before=$(/usr/bin/tmutil latestbackup)
changed=1
sudo -n /sbin/ifconfig "$interface" constrained
[[ $("${probe[@]}" flag "$interface") == 1 ]]
backup_result=0
/usr/bin/tmutil startbackup --block --destination "$destination" || backup_result=$?
print -r -- "Time Machine exit: $backup_result"
/usr/bin/tmutil status
"${probe[@]}" idle >/dev/null
[[ $backup_result == 0 ]]
[[ $("${probe[@]}" flag "$interface") == 1 ]]
after=$(/usr/bin/tmutil latestbackup)
print -r -- "Before: $before" "After: $after"
[[ -n $after && $after != "$before" ]]
print -r -- 'PASS: new completed LAN backup with constrained set. Prior flag will now be restored.'
print -r -- 'Low Data Mode control remains OFF; enable it explicitly in the Mac Panel.'
