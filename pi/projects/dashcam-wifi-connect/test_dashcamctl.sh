#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
control="$repo_root/pi/projects/dashcam-wifi-connect/dashcamctl"
state="$test_root/state"
mkdir -p "$state"
printf '0\n' > "$state/recording"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

cat > "$test_root/nmcli" <<'EOF'
#!/bin/bash
state=${DASHCAM_TEST_STATE:?}
if [[ ${1:-} == -g ]]; then
  property=$2
  case $property in
    GENERAL.CONNECTION)
      [[ -e $state/connected ]] && printf 'dashcam\n' || printf -- '--\n'
      ;;
    connection.id) printf 'dashcam\n' ;;
    connection.autoconnect) printf 'no\n' ;;
    ipv4.never-default)
      [[ -e $state/unsafe ]] && printf 'no\n' || printf 'yes\n'
      ;;
    ipv6.method) printf 'disabled\n' ;;
    *) exit 2 ;;
  esac
  exit 0
fi
if [[ ${1:-} == --wait && ${3:-} == connection && ${4:-} == up ]]; then
  touch "$state/connected"
  exit 0
fi
if [[ ${1:-} == connection && ${2:-} == down ]]; then
  rm -f "$state/connected"
  exit 0
fi
exit 2
EOF

cat > "$test_root/ip" <<'EOF'
#!/bin/bash
printf '192.168.10.1 dev wlan0 src 192.168.10.121\n'
EOF

cat > "$test_root/curl" <<'EOF'
#!/bin/bash
state=${DASHCAM_TEST_STATE:?}
args=$*
if [[ $args == *'cmd=2005'* ]]; then
  printf '{\n  "RecodStatus" : %s\n}\n' "$(cat "$state/recording")"
elif [[ $args == *'cmd=1100&par=1'* ]]; then
  printf '1\n' > "$state/recording"
  printf '{"Cmd":1100,"Value":0}\n'
elif [[ $args == *'cmd=1100&par=0'* ]]; then
  printf '0\n' > "$state/recording"
  printf '{"Cmd":1100,"Value":0}\n'
else
  exit 2
fi
EOF

cat > "$test_root/systemctl" <<'EOF'
#!/bin/bash
[[ ${1:-} == is-active ]] && exit 1
exit 0
EOF

cat > "$test_root/sudo" <<'EOF'
#!/bin/bash
[[ ${1:-} == -n ]] && shift
exec "$@"
EOF

cat > "$test_root/sleep" <<'EOF'
#!/bin/bash
exit 0
EOF

chmod +x "$test_root"/{nmcli,ip,curl,systemctl,sudo,sleep}

run_control() {
  DASHCAM_TEST_STATE="$state" \
    DASHCAMCTL_NMCLI="$test_root/nmcli" \
    DASHCAMCTL_IP="$test_root/ip" \
    DASHCAMCTL_CURL="$test_root/curl" \
    DASHCAMCTL_SYSTEMCTL="$test_root/systemctl" \
    DASHCAMCTL_SUDO="$test_root/sudo" \
    DASHCAMCTL_SLEEP="$test_root/sleep" \
    bash "$control" "$@"
}

output=$(run_control connect) || fail "connect failed"
[[ $output == *'connection=active'* ]] || fail "connect omitted active state"
[[ $output == *'recording=on'* ]] || fail "connect did not ensure recording"
[[ -e $state/connected ]] || fail "connect did not activate the profile"
[[ $(cat "$state/recording") == 1 ]] || fail "connect did not start recording"

output=$(run_control status) || fail "status failed while connected"
[[ $output == *'camera=reachable'* ]] || fail "status did not reach camera"
[[ $output == *'stream=rtsp://192.168.10.1:8554/ch01'* ]] ||
  fail "status returned the wrong stream URL"

run_control record-stop >/dev/null || fail "record-stop failed"
[[ $(cat "$state/recording") == 0 ]] || fail "record-stop was not sent"
run_control disconnect >/dev/null || fail "disconnect failed"
[[ ! -e $state/connected ]] || fail "disconnect left the profile active"
[[ $(cat "$state/recording") == 0 ]] || fail "disconnect changed recording state"

touch "$state/unsafe"
run_control connect >/dev/null 2>&1 && fail "unsafe default-route profile was accepted"
[[ ! -e $state/connected ]] || fail "unsafe profile was activated"

echo "PASS: dashcam connection and recording controls"
