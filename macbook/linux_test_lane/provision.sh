#!/bin/bash
# Provision the Linux test lane VM to match vanpi's runtime. Runs as root inside
# the VM, read from the checkout's read-only mount; the Mac CLI (`up`) calls it
# only when its stamp (a hash of this file, pins.env and requirements.txt)
# differs from the one recorded on the VM, so every step must be idempotent.
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

# Tools the Pi has that Linux-only tests probe for: borg, testparm, hd-idle,
# fuser and flock/findmnt/unshare (util-linux), plus the runner's own git.
export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=300 update -q
apt-get -o DPkg::Lock::Timeout=300 install -y -q --no-install-recommends \
  python3 python3-venv git rsync curl ca-certificates bzip2 xz-utils unzip \
  borgbackup samba-common-bin hd-idle psmisc procps util-linux sqlite3 jq tmux less zsh

expect() {
  local name=$1 wanted=$2 found=$3
  [[ "$found" == "$wanted" ]] || {
    echo "$name is $found but the Pi runs $wanted; update pins.env only if the Pi changed." >&2
    exit 1
  }
}
expect python3 "$EXPECT_PYTHON" "$(/usr/bin/python3 -c 'import platform; print(platform.python_version())')"
expect bash "$EXPECT_BASH" "$(/usr/bin/bash -c 'IFS=.; echo "${BASH_VERSINFO[*]:0:3}"')"
expect borg "borg $EXPECT_BORG" "$(/usr/bin/borg --version)"

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

mkdir -p "$state"
printf '%s\n' "$stamp" > "$state/provisioned.tmp"
mv -f "$state/provisioned.tmp" "$state/provisioned"
echo "Provisioned: python $EXPECT_PYTHON, bash $EXPECT_BASH, borg $EXPECT_BORG, node v$NODE_VERSION, rclone v$RCLONE_VERSION, $(/usr/local/bin/restic version | cut -d' ' -f1-2)."
