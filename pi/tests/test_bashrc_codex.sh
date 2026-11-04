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
test_dir=$(mktemp -d "${TMPDIR:-/tmp}/bashrc-codex-test.XXXXXX")
trap 'rm -rf -- "$test_dir"' EXIT
home="$test_dir/home"
mkdir -p "$home/secrets" "$home/config/pi" "$home/scripts" "$home/work/child"
# Any ordinary-shell setup accidentally reached in Codex mode leaves evidence.
for file in secrets/.bash_variables config/pi/agent.bashrc .bash_defaults canbus_funcs.sh; do
  printf '%s\n' 'export BASHRC_TEST_SECRET=loaded; echo unexpected-setup; touch "$HOME/leaked"' > "$home/$file"
done
cp "$repo/pi/scripts/shell_tricks.bash" "$repo/shared/sh/shell_tricks_common.sh" "$home/scripts/"
printf '%s\n' 'set -euo pipefail' '[[ $(type -t blklbl) == function ]]' \
  '[[ $(type -t grep) == file ]]' '[[ ! -e $HOME/leaked ]]' > "$test_dir/strict.sh"

# The minimum supported interpreter is macOS Bash 3.2; dotted names parse there.
/bin/bash -n "$profile"
env -i HOME="$home" PATH=/usr/bin:/bin CODEX_THREAD_ID=t \
  /bin/bash -c '
  set -Eeuo pipefail
  trap '"'"'printf "FAILED line %s: %s\n" "$LINENO" "$BASH_COMMAND" >&2
    head -c 400 "$HOME/load.out" "$HOME/load.err" >&2 || :'"'"' ERR
  source "$1" > "$HOME/load.out" 2> "$HOME/load.err"
  [[ ! -s $HOME/load.out && ! -s $HOME/load.err && ! -e $HOME/leaked ]]
  [[ -z ${DISPLAY+x} && -z ${BASHRC_TEST_SECRET+x} && -z ${PROMPT_COMMAND+x} ]]
  [[ -z $(alias -p) ]]
  [[ $(type -t blklbl) == function && $(type -t sshroute) == function ]]
  [[ $(type -t ..) == function && $(type -t croot) == function ]]
  [[ $(type -t grep) == file ]]
  [[ $S_REPOS == "$HOME/dev/obd-things" ]]
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
  [[ $PWD == "$HOME/work" ]]
  # Forwarding must preserve a filename containing spaces through the real ls.
  touch "$HOME/a file"
  [[ $(l "$HOME/a file") == *"a file" ]]
  [[ $(sshroute) == "not an ssh session" ]]
  [[ $(fndef ..) == *"builtin cd"* ]]
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
