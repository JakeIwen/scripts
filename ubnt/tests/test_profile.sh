#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
profile="$script_dir/../persistent/profile"
test_root=$(mktemp -d "${TMPDIR:-/tmp}/ubnt-profile-test.XXXXXX")
trap 'rm -rf "$test_root"' EXIT HUP INT TERM

mkdir -p "$test_root/config"
printf '%s\n' 'profile_loaded=yes' > "$test_root/config/.profile"

HOME=$test_root
export HOME
. "$profile"

[ "${profile_loaded:-}" = yes ]
printf 'profile: ok\n'
