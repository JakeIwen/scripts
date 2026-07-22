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

declare -f cronp | grep -F '/home/pi/scripts/parse_cron.sh' >/dev/null

result=$(s price_check first second)
[ "$result" = "python-main first second" ]

# The price-check functions run against the same stub: assert only what the
# bashrc owns — argument forwarding and usage guards. main.py behavior is
# covered by its own tests.
db="$HOME/.local/share/price_check/price_check.sqlite3"
result=$(add_pricecheck amazon 54.99 https://example.com/item "Example item")
[ "$result" = "python-main --db $db add amazon 54.99 https://example.com/item Example item" ]
result=$(rm_pricecheck "Example item")
[ "$result" = "python-main --db $db remove Example item" ]
if add_pricecheck amazon 54.99 >/dev/null 2>&1; then
  echo "add_pricecheck accepted too few arguments" >&2
  exit 1
fi
if rm_pricecheck one two >/dev/null 2>&1; then
  echo "rm_pricecheck accepted too many arguments" >&2
  exit 1
fi

if s does_not_exist >/dev/null 2>&1; then
  echo "s unexpectedly found a missing script" >&2
  exit 1
fi

echo "s function tests passed"
