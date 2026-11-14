#! /bin/bash
# stamp Time Machine's sticky exclusion xattr on node_modules dirs and on a
# fixed list of reinstallable folders. sticky (no -p) because fixed-path mode
# requires root — the old `-p` version silently excluded nothing when run
# unprivileged (tmutil rc=80). the xattr dies with the folder, so the hourly
# cron run re-stamps folders that `npm install` or a cache wipe recreated.

if [ $# -gt 0 ]; then roots=("$@"); else roots=("$HOME/ais" "$HOME/dev"); fi

# reinstallable installs, caches and runtime output that churned the most
# backup space in Oct 2026 (see the TM growth review), plus ~/Downloads (85 GB,
# excluded at Jacob's request, Nov 2026) and ~/.lima, whose always-running
# linux-test-lane VM rewrites a 40 GiB sparse disk image that every hourly
# backup recopied (`linux-test-lane up` rebuilds it from pins.env); missing
# paths are skipped. Photos' MediaAnalysis cache (rebuilt by mediaanalysisd)
# and the model gateway's request logs are rewritten on most backups.
fixed=(
  "$HOME/Downloads"
  "$HOME/.lima"
  "$HOME/Pictures/Photos Library.photoslibrary/private/com.apple.mediaanalysisd/MediaAnalysis"
  "$HOME/.claude/model-gateway/logs"
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

# SQLite files rewritten every few minutes, so every hourly backup stored a
# full fresh copy of each (~600 MB/hour, Nov 2026). Codex's thread history is
# an index it builds from the rollout files in ~/.codex/sessions (it records a
# byte offset per rollout), and those rollouts stay backed up. The rest are
# logs, chart samples and a search index. Each database's -wal and -shm files
# are excluded too; SQLite recreates them, which the hourly re-stamp covers.
sqlite_dbs=(
  "$HOME/.codex/thread_history_1.sqlite"
  "$HOME/.codex/logs_2.sqlite"
  "$HOME/.local/state/mac-panel/history.sqlite"
  "$HOME/.local/share/llm-switchboard/transcript-search.sqlite3"
  "$HOME/Library/Application Support/rtk/history.db"
)
for db in "${sqlite_dbs[@]}"; do fixed+=("$db" "$db-wal" "$db-shm"); done

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
