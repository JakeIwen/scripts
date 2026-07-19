#!/bin/bash
set -euo pipefail

repo_bashrc=${1:-pi/.bashrc}
test_dir=$(mktemp -d "${TMPDIR:-/tmp}/bashrc-s-test.XXXXXX")
trap 'rm -rf -- "$test_dir"' EXIT
export HOME="$test_dir/home"
export PROMPT_COMMAND=
mkdir -p "$HOME/scripts/price_check" "$HOME/configs" "$HOME/.twilio" "$HOME/secrets"
: > "$HOME/.twilio/twilio_creds.sh"
: > "$HOME/secrets/.bash_variables"

printf '%s\n' \
  'import sys' \
  'print("python-main " + " ".join(sys.argv[1:]))' \
  > "$HOME/scripts/price_check/main.py"
: > "$HOME/scripts/price_check/amazon_parser.py"

# shellcheck source=/dev/null
. "$repo_bashrc"

result=$(s price_check first second)
[ "$result" = "python-main first second" ]

printf '%s\n' \
  '# parser<TAB>threshold<TAB>URL<TAB>title (optional)' \
  $'amazon\t55\thttps://example.com/existing\tExisting item' \
  > "$HOME/configs/price_checks.tsv"
if add_pricecheck amazon 55 https://example.com/existing >/dev/null 2>&1; then
  echo "add_pricecheck accepted a URL from the base config" >&2
  exit 1
fi
result=$(add_pricecheck amazon 54.99 https://example.com/item "Example item")
[ "$result" = 'added price check: Example item (amazon, below $54.99)' ]
grep -q '^# Local additions;' "$HOME/configs/price_checks.local.tsv"
expected=$'amazon\t54.99\thttps://example.com/item\tExample item'
[ "$(tail -1 "$HOME/configs/price_checks.local.tsv")" = "$expected" ]
if add_pricecheck amazon 54.99 https://example.com/item >/dev/null 2>&1; then
  echo "add_pricecheck accepted a duplicate URL" >&2
  exit 1
fi
export PRICE_CHECK_SCRIPT="$(dirname "$repo_bashrc")/scripts/price_check/main.py"
result=$(rm_pricecheck "Example item")
[ "$result" = "removed price check: Example item" ]
[ ! -e "$HOME/configs/price_checks.local.tsv" ]

if s does_not_exist >/dev/null 2>&1; then
  echo "s unexpectedly found a missing script" >&2
  exit 1
fi

echo "s function tests passed"
