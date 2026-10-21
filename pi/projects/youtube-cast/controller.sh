#!/bin/bash
# Pi-side lifecycle controller. Run as pi; only unit operations need sudo.
set -euo pipefail
umask 077
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ $(id -un) == pi ]] || { echo 'Run this controller as pi, not root.' >&2; exit 1; }
config_value() {
  /usr/bin/node --input-type=module -e \
    'const {config}=await import(process.argv[1]); console.log(config[process.argv[2]])' \
    "$here/src/config.mjs" "$1"
}
app=$(config_value installDirectory)
state=$(config_value stateDirectory)
port=$(config_value statusPort)
dial_port=$(config_value dialPort)
unit=youtube-cast.service
live_unit=/etc/systemd/system/$unit

assert_unit() {
  [[ -f "$live_unit" && ! -L "$live_unit" ]] || { echo 'Expected a regular installed unit.' >&2; return 1; }
  [[ $(stat -c '%U:%G:%a' "$live_unit") == root:root:644 ]] || return 1
  grep -Fxq "ExecStart=/usr/bin/node $app/current/src/index.mjs" "$live_unit" || return 1
  [[ $(systemctl show "$unit" -p FragmentPath --value) == "$live_unit" ]] || return 1
  [[ -z $(systemctl show "$unit" -p DropInPaths --value) ]] || { echo 'Unreviewed unit drop-ins; refusing.' >&2; return 1; }
}

status() {
  assert_unit || return 1
  cmp -s "$app/current/$unit" "$live_unit" || return 1
  systemctl is-enabled --quiet "$unit" || return 1
  systemctl is-active --quiet "$unit" || return 1
  local pid
  pid=$(systemctl show "$unit" -p MainPID --value)
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  curl -fsS --max-time 5 "http://127.0.0.1:$port/health" | /usr/bin/node -e '
    let body="";process.stdin.on("data",x=>body+=x);process.stdin.on("end",()=>{
      const h=JSON.parse(body);if(!h.ok||h.name!=="Van TV"||h.pid!==Number(process.argv[1])||!h.instance)process.exit(1);
      console.log(`Van TV healthy: PID ${h.pid}, receiver ${h.receiver}, player ${h.player}`);
    });' "$pid" || return 1
  ss -ltnp "sport = :$dial_port" | grep -q "pid=$pid,"
}

wait_healthy() {
  local attempt
  for attempt in $(seq 1 45); do
    if status >/dev/null 2>&1; then status; return; fi
    sleep 2
  done
  systemctl --no-pager status "$unit" >&2 || true
  echo "Van TV did not become healthy; inspect journalctl -u $unit" >&2
  return 1
}

update_ytdlp() {
  /usr/bin/python3 -m venv "$app/venv"
  "$app/venv/bin/python" -m pip install --upgrade 'yt-dlp[default]' deno
  "$app/venv/bin/yt-dlp" --version
}

prepare() {
  /usr/bin/node -e 'if(Number(process.versions.node.split(".")[0])<20)process.exit(1)'
  local executable
  for executable in npm /usr/bin/mpv /usr/bin/xset /usr/bin/dbus-send curl /usr/bin/python3; do
    command -v "$executable" >/dev/null || { echo "Missing prerequisite: $executable" >&2; return 1; }
  done
  systemd-analyze verify "$here/$unit"
  local file
  for file in "$here"/src/*.mjs; do /usr/bin/node --check "$file"; done
}

install_release() {
  prepare
  if [[ -e "$live_unit" || -L "$live_unit" ]]; then
    assert_unit
  elif ss -ltn "sport = :$port or sport = :$dial_port" | grep -q LISTEN; then
    echo 'A foreign listener owns a receiver port; refusing install.' >&2; return 1
  fi
  install -d -m 0755 "$app" "$app/releases"
  install -d -m 0700 "$state"
  local release previous
  release=$(mktemp -d "$app/releases/release.XXXXXXXX")
  cp -R "$here/src" "$here/web" "$here/package.json" "$here/package-lock.json" \
    "$here/$unit" "$here/controller.sh" "$here/README.md" "$release/"
  (cd "$release" && npm ci --omit=dev)
  update_ytdlp
  previous=''
  [[ ! -e "$app/current" || -L "$app/current" ]] || { echo 'current is not a symlink' >&2; return 1; }
  if [[ -L "$app/current" ]]; then
    previous=$(readlink -e "$app/current")
    case "$previous" in "$app"/releases/*) ;; *) echo 'Foreign current release' >&2; return 1 ;; esac
  fi
  ln -s "$release" "$app/current.new"
  mv -Tf "$app/current.new" "$app/current"
  # install must not follow an old unit symlink into a user-writable checkout.
  sudo install -o root -g root -m 0644 "$release/$unit" "$live_unit"
  sudo systemctl daemon-reload
  sudo systemctl enable --now "$unit"
  sudo systemctl restart "$unit"
  if ! wait_healthy; then
    echo "Install failed. Retained previous release: ${previous:-none}" >&2
    echo 'No automatic rollback of the shared yt-dlp venv. Review logs before retrying.' >&2
    return 1
  fi
  if [[ -n "$previous" ]]; then
    ln -sfn "$previous" "$app/previous"
    echo "Retained previous release: $previous"
  fi
}

verb=${1:-status}
case "$verb" in
  install|start|stop|restart|verify|uninstall|update-ytdlp)
    install -d -m 0700 "$state"
    exec 9>"$state/controller.lock"
    /usr/bin/flock -n 9 || { echo 'Another controller operation is running.' >&2; exit 75; }
    ;;
esac
case "$verb" in
  prepare) prepare ;;
  install) install_release ;;
  status) status ;;
  update-ytdlp) update_ytdlp ;;
  start) assert_unit; sudo systemctl enable --now "$unit"; wait_healthy ;;
  stop) assert_unit; sudo systemctl disable --now "$unit" ;;
  restart) assert_unit; sudo systemctl restart "$unit"; wait_healthy ;;
  verify)
    status
    old=$(systemctl show "$unit" -p MainPID --value)
    sudo systemctl kill --kill-who=main --signal=SIGKILL "$unit"
    wait_healthy
    [[ $(systemctl show "$unit" -p MainPID --value) != "$old" ]]
    ;;
  uninstall)
    assert_unit
    sudo systemctl disable --now "$unit"
    sudo rm -- "$live_unit"
    sudo systemctl daemon-reload
    echo 'Registration removed; releases, venv and paired screen identity preserved.'
    ;;
  *) echo "Unknown action: $verb" >&2; exit 2 ;;
esac
