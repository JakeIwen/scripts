#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
project_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
ucode_bin=${UCODE:-}

if [ -z "$ucode_bin" ]; then
	ucode_bin=$(command -v ucode 2>/dev/null || true)
fi
[ -n "$ucode_bin" ] || {
	printf 'set UCODE to an executable host ucode binary\n' >&2
	exit 2
}
[ -x "$ucode_bin" ] || {
	printf 'UCODE is not executable: %s\n' "$ucode_bin" >&2
	exit 2
}

exec "$ucode_bin" -L "$project_dir/files/usr/share/ucode" \
	"$script_dir/service-policy-cases.uc"
