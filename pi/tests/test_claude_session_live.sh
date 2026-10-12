#!/bin/bash
# _claude_session_live_pid from pi/.bashrc: claudeclean deletes only on status 1.
set -uo pipefail

repo_bashrc=${1:-pi/.bashrc}
test_dir=$(mktemp -d "${TMPDIR:-/tmp}/claude-live-test.XXXXXX")
trap 'rm -rf -- "$test_dir"' EXIT
export HOME="$test_dir/home"
export PROMPT_COMMAND=
mkdir -p "$HOME/.twilio" "$HOME/secrets"
: > "$HOME/.twilio/twilio_creds.sh"
: > "$HOME/secrets/.bash_variables"
# shellcheck source=/dev/null
. "$repo_bashrc" >/dev/null 2>&1

fail() { echo "FAIL: $*" >&2; exit 1; }

_claude_session_live_pid sid-a >/dev/null; status=$?
[ "$status" -eq 2 ] || fail "missing registry should be unreadable (2), got $status"

mkdir -p "$HOME/.claude/sessions"
printf '{"sessionId":"sid-a"}\n' > "$HOME/.claude/sessions/$$.json"
pid=$(_claude_session_live_pid sid-a); status=$?
[ "$status" -eq 0 ] && [ "$pid" = "$$" ] || fail "live session not detected (status $status, pid '$pid')"

_claude_session_live_pid sid-b >/dev/null; status=$?
[ "$status" -eq 1 ] || fail "unknown session should be not-live (1), got $status"

printf '{"sessionId":"sid-c"}\n' > "$HOME/.claude/sessions/999999.json"
_claude_session_live_pid sid-c >/dev/null; status=$?
[ "$status" -eq 1 ] || fail "dead pid should be not-live (1), got $status"

echo "claude session liveness tests passed"
