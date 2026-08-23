#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
monitor="$script_dir/../usr/libexec/clientwan-path-monitor"
init_script="$script_dir/../etc/init.d/clientwan-path-monitor"
sysupgrade_conf="$script_dir/../etc/sysupgrade.conf"
backup_exporter="$script_dir/../usr/libexec/openwrt-backup-export"
pi_backup="$script_dir/../../pi/scripts/backup/openwrt_backup.sh"
test_root=$(mktemp -d "${TMPDIR:-/tmp}/clientwan-path-test.XXXXXX")
mock_bin="$test_root/bin"
mock_state="$test_root/state"
log_file="$test_root/log"
mkdir -p "$mock_bin" "$mock_state"

cleanup() {
	rm -rf "$test_root"
}
trap cleanup EXIT HUP INT TERM

mock_command="$mock_bin/mock-command"
cat > "$mock_command" <<'MOCK'
#!/bin/sh
set -eu

tool=${0##*/}
state=${MOCK_STATE:?}

case $tool in
	ubus)
		[ "$1" = call ]
		if [ -f "$state/interface-down" ]; then
			printf '{"up":false}\n'
		else
			printf '{"up":true,"l3_device":"wl1-sta0"}\n'
		fi
		;;
	jsonfilter)
		expression=
		while [ "$#" -gt 0 ]; do
			case $1 in
				-e) expression=$2; shift 2 ;;
				*) shift ;;
			esac
		done
		cat >/dev/null
		case $expression in
			'@.up')
				[ ! -f "$state/interface-down" ] && printf 'true\n' \
					|| printf 'false\n'
				;;
			'@.l3_device')
				[ ! -f "$state/interface-down" ] && printf 'wl1-sta0\n'
				;;
			*) exit 1 ;;
		esac
		;;
	ip)
		printf 'default via 172.20.10.1 dev wl1-sta0\n'
		;;
	ping)
		target=
		for argument in "$@"; do target=$argument; done
		case $target in
			172.20.10.1) [ ! -f "$state/gateway-down" ] ;;
			1.1.1.1) [ ! -f "$state/public1-down" ] ;;
			208.67.222.222) [ ! -f "$state/public2-down" ] ;;
			*) exit 1 ;;
		esac
		;;
	logger)
		for argument in "$@"; do message=$argument; done
		printf '%s\n' "$message" >> "${MOCK_LOG:?}"
		;;
	sleep)
		:
		;;
	*) exit 1 ;;
esac
MOCK
chmod 755 "$mock_command"

for tool_name in ubus jsonfilter ip ping logger sleep; do
	ln -s mock-command "$mock_bin/$tool_name"
done

export MOCK_STATE="$mock_state"
export MOCK_LOG="$log_file"
export UBUS="$mock_bin/ubus"
export JSONFILTER="$mock_bin/jsonfilter"
export IP="$mock_bin/ip"
export PING="$mock_bin/ping"
export LOGGER="$mock_bin/logger"
export SLEEP="$mock_bin/sleep"
export AWK=/usr/bin/awk
export MAX_SAMPLES=1
export REPORT_SAMPLES=1

: > "$mock_state/public1-down"
: > "$mock_state/public2-down"
"$monitor"
grep -F 'state-change state=gateway-1-public-0' "$log_file" >/dev/null
grep -F 'summary samples=1 interface_up=1 gateway_ok=1 public1_ok=0 public2_ok=0' \
	"$log_file" >/dev/null

: > "$log_file"
: > "$mock_state/interface-down"
"$monitor"
grep -F 'state-change state=interface-down' "$log_file" >/dev/null
grep -F 'summary samples=1 interface_up=0 gateway_ok=0 public1_ok=0 public2_ok=0' \
	"$log_file" >/dev/null

/bin/sh -n "$monitor"
/bin/sh -n "$init_script"
for file in "$sysupgrade_conf" "$backup_exporter" "$pi_backup"; do
	grep -F 'rc.d/S20clientwan-path-monitor' "$file" >/dev/null
done
printf 'clientwan-path-monitor: ok\n'
