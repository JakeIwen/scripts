#!/bin/bash
# Exercises pi/scripts/model_gateway_setup_guard.cjs through real node processes.
set -euo pipefail

guard=$(cd "$(dirname "$0")/../scripts" && pwd)/model_gateway_setup_guard.cjs
test_dir=$(mktemp -d "${TMPDIR:-/tmp}/gateway-guard-test.XXXXXX")
trap 'rm -rf -- "$test_dir"' EXIT

export CLAUDE_CONFIG_DIR="$test_dir/.claude"
cli="$CLAUDE_CONFIG_DIR/plugins/cache/eigenwise-toolshed/model-gateway/0.54.2/bin/model-gateway.js"
launcher="$CLAUDE_CONFIG_DIR/model-gateway/model-gateway.js"
updater="$CLAUDE_CONFIG_DIR/model-gateway/update.js"
unrelated="$test_dir/other-app/update.js"
lookalike="$test_dir/projects/model-gateway/update.js"
lookalike_cli="$test_dir/projects/model-gateway/bin/model-gateway.js"
for file in "$cli" "$launcher" "$updater" "$unrelated" "$lookalike" "$lookalike_cli"; do
  mkdir -p "$(dirname "$file")"
  printf 'console.log("ran " + process.argv.slice(2).join(" "))\n' > "$file"
done

fail() { echo "FAIL: $*" >&2; exit 1; }

expect_blocked() {
  local out status=0
  out=$(node --require "$guard" "$@" 2>&1) || status=$?
  [ "$status" -eq 3 ] || fail "expected exit 3 for $*, got $status"
  [[ "$out" != *"ran "* ]] || fail "script still ran for $*"
}

expect_runs() {
  local out
  out=$(node --require "$guard" "$@" 2>&1) || fail "expected $* to run: $out"
  [[ "$out" == "ran"* ]] || fail "unexpected output for $*: $out"
}

expect_blocked "$cli" setup
expect_blocked "$cli" setup --preserve-wiring
expect_blocked "$launcher" setup
expect_blocked "$updater"

expect_runs "$cli" ensure --quiet
expect_runs "$cli" doctor
expect_runs "$launcher" login --device
expect_runs "$unrelated"
expect_runs "$lookalike"
expect_runs "$lookalike_cli" setup

# A relative path to the real CLI is still the CLI.
(cd "$(dirname "$cli")/.." && expect_blocked bin/model-gateway.js setup)

# Under -e/-p, argv entries are data for the evaluated code, not a script.
out=$(node --require "$guard" -e 'console.log("evaluated")' "$updater" 2>&1) || fail "-e was blocked: $out"
[ "$out" = evaluated ] || fail "unexpected -e output: $out"
out=$(node --require "$guard" -p '"printed"' "$cli" setup 2>&1) || fail "-p was blocked: $out"
[ "$out" = printed ] || fail "unexpected -p output: $out"
MODEL_GATEWAY_ALLOW_SETUP=1 expect_runs "$cli" setup

echo "model gateway setup guard tests passed"
