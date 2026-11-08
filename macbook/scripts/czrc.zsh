#!/bin/zsh -f
# czrc [message words]: commit tracked edits to the Mac shell repo (~/mac-config)
# from any folder. With no words, the message names the functions the commit
# touches ("zshrc: edit fport, add czrc"). If a leftover branch is checked out,
# fast-forward main to it and delete the branch afterwards; stop at git's first
# refusal, never make a merge commit, never force-delete. ~/.zshrc's czrc calls
# this; untracked files are listed and left alone.

# The repo is wherever ~/.zshrc links to; CZRC_REPO overrides it (tests).
repo=${CZRC_REPO:-${${:-$HOME/.zshrc}:A:h}}

# Fill defs_out (name -> definition text) from file text $1. A definition runs
# from its name() line to the next column-0 }, the zshrc layout fndef relies on.
function_defs() {
  local line name= def
  typeset -gA defs_out; defs_out=()
  for line in "${(@f)1}"; do
    if [[ -z $name && $line =~ '^([[:alnum:]_.+-]+)\(\)[[:space:]]*\{' ]]; then
      name=$match[1] def=$line$'\n'
      [[ $line =~ '\{[[:space:]]*(#.*)?$' ]] || { defs_out[$name]=$def; name=; }
    elif [[ -n $name ]]; then
      def+=$line$'\n'
      [[ $line == '}' ]] && { defs_out[$name]=$def; name=; }
    fi
  done
}

# "zshrc: edit fport, add czrc" from the staged function changes. git's
# diff=bash hunk headers name only the function above a one-line edit, so
# compare whole definitions before and after instead.
commit_message() {
  local f n
  local -a words parts
  local -A before after
  for f in ${(f)"$(command git -C "$repo" diff --cached --name-only)"}; do
    [[ $f == zshrc || $f == *.zsh ]] || continue
    function_defs "$(command git -C "$repo" show "HEAD:$f" 2>/dev/null)"; before=("${(@kv)defs_out}")
    function_defs "$(command git -C "$repo" show ":$f")"; after=("${(@kv)defs_out}")
    words=()
    for n in ${(ok)after}; do
      [[ -z ${before[$n]-} ]] && words+=("add $n") || [[ $before[$n] == $after[$n] ]] || words+=("edit $n")
    done
    for n in ${(ok)before}; do [[ -n ${after[$n]-} ]] || words+=("remove $n"); done
    parts+=("${f:t:r}: ${(j:, :)words:-edits}")
  done
  print -r -- "${(j:; :)parts:-Update zshrc}"
}

main() {
  local branch
  local -a untracked
  branch=$(command git -C "$repo" branch --show-current) || return
  [[ -n $branch ]] || { print -u2 "czrc: detached HEAD, nothing changed"; return 1; }
  command git -C "$repo" rev-parse --verify -q HEAD >/dev/null || { print -u2 "czrc: no commits yet, nothing changed"; return 1; }
  # A moved main would swap the live shell files for older ones on checkout, so check before touching anything.
  [[ $branch == main ]] || command git -C "$repo" merge-base --is-ancestor main "$branch" ||
    { print -u2 "czrc: main has moved past $branch; stay on $branch and merge by hand, nothing changed"; return 1; }
  command git -C "$repo" status --short && command git -C "$repo" diff HEAD --stat || return
  untracked=(${(f)"$(command git -C "$repo" ls-files --others --exclude-standard)"})
  (( $#untracked )) && print "czrc: untracked files left alone: ${(j:, :)untracked}"
  command git -C "$repo" add -u || return
  if command git -C "$repo" diff --cached --quiet; then print "nothing to commit"
  else command git -C "$repo" commit -m "${*:-$(commit_message)}" || return; fi
  [[ $branch == main ]] && return 0
  command git -C "$repo" checkout main && command git -C "$repo" merge --ff-only "$branch" && command git -C "$repo" branch -d "$branch"
}

main "$@"
