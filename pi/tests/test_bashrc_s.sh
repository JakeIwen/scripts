#!/bin/bash
set -euo pipefail

repo_bashrc=${1:-pi/.bashrc}
test_dir=$(mktemp -d "${TMPDIR:-/tmp}/bashrc-s-test.XXXXXX")
trap 'rm -rf -- "$test_dir"' EXIT
export HOME="$test_dir/home"
export PROMPT_COMMAND=
mkdir -p "$HOME/scripts/price_check" "$HOME/.twilio" "$HOME/secrets"
: > "$HOME/.twilio/twilio_creds.sh"
: > "$HOME/secrets/.bash_variables"

printf '%s\n' \
  'import sys' \
  'print("python-main " + " ".join(sys.argv[1:]))' \
  > "$HOME/scripts/price_check/main.py"

# shellcheck source=/dev/null
. "$repo_bashrc"

declare -f cronp | grep -F '/home/pi/scripts/shared/sh/parse_cron.sh' >/dev/null

result=$(s price_check first second)
[ "$result" = "python-main first second" ]

export PRICE_CHECK_SCRIPT="$(dirname "$repo_bashrc")/scripts/price_check/main.py"
result=$(add_pricecheck amazon 54.99 https://example.com/item "Example item")
[ "$result" = 'added price check: Example item' ]
if add_pricecheck amazon 54.99 https://example.com/item >/dev/null 2>&1; then
  echo "add_pricecheck accepted a duplicate URL" >&2
  exit 1
fi
result=$(rm_pricecheck "Example item")
[ "$result" = "removed price check: Example item" ]
[ -f "$HOME/.local/share/price_check/price_check.sqlite3" ]

if s does_not_exist >/dev/null 2>&1; then
  echo "s unexpectedly found a missing script" >&2
  exit 1
fi

echo "s function tests passed"
