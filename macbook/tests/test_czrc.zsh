#!/bin/zsh
# czrc.zsh commits tracked edits, names touched functions, and fast-forwards main
# from a leftover branch, all against a throwaway repo (CZRC_REPO).
set -u

repo_root="${0:A:h:h:h}"
czrc="$repo_root/macbook/scripts/czrc.zsh"
tmp=$(/usr/bin/mktemp -d)
tmp=${tmp:A}
trap '/bin/rm -rf -- "$tmp"' EXIT

fail() {
  print -u2 -- "FAIL: $*"
  exit 1
}

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
# An inherited CZRC_REPO would point the default-path case below at a real repo.
unset CZRC_REPO

czrc_run() { # repo [words]
  local repo=$1; shift
  CZRC_REPO=$repo /bin/zsh -f "$czrc" "$@"
}
czrc_repo="$tmp/czrc"
/bin/mkdir -p "$czrc_repo"
g() { command git -C "$czrc_repo" "$@"; }
g init -q -b main
printf 'alpha() { echo a; }\nbeta() {\n  echo b\n}\n' > "$czrc_repo/zshrc"
g add zshrc && g commit -qm init

printf 'alpha() { echo A; }\nbeta() {\n  echo b\n}\ngamma() { echo g; }\n' > "$czrc_repo/zshrc"
czrc_run "$czrc_repo" >/dev/null 2>&1
[[ "$(g log -1 --format=%s)" == "zshrc: edit alpha, add gamma" ]] || fail "default message: $(g log -1 --format=%s)"

printf 'alpha() { echo A; }\nbeta() {\n  echo B\n}\n' > "$czrc_repo/zshrc"
czrc_run "$czrc_repo" >/dev/null 2>&1
[[ "$(g log -1 --format=%s)" == "zshrc: edit beta, remove gamma" ]] || fail "multi-line/remove message: $(g log -1 --format=%s)"

echo x >> "$czrc_repo/zshrc"; echo u > "$czrc_repo/stray"
out=$(czrc_run "$czrc_repo" why words)
[[ "$(g log -1 --format=%s)" == "why words" ]] || fail "explicit message"
[[ $out == *"untracked files left alone: stray"* ]] || fail "untracked notice: $out"
[[ -z "$(g ls-files stray)" ]] || fail "staged an untracked file"
/bin/rm "$czrc_repo/stray"

out=$(czrc_run "$czrc_repo")
[[ $out == *"nothing to commit"* ]] || fail "clean tree: $out"

g checkout -qb side
echo '# one' >> "$czrc_repo/zshrc"; g commit -qam one
echo '# two' >> "$czrc_repo/zshrc"
czrc_run "$czrc_repo" >/dev/null 2>&1
[[ "$(g branch --show-current)" == main ]] || fail "did not return to main"
[[ -z "$(g branch --list side)" ]] || fail "left the merged branch"
[[ -z "$(g rev-list --merges main)" ]] || fail "made a merge commit"

g checkout -qb diverged
echo '# d' >> "$czrc_repo/zshrc"; g commit -qam d
g checkout -q main; echo '# m' >> "$czrc_repo/zshrc"; g commit -qam m
g checkout -q diverged; echo '# e' >> "$czrc_repo/zshrc"
if czrc_run "$czrc_repo" >/dev/null 2>&1; then fail "merged a diverged branch"; fi
[[ -z "$(g rev-list --merges --all)" ]] || fail "made a merge commit on divergence"
[[ "$(g branch --show-current)" == diverged ]] || fail "left the diverged branch"
[[ -n "$(g diff --name-only)" ]] || fail "touched the diverged branch's edit"
g checkout -q -- zshrc; g checkout -q main

# Compact spelling g(){ ... } is still named, and a detached HEAD changes nothing.
printf 'g(){ echo g; }\n' >> "$czrc_repo/zshrc"; g commit -qam g
/usr/bin/sed -i '' 's/echo g; }$/echo G; }/' "$czrc_repo/zshrc"
czrc_run "$czrc_repo" >/dev/null 2>&1
[[ "$(g log -1 --format=%s)" == "zshrc: edit g" ]] || fail "g(){ spelling: $(g log -1 --format=%s)"
g checkout -q --detach; echo '# det' >> "$czrc_repo/zshrc"; head=$(g rev-parse HEAD)
if czrc_run "$czrc_repo" >/dev/null 2>&1; then fail "accepted detached HEAD"; fi
[[ "$(g rev-parse HEAD)" == "$head" ]] || fail "committed on detached HEAD"
g checkout -q -- zshrc; g checkout -q main
[[ -n "$(g branch --list diverged)" ]] || fail "deleted an unmerged branch"

# An unborn HEAD stops before creating a commit.
/bin/mkdir "$tmp/unborn"; command git -C "$tmp/unborn" init -q -b main; echo x > "$tmp/unborn/zshrc"
if czrc_run "$tmp/unborn" >/dev/null 2>&1; then fail "ran on an unborn HEAD"; fi
command git -C "$tmp/unborn" rev-parse --verify -q HEAD >/dev/null 2>&1 && fail "committed on an unborn HEAD"

# With no override, the repo is wherever ~/.zshrc links to.
/bin/mkdir -p "$tmp/home"; /bin/ln -s "$czrc_repo/zshrc" "$tmp/home/.zshrc"
echo '# via link' >> "$czrc_repo/zshrc"
HOME=$tmp/home /bin/zsh -f "$czrc" via link >/dev/null || fail "default repo from ~/.zshrc link"
[[ "$(g log -1 --format=%s)" == "via link" ]] || fail "default repo commit: $(g log -1 --format=%s)"

print "czrc passed"
