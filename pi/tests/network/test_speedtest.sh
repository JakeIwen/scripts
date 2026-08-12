#!/bin/bash

set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../../.." && pwd)
SPEEDTEST_SCRIPT="$REPO_ROOT/pi/scripts/speedtest.sh"
TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/speedtest-test.XXXXXX")

cleanup() {
  case $TEST_ROOT in
    "${TMPDIR:-/tmp}"/speedtest-test.*) find "$TEST_ROOT" -depth -delete ;;
    *) printf 'refusing unexpected test cleanup path: %s\n' "$TEST_ROOT" >&2 ;;
  esac
}
trap cleanup EXIT

fail() {
  printf 'speedtest test failed: %s\n' "$*" >&2
  exit 1
}

mkdir -p "$TEST_ROOT/bin"

cat > "$TEST_ROOT/bin/ping" <<'EOF'
#!/bin/sh
cat <<'PING'
PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.
64 bytes from 8.8.8.8: icmp_seq=1 ttl=118 time=42.1 ms

--- 8.8.8.8 ping statistics ---
1 packets transmitted, 1 received, 0% packet loss, time 0ms
rtt min/avg/max/mdev = 42.100/42.100/42.100/0.000 ms
PING
EOF
chmod +x "$TEST_ROOT/bin/ping"

cat > "$TEST_ROOT/bin/speedtest-cli" <<'EOF'
#!/bin/sh
cat <<'OUTPUT'
Ping: 1800000.0 ms
Download: 11.76 Mbit/s
Upload: 10.62 Mbit/s
OUTPUT
EOF
chmod +x "$TEST_ROOT/bin/speedtest-cli"

output=$(PATH="$TEST_ROOT/bin:/usr/bin:/bin" bash "$SPEEDTEST_SCRIPT") || fail "sentinel fallback failed"
printf '%s\n' "$output" | grep -q '^Latency: *42.100 ms$' || fail "did not replace speedtest-cli sentinel with route ping"

cat > "$TEST_ROOT/bin/speedtest-cli" <<'EOF'
#!/bin/sh
cat <<'OUTPUT'
Ping: 83.25 ms
Download: 12.00 Mbit/s
Upload: 7.00 Mbit/s
OUTPUT
EOF
chmod +x "$TEST_ROOT/bin/speedtest-cli"

output=$(PATH="$TEST_ROOT/bin:/usr/bin:/bin" bash "$SPEEDTEST_SCRIPT") || fail "normal latency failed"
printf '%s\n' "$output" | grep -q '^Latency: *83.25 ms$' || fail "overrode a valid speedtest-cli latency"

printf 'speedtest: ok\n'
