#! /bin/bash
# stamp node_modules dirs with the sticky TM exclusion xattr. sticky (no -p)
# because fixed-path mode requires root — the old `-p` version silently
# excluded nothing when run unprivileged (tmutil rc=80).
# the xattr dies with the folder: rerun after `npm install` recreates one.

if [ $# -gt 0 ]; then roots=("$@"); else roots=("$HOME/ais" "$HOME/dev"); fi

n=0
while IFS= read -r -d '' d; do
  tmutil addexclusion "$d" && ((n++))
done < <(find "${roots[@]}" -type d -name node_modules -prune -print0 2>/dev/null)
echo "excluded $n node_modules dirs under: ${roots[*]}"
