#!/bin/bash
# Exercise the real Pi profile as BASH_ENV, including nested strict-mode scripts.
set -Eeuo pipefail
# A bare failing [[ ]] exits silently under set -e; name it so a failure seen
# only in captured parallel-runner output can be diagnosed.
trap 'printf "FAILED line %s: %s\n" "$LINENO" "$BASH_COMMAND" >&2' ERR
# Bash ignores BASH_ENV when stdin is a socket (it assumes rshd/sshd and reads
# ~/.bashrc instead), and a parallel runner or agent harness may pass one.
exec < /dev/null
repo=$(cd -- "$(dirname -- "$0")/../.." && pwd)
profile="$repo/pi/.bashrc"
# Strip a trailing slash: macOS TMPDIR ends in one, and $PWD collapses the doubled
# slash while $HOME keeps it, so the PWD assertion below would mismatch.
tmp_base=${TMPDIR:-/tmp}
test_dir=$(mktemp -d "${tmp_base%/}/bashrc-codex-test.XXXXXX")
trap 'rm -rf -- "$test_dir"' EXIT
home="$test_dir/home"
mkdir -p "$home/secrets" "$home/config/pi" "$home/scripts" "$home/work/child"
# Any ordinary-shell setup accidentally reached in Codex mode leaves evidence.
for file in secrets/.bash_variables config/pi/agent.bashrc .bash_defaults canbus_funcs.sh; do
  printf '%s\n' 'export BASHRC_TEST_SECRET=loaded; echo unexpected-setup; touch "$HOME/leaked"' > "$home/$file"
done
cp "$repo/pi/scripts/shell_tricks.bash" "$repo/shared/sh/shell_tricks_common.sh" "$home/scripts/"
# Nested shells are bash 3.2 too, where set -e ignores a failing [[ ]]: fail explicitly.
printf '%s\n' 'set -euo pipefail' \
  '[[ $(type -t blklbl) == function ]] || { echo "strict: blklbl not a function" >&2; exit 1; }' \
  '[[ $(type -t grep) == file ]] || { echo "strict: grep shadowed" >&2; exit 1; }' \
  '[[ ! -e $HOME/leaked ]] || { echo "strict: ordinary setup leaked" >&2; exit 1; }' > "$test_dir/strict.sh"

# The minimum supported interpreter is macOS Bash 3.2; dotted names parse there.
/bin/bash -n "$profile"
env -i HOME="$home" PATH=/usr/bin:/bin CODEX_THREAD_ID=t \
  /bin/bash -c '
  set -Eeuo pipefail
  trap '"'"'printf "FAILED line %s: %s\n" "$LINENO" "$BASH_COMMAND" >&2
    head -c 400 "$HOME/load.out" "$HOME/load.err" >&2 || :'"'"' ERR
  source "$1" > "$HOME/load.out" 2> "$HOME/load.err"
  [[ ! -s $HOME/load.out && ! -s $HOME/load.err && ! -e $HOME/leaked ]] || { echo "expected: startup not silent or leaked setup" >&2; exit 1; }
  [[ -z ${DISPLAY+x} && -z ${BASHRC_TEST_SECRET+x} && -z ${PROMPT_COMMAND+x} ]] || { echo "expected: DISPLAY/secret/PROMPT_COMMAND unset" >&2; exit 1; }
  [[ -z $(alias -p) ]] || { echo "expected: no aliases defined" >&2; exit 1; }
  [[ $(type -t blklbl) == function && $(type -t sshroute) == function ]] || { echo "expected: blklbl and sshroute are functions" >&2; exit 1; }
  [[ $(type -t ..) == function && $(type -t croot) == function ]] || { echo "expected: .. and croot are functions" >&2; exit 1; }
  [[ $(type -t grep) == file ]] || { echo "expected: grep is the real file, not shadowed" >&2; exit 1; }
  [[ $S_REPOS == "$HOME/dev/obd-things" ]] || { echo "expected: S_REPOS == $HOME/dev/obd-things" >&2; exit 1; }
  # Source again as a strict script would: definitions are silent and repeatable.
  source "$1"
  while read -r _ _ name; do
    kind=$(unset -f "$name"; type -t "$name" || :)
    if [[ -n $kind ]]; then
      # macOS ships a Perl pp command that the target Pi does not have.
      if [[ $name == pp && $OSTYPE == darwin* && $kind == file ]]; then
        printf "%s\n" "SKIP: pp collision assertion; macOS-only Perl executable" >&2
        continue
      fi
      printf "command shadow: %s (%s)\n" "$name" "$kind" >&2
      exit 1
    fi
  done < <(declare -F)
  builtin cd "$HOME/work/child"
  ..
  [[ $PWD == "$HOME/work" ]] || { echo "expected: PWD == $HOME/work after .." >&2; exit 1; }
  # Forwarding must preserve a filename containing spaces through the real ls.
  touch "$HOME/a file"
  [[ $(l "$HOME/a file") == *"a file" ]] || { echo "expected: l preserves a filename with spaces" >&2; exit 1; }
  [[ $(sshroute) == "not an ssh session" ]] || { echo "expected: sshroute says not an ssh session" >&2; exit 1; }
  [[ $(fndef ..) == *"builtin cd"* ]] || { echo "expected: fndef .. shows builtin cd" >&2; exit 1; }
  export BASH_ENV="$1"
  /bin/bash "$2"
  ' _ "$profile" "$test_dir/strict.sh"

# Automatic startup, not just an explicit source, must be silent too.
status=0
env -i HOME="$home" PATH=/usr/bin:/bin CODEX_THREAD_ID=t BASH_ENV="$profile" \
  /bin/bash -euo pipefail -c '[[ $(type -t blklbl) == function ]]' \
  > "$test_dir/startup.out" 2> "$test_dir/startup.err" || status=$?
if [[ $status -ne 0 || -s $test_dir/startup.out || -s $test_dir/startup.err ]]; then
  echo "automatic startup exited $status or was not silent:" >&2
  head -c 400 "$test_dir/startup.out" "$test_dir/startup.err" >&2
  exit 1
fi
echo 'Codex bashrc boundary tests passed'
