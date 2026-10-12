#!/bin/bash
# Builds claude-code-proxy from source for Model Gateway on the Pi.
#   usage: build_claude_code_proxy.sh [--tag vX.Y.Z] [--keep-build]
#
# The project's prebuilt linux-arm64 release needs glibc 2.39; Bookworm ships
# 2.36, so the downloaded binary cannot start. A native build links against the
# Pi's own glibc. Model Gateway's `setup` always re-downloads the release binary
# and would replace this build, so on the Pi run this script instead of `setup`
# (`ensure`, `login`, `env`, `doctor` and `status` never download).
set -euo pipefail

repo=raine/claude-code-proxy
dest=/home/pi/.claude/model-gateway/bin/claude-code-proxy
work=/home/pi/.cache/claude-code-proxy-build
cargo_bin=/home/pi/.cargo/bin
min_free_kb=$((4 * 1024 * 1024))   # toolchain, registry, and target dir
tag=
keep_build=0

while [ $# -gt 0 ]; do
  case "$1" in
    --tag) tag=${2:?--tag needs a value}; shift 2 ;;
    --keep-build) keep_build=1; shift ;;
    *) echo "usage: build_claude_code_proxy.sh [--tag vX.Y.Z] [--keep-build]" >&2; exit 2 ;;
  esac
done

mkdir -p "$work"
exec 9>"$work.lock"
flock -n 9 || { echo "another proxy build is running" >&2; exit 1; }

free_kb=$(df -Pk /home/pi | awk 'NR == 2 { print $4 }')
if [ "$free_kb" -lt "$min_free_kb" ]; then
  echo "need 4 GB free on /home/pi for the build; have $((free_kb / 1024)) MB" >&2
  exit 1
fi

if [ -z "$tag" ]; then
  # The same release Model Gateway's setup would fetch.
  tag=$(/usr/bin/curl -fsS --max-time 20 "https://api.github.com/repos/$repo/releases/latest" |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])')
fi
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "unexpected tag: $tag" >&2; exit 1; }

if [ ! -x "$cargo_bin/cargo" ]; then
  echo "installing a minimal Rust toolchain under /home/pi/.rustup and /home/pi/.cargo"
  /usr/bin/curl --proto '=https' --tlsv1.2 -fsS https://sh.rustup.rs |
    sh -s -- -y --profile minimal --default-toolchain stable --no-modify-path
fi
export PATH="$cargo_bin:$PATH"
rustup update stable --no-self-update >/dev/null

rm -rf "$work/src"
git clone --quiet --depth 1 --branch "$tag" "https://github.com/$repo.git" "$work/src"

# Lowest CPU and I/O priority with two jobs: the Pi keeps serving media,
# Home Assistant and backups while this builds.
echo "building $tag (takes a while on a Pi 4)"
(cd "$work/src" &&
  CARGO_TARGET_DIR="$work/target" nice -n 19 ionice -c 3 cargo build --release --locked -j 2)

built="$work/target/release/claude-code-proxy"
version=$("$built" --version)
case "$version" in
  *"${tag#v}"*) ;;
  *) echo "built binary reports '$version', expected ${tag#v}" >&2; exit 1 ;;
esac

# The live path never disappears: keep a copy as .prev, then rename the staged
# binary over it (atomic on the same filesystem).
mkdir -p "$(dirname "$dest")"
trap 'rm -f "$dest.new"' EXIT
install -m 0755 "$built" "$dest.new"
if [ -e "$dest" ]; then cp -p "$dest" "$dest.prev"; fi
mv -f "$dest.new" "$dest"
echo "installed $version at $dest"

if [ "$keep_build" -eq 0 ]; then
  rm -rf "$work"
fi

launcher=/home/pi/.claude/model-gateway/model-gateway.js
if [ -f "$launcher" ]; then
  # Restarts a running proxy whose on-disk version changed.
  if ! node "$launcher" ensure; then
    echo "installed $version, but the gateway did not start; check: node $launcher doctor" >&2
    exit 1
  fi
fi
