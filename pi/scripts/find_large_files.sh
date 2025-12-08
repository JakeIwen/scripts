#!/usr/bin/env bash
#
# find_space_hogs.sh
#
# Usage:
#   chmod +x find_space_hogs.sh
#   ./find_space_hogs.sh [MIN_FILE_SIZE]
#
# Example:
#   ./find_space_hogs.sh 200M   # show files > 200M (default is 100M)

MIN_SIZE="${1:-100M}"   # minimum file size to report (default: 100M)

echo "=== Biggest directories (excluding /mnt) ==="
# -x : stay on one filesystem (root)
# --exclude=/mnt : skip external mounts under /mnt
# --max-depth=4 : limit depth so output's not insane
sudo du -xh --max-depth=4 --exclude=/mnt / 2>/dev/null \
  | sort -h \
  | tail -n 40

echo
echo "=== Biggest files (> $MIN_SIZE, excluding /mnt) ==="
# find large files, skipping /mnt completely
sudo find / \
  -path /mnt -prune -o \
  -type f -size +"$MIN_SIZE" \
  -printf '%s\t%p\n' 2>/dev/null \
  | sort -n \
  | tail -n 50 \
  | awk '
    function human(x,   unit) {
      split("B K M G T", unit, " ")
      i = 1
      while (x >= 1024 && i < 5) { x /= 1024; i++ }
      return sprintf("%.1f%s", x, unit[i])
    }
    {
      size = $1
      $1 = ""
      sub(/^[ \t]+/, "", $0)
      printf "%8s  %s\n", human(size), $0
    }
  '