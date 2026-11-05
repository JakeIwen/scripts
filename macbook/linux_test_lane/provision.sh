#!/bin/bash
# Provision the Linux test lane VM to match vanpi's runtime. Runs as root inside
# the VM, read from the checkout's read-only mount; the Mac CLI (`up`) calls it
# only when its stamp (provisioning inputs and upstream binary installers)
# differs from the one recorded on the VM, or for explicit `up --repair`.
# Every step must be idempotent.
set -eu -o pipefail
umask 022

stamp=${1:?usage: provision.sh STAMP VENV}
venv=${2:?usage: provision.sh STAMP VENV}
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../.." && pwd)
# shellcheck source=pins.env
source "$here/pins.env"
state=/var/lib/linux-test-lane

(( EUID == 0 )) || { echo 'provision.sh must run as root.' >&2; exit 1; }
. /etc/os-release
[[ "$VERSION_CODENAME" == bookworm && $(uname -m) == aarch64 ]] || {
  echo "Expected Debian bookworm aarch64, found $PRETTY_NAME $(uname -m)." >&2; exit 1;
}

expect() {
  local name=$1 wanted=$2 found=$3
  [[ "$found" == "$wanted" ]] || {
    echo "$name is $found but the Pi runs $wanted; update pins.env only if the Pi changed." >&2
    exit 1
  }
}
verify_tools() {
  local tool server_version restic_version
  for tool in python3 git rsync curl bzip2 xz unzip borg testparm hd-idle fuser \
              ps flock findmnt unshare sqlite3 jq tmux less zsh rsyslogd \
              /usr/local/bin/node /usr/local/bin/rclone "$venv/bin/python" \
              "$venv/bin/offsite-restic" "$venv/bin/offsite-rest-server"; do
    command -v "$tool" >/dev/null || { echo "Missing required lane tool: $tool" >&2; return 1; }
  done
  expect python3 "$EXPECT_PYTHON" "$(/usr/bin/python3 -c 'import platform; print(platform.python_version())')"
  expect bash "$EXPECT_BASH" "$(/usr/bin/bash -c 'IFS=.; echo "${BASH_VERSINFO[*]:0:3}"')"
  expect borg "borg $EXPECT_BORG" "$(/usr/bin/borg --version)"
  expect node "v$NODE_VERSION" "$(/usr/local/bin/node --version)"
  expect rclone "rclone v$RCLONE_VERSION" "$(/usr/local/bin/rclone version | head -1)"
  restic_version=$(cd "$repo" && PYTHONPATH="$repo/pi/scripts/backup" /usr/bin/python3 -B -c '
import hashlib, sys
from pathlib import Path
from offsite_snapshot import restic_pin
pin = restic_pin(Path("pi/scripts"))
if hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest() != pin["binary_sha"]:
    raise SystemExit("Lane restic differs from its verified pin")
print(pin["version"])
' "$venv/bin/offsite-restic")
  expect restic "$restic_version" "$("$venv/bin/offsite-restic" version | cut -d' ' -f2)"
  server_version=$(cd "$repo" && /usr/bin/python3 -B -c 'from synology.offsite.binaries import VERSION; print(VERSION)')
  "$venv/bin/offsite-rest-server" --version | grep -F "rest-server $server_version compiled" >/dev/null
  (cd "$venv/bin" && sha256sum --check --status offsite-rest-server.sha256)
  "$venv/bin/python" -c 'import flask, werkzeug'
}

# Even an unchanged stamp cannot authorize a cached pass with missing tools.
if [[ "$stamp" == --verify ]]; then
  verify_tools
  exit 0
fi

# Tools the Pi has that Linux-only tests probe for, plus the runner's own git.
export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=300 update -q
reinstall=()
[[ ${3:-} != --repair ]] || reinstall=(--reinstall)
apt-get -o DPkg::Lock::Timeout=300 install -y -q --no-install-recommends "${reinstall[@]}" \
  python3 python3-venv git rsync curl ca-certificates bzip2 xz-utils unzip \
  borgbackup samba-common-bin hd-idle psmisc procps util-linux sqlite3 jq tmux less zsh rsyslog

work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
fetch() {
  local url=$1 sha=$2 output=$3
  curl --fail --location --proto '=https' --tlsv1.2 --connect-timeout 15 --max-time 600 \
    --silent --show-error "$url" --output "$output"
  printf '%s  %s\n' "$sha" "$output" | sha256sum --check --status || {
    echo "SHA-256 mismatch for $url; nothing installed." >&2; exit 1;
  }
}

if [[ "$(/usr/local/bin/node --version 2>/dev/null || true)" != "v$NODE_VERSION" ]]; then
  name="node-v$NODE_VERSION-linux-arm64"
  fetch "https://nodejs.org/dist/v$NODE_VERSION/$name.tar.xz" "$NODE_SHA256" "$work/node.tar.xz"
  mkdir -p /usr/local/lib/nodejs
  rm -rf -- "/usr/local/lib/nodejs/$name"
  tar -xJf "$work/node.tar.xz" -C /usr/local/lib/nodejs
  ln -sfn "/usr/local/lib/nodejs/$name/bin/node" /usr/local/bin/node
fi
expect node "v$NODE_VERSION" "$(/usr/local/bin/node --version)"

if [[ "$(/usr/local/bin/rclone version 2>/dev/null | head -1 || true)" != "rclone v$RCLONE_VERSION" ]]; then
  name="rclone-v$RCLONE_VERSION-linux-arm64"
  fetch "https://downloads.rclone.org/v$RCLONE_VERSION/$name.zip" "$RCLONE_SHA256" "$work/rclone.zip"
  unzip -q -j "$work/rclone.zip" "$name/rclone" -d "$work"
  install -o root -g root -m 0755 "$work/rclone" /usr/local/bin/rclone
fi
expect rclone "rclone v$RCLONE_VERSION" "$(/usr/local/bin/rclone version | head -1)"

# The production installer owns restic's pin and verification.
bash "$repo/pi/scripts/backup/install_restic.sh"

# A fresh venv per provisioning: never reuse the Mac's .local/test-venv binaries.
rm -rf -- "$venv"
runuser -u "$LANE_USER" -- /usr/bin/python3 -m venv "$venv"
runuser -u "$LANE_USER" -- "$venv/bin/python" -m pip install -q --disable-pip-version-check \
  --no-input -r "$here/requirements.txt"

# Reuse the receiver's archive pin, bounded download and member validation.
# Only the test venv receives rest-server; no receiver/service is installed.
(cd "$repo" && /usr/bin/python3 -B -c '
import sys
from synology.offsite.binaries import fetch_verified, target_architecture
fetch_verified(target_architecture(), sys.argv[1])
' "$work/rest-server")
install -o root -g root -m 0755 "$work/rest-server/rest-server" "$venv/bin/offsite-rest-server"
ln -s /usr/local/bin/restic "$venv/bin/offsite-restic"
(cd "$venv/bin" && sha256sum offsite-rest-server > offsite-rest-server.sha256)
verify_tools

mkdir -p "$state"
printf '%s\n' "$stamp" > "$state/provisioned.tmp"
mv -f "$state/provisioned.tmp" "$state/provisioned"
echo "Provisioned: python $EXPECT_PYTHON, bash $EXPECT_BASH, borg $EXPECT_BORG, node v$NODE_VERSION, rclone v$RCLONE_VERSION, $(/usr/local/bin/restic version | cut -d' ' -f1-2)."
