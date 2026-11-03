#!/bin/bash
# Restic's release archive checksum is pinned, not downloaded as trust at install time.
set -eu -o pipefail
umask 077
version=0.19.1
archive="restic_${version}_linux_arm64.bz2"
base="https://github.com/restic/restic/releases/download/v${version}"
# Source: https://github.com/restic/restic/releases/download/v0.19.1/SHA256SUMS
archive_sha=a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465
# SHA-256 of the decompressed, archive-verified binary, for offline idempotence.
binary_sha=2fb45ac6f9071b6f20eb883953a188f9e7c7cb6bbe43c67a2e47ada4e85ee7f0
target=/usr/local/bin/restic
(( EUID == 0 )) || { printf '%s\n' 'Run install_restic.sh with sudo.' >&2; exit 1; }
[[ $(/usr/bin/uname -s) == Linux && $(/usr/bin/uname -m) == aarch64 ]] || {
  printf '%s\n' 'This installer requires Linux arm64.' >&2; exit 1;
}
if [[ -L "$target" ]]; then
  printf '%s\n' 'Refusing a symlink at /usr/local/bin/restic.' >&2
  exit 1
fi
if [[ -f "$target" ]] && printf '%s  %s\n' "$binary_sha" "$target" | /usr/bin/sha256sum --check --status; then
  printf 'restic %s is already installed and hash-verified.\n' "$version"
  exit 0
fi
work=$(/usr/bin/mktemp -d)
staged=
cleanup() {
  [[ -z "$staged" ]] || /usr/bin/rm -f -- "$staged"
  /usr/bin/rm -f -- "$work/$archive" "$work/restic"
  /usr/bin/rmdir -- "$work"
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
/usr/bin/curl --fail --location --proto '=https' --tlsv1.2 --connect-timeout 15 --max-time 600 \
  "$base/$archive" --output "$work/$archive"
printf '%s  %s\n' "$archive_sha" "$work/$archive" | /usr/bin/sha256sum --check --status || {
  printf '%s\n' 'Restic archive SHA-256 mismatch; nothing installed.' >&2; exit 1;
}
/usr/bin/bzip2 --decompress --stdout "$work/$archive" > "$work/restic"
printf '%s  %s\n' "$binary_sha" "$work/restic" | /usr/bin/sha256sum --check --status
staged=$(/usr/bin/mktemp /usr/local/bin/.restic.XXXXXXXX)
/usr/bin/install -o root -g root -m 0755 -- "$work/restic" "$staged"
/usr/bin/mv -T -- "$staged" "$target"
staged=
"$target" version
