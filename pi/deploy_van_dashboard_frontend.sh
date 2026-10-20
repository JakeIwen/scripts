#!/usr/bin/env bash
set -euo pipefail

# Scoped deployment of the React dashboard release that van-dashboard.service
# serves on :8788.  This helper deliberately derives its source checkout,
# stages only the built frontend, and never invokes the repository-wide sync
# (which is tied to the primary trusted checkout).

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "$script_dir/.." && pwd -P)"
frontend_dir="$repo_root/pi/apps/van_dashboard/frontend"
target="${VAN_DASHBOARD_FRONTEND_TARGET:-pi@vanpi.lan}"
mode=deploy

usage() {
  echo "usage: $0 [--target user@host] [--rollback]" >&2
}

while (( $# )); do
  case "$1" in
    --target)
      [[ $# -ge 2 && -n "${2:-}" && "${2:-}" != -* ]] || {
        usage
        exit 2
      }
      target=$2
      shift 2
      ;;
    --rollback)
      mode=rollback
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

[[ "$target" != -* && "$target" != *[[:space:]]* ]] || {
  echo "refusing unsafe frontend deployment target: $target" >&2
  exit 2
}

local_stage="$(mktemp -d "${TMPDIR:-/tmp}/van-dashboard-frontend.XXXXXX")"
local_mux_dir="$(mktemp -d /tmp/van-dashboard-frontend-ssh.XXXXXX)"
remote_stage=""
ssh_options=(
  -o ControlMaster=auto
  -o "ControlPath=$local_mux_dir/socket"
  -o ControlPersist=120
)

cleanup() {
  if [[ "$remote_stage" =~ ^/tmp/van-dashboard-frontend\.[[:alnum:]]+$ ]]; then
    ssh "${ssh_options[@]}" "$target" \
      "rm -rf -- '$remote_stage'" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$local_stage" "$local_mux_dir"
}
trap cleanup EXIT

ssh "${ssh_options[@]}" "$target" true

if [[ "$mode" == rollback ]]; then
  ssh "${ssh_options[@]}" "$target" bash -s <<'ROLLBACK'
set -eu

live_root=/home/pi/scripts/van-dashboard-frontend
current_link=$live_root/current
previous_link=$live_root/previous

current_target=$(/usr/bin/readlink "$current_link") || {
  echo "frontend current release is not installed" >&2
  exit 1
}
previous_target=$(/usr/bin/readlink "$previous_link") || {
  echo "frontend previous release is not available" >&2
  exit 1
}

case "$current_target" in
  releases/*) ;;
  *) echo "refusing unexpected current frontend target: $current_target" >&2; exit 1 ;;
esac
case "$previous_target" in
  releases/*) ;;
  *) echo "refusing unexpected previous frontend target: $previous_target" >&2; exit 1 ;;
esac
[[ -d "$live_root/$current_target" ]] || {
  echo "current frontend release is missing: $current_target" >&2
  exit 1
}
[[ -d "$live_root/$previous_target" ]] || {
  echo "previous frontend release is missing: $previous_target" >&2
  exit 1
}
[[ -r "$live_root/$previous_target/index.html" ]] || {
  echo "previous frontend release lacks index.html: $previous_target" >&2
  exit 1
}

replace_link() {
  link_target=$1
  link_path=$2
  temporary_link="${link_path}.new.$$"
  /usr/bin/ln -s "$link_target" "$temporary_link"
  /usr/bin/mv -Tf "$temporary_link" "$link_path"
}

# Each replacement is atomic.  Flask resolves the current link on every
# request, so :8788 serves the restored release without a restart.
replace_link "$previous_target" "$current_link"
replace_link "$current_target" "$previous_link"
ROLLBACK
else
  for required in \
    "$frontend_dir/package.json" \
    "$frontend_dir/package-lock.json"; do
    [[ -r "$required" ]] || {
      echo "required frontend deployment file is missing: $required" >&2
      exit 1
    }
  done

  command -v npm >/dev/null 2>&1 || {
    echo "npm is required to build the dashboard frontend on this Mac" >&2
    exit 1
  }

  (
    cd "$frontend_dir"
    # Set VAN_DASHBOARD_FRONTEND_SKIP_NPM_CI=1 for quick repeat deployments
    # only when node_modules is already known to match package-lock.json.
    if [[ "${VAN_DASHBOARD_FRONTEND_SKIP_NPM_CI:-0}" != 1 ]]; then
      npm ci
    fi
    npm run build
  )

  dist_dir="$frontend_dir/dist"
  [[ -r "$dist_dir/index.html" ]] || {
    echo "frontend build did not produce dist/index.html" >&2
    exit 1
  }

  git_revision="$(git -C "$repo_root" rev-parse --short=12 HEAD)"
  dirty_suffix=""
  if [[ -n "$(git -C "$repo_root" status --porcelain -- \
      pi/apps/van_dashboard/frontend \
      pi/deploy_van_dashboard_frontend.sh)" ]]; then
    dirty_suffix="-dirty"
  fi
  release_id="$(date -u +%Y%m%dT%H%M%SZ)-${git_revision}${dirty_suffix}-$$"
  [[ "$release_id" =~ ^[[:alnum:]._-]+$ ]] || {
    echo "generated unsafe frontend release identifier: $release_id" >&2
    exit 1
  }

  mkdir -p "$local_stage/release"
  cp -a "$dist_dir/." "$local_stage/release/"
  printf '%s\n' "$release_id" > "$local_stage/release/BUILD_ID"

  remote_stage="$(
    ssh "${ssh_options[@]}" "$target" \
      'mktemp -d /tmp/van-dashboard-frontend.XXXXXX'
  )"
  [[ "$remote_stage" =~ ^/tmp/van-dashboard-frontend\.[[:alnum:]]+$ ]] || {
    echo "unexpected remote frontend staging path: $remote_stage" >&2
    exit 1
  }

  scp "${ssh_options[@]}" -r \
    "$local_stage/release" \
    "$target:$remote_stage/"

  ssh "${ssh_options[@]}" "$target" bash -s -- \
    "$remote_stage" "$release_id" <<'INSTALL'
set -eu

stage=$1
release_id=$2
case "$stage" in
  /tmp/van-dashboard-frontend.[A-Za-z0-9]*) ;;
  *) echo "refusing unsafe frontend staging path: $stage" >&2; exit 1 ;;
esac
case "$release_id" in
  *[!A-Za-z0-9._-]* | "")
    echo "refusing unsafe frontend release identifier: $release_id" >&2
    exit 1
    ;;
esac

live_root=/home/pi/scripts/van-dashboard-frontend
releases=$live_root/releases
incoming=$releases/.incoming-$release_id
release=$releases/$release_id

cleanup() {
  rm -rf -- "$stage"
  if [[ -d "$incoming" ]]; then
    rm -rf -- "$incoming"
  fi
}
trap cleanup EXIT

[[ -r "$stage/release/index.html" ]] || {
  echo "staged frontend release lacks index.html" >&2
  exit 1
}
[[ ! -e "$release" && ! -e "$incoming" ]] || {
  echo "frontend release already exists: $release_id" >&2
  exit 1
}

/usr/bin/install -d -m 0755 "$releases" "$incoming"
/bin/cp -a "$stage/release/." "$incoming/"
/usr/bin/find "$incoming" -type d -exec /bin/chmod 0755 {} +
/usr/bin/find "$incoming" -type f -exec /bin/chmod 0644 {} +
/usr/bin/mv "$incoming" "$release"

replace_link() {
  link_target=$1
  link_path=$2
  temporary_link="${link_path}.new.$$"
  /usr/bin/ln -s "$link_target" "$temporary_link"
  /usr/bin/mv -Tf "$temporary_link" "$link_path"
}

if [[ -e "$live_root/current" && ! -L "$live_root/current" ]]; then
  echo "refusing to replace non-symlink frontend current path" >&2
  exit 1
fi
if current_target=$(/usr/bin/readlink "$live_root/current" 2>/dev/null); then
  case "$current_target" in
    releases/*)
      [[ -d "$live_root/$current_target" ]] || {
        echo "current frontend release is missing: $current_target" >&2
        exit 1
      }
      replace_link "$current_target" "$live_root/previous"
      ;;
    *)
      echo "refusing unexpected current frontend target: $current_target" >&2
      exit 1
      ;;
  esac
fi

replace_link "releases/$release_id" "$live_root/current"
INSTALL
fi

ssh "${ssh_options[@]}" "$target" bash -s <<'HEALTH'
set -eu
/usr/bin/systemctl --no-pager --full is-active van-dashboard.service

# The dashboard must serve the release that current now names, byte for byte,
# and its API must answer.
current_index=/home/pi/scripts/van-dashboard-frontend/current/index.html
attempt=1
while [[ "$attempt" -le 30 ]]; do
  index_ok=false
  backend_ok=false
  if /usr/bin/curl -fsS --connect-timeout 1 --max-time 2 \
      http://127.0.0.1:8788/ | /usr/bin/cmp -s - "$current_index"; then
    index_ok=true
  fi
  if /usr/bin/curl -fsS --connect-timeout 1 --max-time 5 \
      http://127.0.0.1:8788/api/status >/dev/null; then
    backend_ok=true
  fi
  if [[ "$index_ok" == true && "$backend_ok" == true ]]; then
    exit 0
  fi
  if [[ "$attempt" -eq 30 ]]; then
    echo "dashboard did not serve the current release after 30 attempts" >&2
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep 1
done
HEALTH

echo "Dashboard frontend $mode completed on $target"
