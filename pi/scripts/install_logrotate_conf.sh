#!/bin/bash
# Installs a candidate /etc/logrotate.d/rsyslog after proving the effective
# logrotate configuration still loads: validate a root-owned copy of the main
# conf with the candidate in place of the live file, and require logrotate's
# own exit status 0 (a missing binary or failed sudo refuses). Run as the
# admin user; pi/sync_scripts.sh ships it next to the candidate. The live file
# is replaced only by a rename of a verified, fsynced copy staged beside
# $conf_dir (outside it, so logrotate never reads the half-written copy), so a
# full disk or interrupted write leaves the old file whole.
# usage: install_logrotate_conf.sh CANDIDATE
set -u

candidate=${1:-}
sudo_command=${INSTALL_LOGROTATE_SUDO:-/usr/bin/sudo}
logrotate_command=${INSTALL_LOGROTATE_BIN:-/usr/sbin/logrotate}
main_conf=${INSTALL_LOGROTATE_MAIN:-/etc/logrotate.conf}
conf_dir=${INSTALL_LOGROTATE_DIR:-/etc/logrotate.d}
sync_command=${INSTALL_LOGROTATE_SYNC:-/usr/bin/sync}
target=$conf_dir/rsyslog
stage_dir=$(dirname -- "$conf_dir")
work=""
staged=""

[[ -n "$candidate" && -r "$candidate" ]] || { echo "usage: $0 CANDIDATE" >&2; exit 2; }
if cmp -s -- "$candidate" "$target"; then
  echo "logrotate: $target already current"
  exit 0
fi

as_root() { "$sudo_command" -n "$@"; }
cleanup() {
  [[ -n "$work" ]] && as_root /bin/rm -rf -- "$work" >/dev/null 2>&1
  [[ -n "$staged" ]] && as_root /bin/rm -f -- "$staged" >/dev/null 2>&1
  return 0
}
trap cleanup EXIT
refuse() { echo "logrotate: refusing to install: $*" >&2; exit 1; }

work=$(as_root /usr/bin/mktemp -d /tmp/vanpi-logrotate-check.XXXXXX) || refuse "cannot make a root-owned work dir"
as_root /usr/bin/install -d -m 0755 -o root -g root -- "$work/d" || refuse "cannot stage the check"
for f in "$conf_dir"/*; do
  [[ -f "$f" && "$(basename -- "$f")" != rsyslog ]] || continue
  as_root /usr/bin/install -m 0644 -o root -g root -- "$f" "$work/d/" || refuse "cannot copy $f"
done
as_root /usr/bin/install -m 0644 -o root -g root -- "$candidate" "$work/d/rsyslog" || refuse "cannot stage the candidate"

staged_main=$(/usr/bin/mktemp) || refuse "cannot stage the main config"
sed "s|^include .*logrotate\\.d.*\$|include $work/d|" "$main_conf" > "$staged_main" || refuse "cannot read $main_conf"
grep -q "^include $work/d\$" "$staged_main" || { rm -f -- "$staged_main"; refuse "$main_conf has no include of $conf_dir"; }
as_root /usr/bin/install -m 0644 -o root -g root -- "$staged_main" "$work/logrotate.conf"
status=$?
rm -f -- "$staged_main"
(( status == 0 )) || refuse "cannot stage the main config"

output=$(as_root "$logrotate_command" -d "$work/logrotate.conf" 2>&1)
status=$?
(( status == 0 )) || { printf '%s\n' "$output" >&2; refuse "logrotate -d exited $status"; }
grep -q 'rotating pattern: /var/log/syslog' <<< "$output" ||
  refuse "the candidate's syslog stanza was not loaded"

# Publish: same-filesystem rename of a copy proven byte-identical and flushed.
[[ -f "$target" && ! -L "$target" ]] || [[ ! -e "$target" && ! -L "$target" ]] ||
  refuse "$target is not a regular file"
[[ "$(stat -c %d -- "$stage_dir" 2>/dev/null || stat -f %d -- "$stage_dir")" == \
   "$(stat -c %d -- "$conf_dir" 2>/dev/null || stat -f %d -- "$conf_dir")" ]] ||
  refuse "$stage_dir and $conf_dir are on different filesystems"
staged=$(as_root /usr/bin/mktemp "$stage_dir/.logrotate-rsyslog.XXXXXX") || refuse "cannot stage the new file"
as_root /usr/bin/install -m 0644 -o root -g root -- "$candidate" "$staged" || refuse "cannot write the staged file"
cmp -s -- "$candidate" "$staged" || refuse "the staged file does not match the candidate"
as_root "$sync_command" -- "$staged" || refuse "cannot flush the staged file"
as_root /bin/mv -f -- "$staged" "$target" || refuse "cannot rename the staged file into place"
staged=""
as_root "$sync_command" -- "$conf_dir" || echo "logrotate: warning: could not flush $conf_dir" >&2
echo "logrotate: installed $target"
