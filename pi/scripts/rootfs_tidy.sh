#!/bin/bash
# Conservative root-filesystem cache cleanup. Dry-run unless --apply is given.
# Codex release retention is the shared cleanup_codex_releases.py (the Mac runs
# the same script). It needs root to see non-dumpable processes in /proc, and
# acts on the owner's files with that account's effective IDs (--owner).
set -u

apply=0
case ${1:-} in
  "") ;;
  --apply) apply=1 ;;
  -h|--help)
    echo "usage: rootfs_tidy.sh [--apply]"
    exit 0
    ;;
  *)
    echo "usage: rootfs_tidy.sh [--apply]" >&2
    exit 2
    ;;
esac

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
codex_cleanup=${ROOTFS_TIDY_CODEX_CLEANUP:-$script_dir/cleanup_codex_releases.py}
codex_user=${ROOTFS_TIDY_CODEX_USER:-pi}
python_command=${ROOTFS_TIDY_PYTHON:-/usr/bin/python3}
id_command=${ROOTFS_TIDY_ID:-/usr/bin/id}
sudo_command=${ROOTFS_TIDY_SUDO:-/usr/bin/sudo}
apt_get_command=${ROOTFS_TIDY_APT_GET:-/usr/bin/apt-get}

failed=0

run_codex_cleanup() {
  local uid mode=--dry-run status=0
  (( apply )) && mode=--apply
  uid=$("$id_command" -u 2>/dev/null) || {
    echo "Codex releases: failed (cannot determine effective user)" >&2
    failed=1
    return
  }
  if [[ "$uid" != 0 ]]; then
    echo "Codex releases: skipped (run as root to inspect every process)"
    return
  fi
  "$python_command" -I -B "$codex_cleanup" --owner "$codex_user" --package all "$mode" || status=$?
  case $status in
    0) ;;
    75) echo "Codex releases: skipped (installer lock held)" ;;
    *)
      echo "Codex releases: cleanup failed with status $status" >&2
      failed=1
      ;;
  esac
}

run_apt_clean() {
  local uid
  uid=$("$id_command" -u 2>/dev/null) || {
    echo "apt cache: skipped (cannot determine effective user)"
    return 0
  }
  if [[ "$uid" == 0 ]]; then
    if (( apply )); then
      "$apt_get_command" clean || failed=1
    else
      echo "apt cache: would run apt-get clean as root"
    fi
    return
  fi
  if "$sudo_command" -n true >/dev/null 2>&1; then
    if (( apply )); then
      "$sudo_command" -n "$apt_get_command" clean || failed=1
    else
      echo "apt cache: would run sudo -n apt-get clean"
    fi
  else
    echo "apt cache: skipped (root or passwordless sudo required)"
  fi
}

run_codex_cleanup
run_apt_clean
exit "$failed"
