#!/bin/zsh
# Installs the capacity-retry wrapper on the Pi at
# ~/.local/share/codex-autocontinue/ (pi/.bashrc's _codex_tui runs its `run`).
# The Mac runs it straight from this checkout, so only the Pi needs a copy.
#
# The copy keeps repo paths (shared/...), because `run` puts its grandparent
# on sys.path and tool_settings finds declarations in ../settings. The
# previous install is kept as codex-autocontinue.previous for rollback:
#   ssh vanpi.lan 'cd ~/.local/share && rm -rf codex-autocontinue &&
#     mv codex-autocontinue.previous codex-autocontinue'
# --test also runs the feature's tests there, including the real-TUI ones.
set -eu -o pipefail

host=vanpi.lan  # LAN only: never push a deploy over the metered uplink
repo=${0:A:h:h:h}
cd "$repo"

ssh -o BatchMode=yes -o ConnectTimeout=5 "$host" true || {
  echo "deploy_pi: $host unreachable on the LAN; not deploying" >&2
  exit 1
}

files=(${(f)"$(git ls-files --cached --others --exclude-standard -- \
  shared/__init__.py shared/python/__init__.py shared/python/tool_settings.py \
  shared/settings/codex-autocontinue.json shared/codex_autocontinue)"})
# The retirement check is a Mac cron job; the Pi copy is only the wrapper.
files=(${files:#*/__pycache__/*})
files=(${files:#*/retirement*})
files=(${files:#*/tests/test_retirement*})
files=(${files:#*/deploy_pi.zsh})
(( ${#files} )) || { echo "deploy_pi: no files to ship" >&2; exit 1; }

COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -cf - -- "${files[@]}" | ssh "$host" '
  set -eu
  cd ~/.local/share
  rm -rf codex-autocontinue.new
  mkdir -p codex-autocontinue.new
  tar -xf - -C codex-autocontinue.new
  if [ -d codex-autocontinue ]; then
    rm -rf codex-autocontinue.previous
    mv codex-autocontinue codex-autocontinue.previous
  fi
  mv codex-autocontinue.new codex-autocontinue'

local_sums=$(shasum -a 256 -- "${files[@]}")
remote_sums=$(print -rN -- "${files[@]}" |
  ssh "$host" 'cd ~/.local/share/codex-autocontinue && xargs -0 sha256sum --')
[[ $local_sums == $remote_sums ]] || {
  echo "deploy_pi: installed files differ from the checkout" >&2
  diff <(print -r -- "$local_sums") <(print -r -- "$remote_sums") >&2 || true
  exit 1
}
ssh "$host" '~/.local/share/codex-autocontinue/shared/codex_autocontinue/run -- true'
echo "pi: installed ${#files} files, hashes match"

if [[ ${1-} == --test ]]; then
  ssh "$host" 'cd ~/.local/share/codex-autocontinue &&
    PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover \
      -s shared/codex_autocontinue/tests -t . 2>&1 | tail -4'
fi
