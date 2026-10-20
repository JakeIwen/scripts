#!/bin/zsh
# Builds codexclean and installs it on the Pi, then this Mac: the build in
# ~/.local/share/codexclean/ and a launcher at ~/.local/bin/codexclean, which
# Codex's !commands (a login shell) find on PATH on both machines.
#
# Building needs Node >= 22.13 here; the installed build runs on any Node
# from 20, which is what the Pi has (/usr/bin/node).
set -eu -o pipefail

here=${0:A:h}
build=$(mktemp -d)
trap 'rm -rf "$build"' EXIT

node --no-warnings "$here/build.mjs" "$build/codexclean"

# A login shell's node: nvm is not loaded there, Homebrew's is.
mac_node=/opt/homebrew/bin/node
[[ -x $mac_node ]] || mac_node=$(command -v node)

launcher() {
  printf '#!/bin/sh\nexec %s "$HOME/.local/share/codexclean/codex/codexclean.mjs" "$@"\n' "$1"
}

ssh vanpi 'mkdir -p ~/.local/share/codexclean ~/.local/bin'
command rsync -a --delete "$build/codexclean/" vanpi:.local/share/codexclean/
launcher /usr/bin/node | ssh vanpi 'cat > ~/.local/bin/codexclean && chmod 755 ~/.local/bin/codexclean && ~/.local/bin/codexclean --help >/dev/null && echo "pi: installed"'

mkdir -p ~/.local/share/codexclean ~/.local/bin
command rsync -a --delete "$build/codexclean/" ~/.local/share/codexclean/
launcher "$mac_node" >| ~/.local/bin/codexclean
chmod 755 ~/.local/bin/codexclean
~/.local/bin/codexclean --help >/dev/null && echo "mac: installed"
