#!/bin/zsh
# Installs the mod on the Pi, which has no checkout of this repo: its tracked
# files (codexclean aside, which codex/deploy.zsh installs) in
# ~/.local/share/claude-mods/clean-output/, the folder the Pi's
# ~/.claude/settings.json names in env.CLAUDE_CODE_PLUGIN_DIRS. Then runs
# the mod's tests there, on the Pi's own Claude Code. Open Pi sessions pick
# the new copy up with /reload-plugins.
set -eu -o pipefail

here=${0:A:h}
dest=.local/share/claude-mods/clean-output
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

# LAN only: nothing here should cross a metered uplink.
pi=vanpi.lan

git -C "$here" ls-files -z -- . ':!codex' | COPYFILE_DISABLE=1 tar -C "$here" --null -T - -cf - | tar -C "$stage" -xf -
ssh $pi "mkdir -p $dest"
command rsync -a --delete "$stage/" "$pi:$dest/"
ssh $pi "cd $dest && claude plugin test . 2>&1 | tail -3"
