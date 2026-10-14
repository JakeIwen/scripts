#!/bin/bash
# Open only pre-existing lock inodes. Validators return the identity from the
# same stat that checked ownership and mode, so replacement during open fails.

backup_lock_open() { # <fd-variable> <path> <validator> [validator arguments ...]
  local result_variable=$1 lock_path=$2 validator=$3 opened_fd validated_inode actual_inode
  shift 3
  "$validator" "$lock_path" "$@" validated_inode || return 1
  if [[ -L "$lock_path" || ! -f "$lock_path" ]]; then
    echo "backup lock changed while opening: $lock_path" >&2
    return 1
  fi
  exec {opened_fd}<"$lock_path" || return 1
  actual_inode=$("$JOB_LOCK_STAT" -Lc '%d:%i' -- "/proc/self/fd/$opened_fd") || actual_inode=
  if [[ -z "$actual_inode" || "$actual_inode" != "$validated_inode" ||
        -L "$lock_path" || ! /proc/self/fd/$opened_fd -ef "$lock_path" ]]; then
    exec {opened_fd}<&-
    echo "backup lock changed while opening: $lock_path" >&2
    return 1
  fi
  printf -v "$result_variable" '%s' "$opened_fd"
}

backup_lock_record_pid() { # <held-fd>: truncate the pinned inode, never reopen its pathname
  # The read-only flock descriptor is inherited by dd. Its proc path pins that
  # inode even if the registry entry changes; nocreat also forbids O_CREAT.
  printf '%s\n' "$$" | /usr/bin/dd of="/proc/self/fd/$1" conv=nocreat status=none
}
