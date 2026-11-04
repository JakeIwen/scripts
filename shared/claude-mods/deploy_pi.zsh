#!/bin/zsh
# Installs a Claude Code mod on the Pi, which has no checkout of this repo:
# the mod's tracked files (clean-output's codex/ aside, which its
# codex/deploy.zsh installs) in ~/.local/share/claude-mods/<mod>/, a folder
# the Pi's ~/.claude/settings.json names in env.CLAUDE_CODE_PLUGIN_DIRS (add a
# new mod's there once). Then runs the mod's tests there, on the Pi's own
# Claude Code. Open Pi sessions pick the new copy up with /reload-plugins.
#   zsh shared/claude-mods/deploy_pi.zsh <mod>
set -eu -o pipefail

mod=${1:?usage: deploy_pi.zsh <mod>}
here=${0:A:h}/$mod
[[ -f $here/.claude-plugin/plugin.json ]] || { echo "deploy_pi: no mod named $mod" >&2; exit 1; }
dest=.local/share/claude-mods/$mod
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

# LAN only: nothing here should cross a metered uplink.
pi=vanpi.lan

git -C "$here" ls-files -z -- . ':!codex' | COPYFILE_DISABLE=1 tar -C "$here" --null -T - -cf - | tar -C "$stage" -xf -
ssh $pi "mkdir -p $dest"
command rsync -a --delete "$stage/" "$pi:$dest/"
# pipefail on the Pi's side too: without it the remote exit is tail's, so a
# failing test run still read as a successful deploy.
ssh $pi "set -o pipefail; cd $dest && claude plugin test . 2>&1 | tail -3"
