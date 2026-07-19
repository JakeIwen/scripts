#!/bin/zsh

set -euo pipefail

usage() {
  /bin/cat <<'EOF'
Usage:
  forget-ssh-host-keys.zsh [--yes] [--known-hosts PATH] HOST
  forget-ssh-host-keys.zsh [--yes] [--known-hosts PATH] --subnet PREFIX

Examples:
  forget-ssh-host-keys.zsh 192.168.6.103
  forget-ssh-host-keys.zsh --subnet 192.168.6
  forget-ssh-host-keys.zsh --yes --subnet 192.168.6

The subnet form removes entries for PREFIX.0 through PREFIX.255. It uses
ssh-keygen -R, so both plain-text and hashed known_hosts entries are handled.
The original file is preserved beside it with a timestamped .backup suffix.
EOF
}

known_hosts_file="${HOME}/.ssh/known_hosts"
assume_yes=0
mode=host
target=""

while (( $# > 0 )); do
  case "$1" in
    --known-hosts)
      (( $# >= 2 )) || { usage >&2; exit 2; }
      known_hosts_file="$2"
      shift 2
      ;;
    --subnet)
      (( $# >= 2 )) || { usage >&2; exit 2; }
      mode=subnet
      target="$2"
      shift 2
      ;;
    --yes)
      assume_yes=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      print -u2 -- "Unknown option: $1"
      usage >&2
      exit 2
      ;;
    *)
      [[ -z "$target" ]] || { print -u2 -- "Only one host or subnet is allowed."; exit 2; }
      target="$1"
      shift
      ;;
  esac
done

[[ -n "$target" ]] || { usage >&2; exit 2; }
[[ -f "$known_hosts_file" ]] || {
  print -u2 -- "known_hosts file not found: $known_hosts_file"
  exit 1
}
[[ -r "$known_hosts_file" && -w "$known_hosts_file" ]] || {
  print -u2 -- "known_hosts file is not readable and writable: $known_hosts_file"
  exit 1
}

typeset -a targets
if [[ "$mode" == subnet ]]; then
  subnet_parts=("${(@s:.:)target}")
  (( ${#subnet_parts[@]} == 3 )) || {
    print -u2 -- "Subnet prefix must contain three octets, such as 192.168.6."
    exit 2
  }
  for octet in "${subnet_parts[@]}"; do
    [[ "$octet" == <-> ]] || { print -u2 -- "Invalid subnet prefix: $target"; exit 2; }
    octet_value=$(( 10#$octet ))
    (( octet_value >= 0 && octet_value <= 255 )) || {
      print -u2 -- "Invalid subnet prefix: $target"
      exit 2
    }
  done
  for host_octet in {0..255}; do
    targets+=("${target}.${host_octet}")
  done
else
  targets=("$target")
fi

if (( ! assume_yes )); then
  if [[ "$mode" == subnet ]]; then
    print -- "This will forget SSH host keys for ${target}.0 through ${target}.255."
  else
    print -- "This will forget SSH host keys for ${target}."
  fi
  print -- "File: $known_hosts_file"
  read "answer?Continue? [y/N] "
  [[ "$answer" == [yY] || "$answer" == [yY][eE][sS] ]] || exit 1
fi

work_dir=$(/usr/bin/mktemp -d /private/tmp/forget-ssh-host-keys.XXXXXX)
case "$work_dir" in
  /private/tmp/forget-ssh-host-keys.*) ;;
  *) print -u2 -- "Unexpected temporary directory: $work_dir"; exit 1 ;;
esac
work_file="${work_dir}/known_hosts"
staged_file="${known_hosts_file}.staged.$$"

cleanup() {
  /bin/rm -f -- "$work_file" "${work_file}.old" "$staged_file" 2>/dev/null || true
  /bin/rmdir "$work_dir" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

/bin/cp -p "$known_hosts_file" "$work_file"
before_lines=$(/usr/bin/wc -l < "$work_file" | /usr/bin/tr -d ' ')

for host in "${targets[@]}"; do
  /usr/bin/ssh-keygen -q -R "$host" -f "$work_file" >/dev/null 2>&1
  /usr/bin/ssh-keygen -q -R "[${host}]:22" -f "$work_file" >/dev/null 2>&1
done

after_lines=$(/usr/bin/wc -l < "$work_file" | /usr/bin/tr -d ' ')
removed_lines=$(( before_lines - after_lines ))
if (( removed_lines == 0 )); then
  print -- "No matching SSH host-key entries were found."
  exit 0
fi

timestamp=$(/bin/date +%Y%m%d-%H%M%S)
backup_file="${known_hosts_file}.backup-${timestamp}"
[[ ! -e "$backup_file" ]] || {
  print -u2 -- "Backup already exists: $backup_file"
  exit 1
}

/bin/cp -p "$known_hosts_file" "$backup_file"
/bin/cp -p "$work_file" "$staged_file"
/bin/mv "$staged_file" "$known_hosts_file"

print -- "Removed $removed_lines known_hosts line(s)."
print -- "Backup: $backup_file"
print -- "The next SSH connection will ask you to verify each forgotten host key."
