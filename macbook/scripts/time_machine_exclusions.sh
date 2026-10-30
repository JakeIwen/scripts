#! /bin/bash
# stamp Time Machine's sticky exclusion xattr on node_modules dirs and on a
# fixed list of reinstallable folders. sticky (no -p) because fixed-path mode
# requires root — the old `-p` version silently excluded nothing when run
# unprivileged (tmutil rc=80). the xattr dies with the folder, so the hourly
# cron run re-stamps folders that `npm install` or a cache wipe recreated.

if [ $# -gt 0 ]; then roots=("$@"); else roots=("$HOME/ais" "$HOME/dev"); fi

# reinstallable installs, caches and runtime output that churned the most
# backup space in Oct 2026 (see the TM growth review); missing paths are skipped.
fixed=(
  "/Applications/Xcode-26.1.0.app"
  "/Applications/Xcode-26.6.0.app"
  "$HOME/Library/Application Support/Spotify/PersistentCache"
  "$HOME/.npm"
  "$HOME/.codex/packages"
  "$HOME/.local/share/claude/versions"
  "$HOME/.vscode/extensions"
  "$HOME/dev/scripts/tmp"
  "$HOME/dev/obd-things/tmp"
  "$HOME/.rbenv"
  "$HOME/.nvm"
)

n=0
while IFS= read -r -d '' d; do
  tmutil addexclusion "$d" && ((n++))
done < <(find "${roots[@]}" -type d -name node_modules -prune -print0 2>/dev/null)
echo "excluded $n node_modules dirs under: ${roots[*]}"

for p in "${fixed[@]}"; do
  [ -e "$p" ] || continue
  case "$(tmutil isexcluded "$p")" in
    "[Excluded]"*) ;;
    *) tmutil addexclusion "$p" && echo "re-excluded $p" ;;
  esac
done
