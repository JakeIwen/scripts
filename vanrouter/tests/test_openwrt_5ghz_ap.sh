#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
helper="$script_dir/../usr/libexec/openwrt-5ghz-ap"
test_root=$(mktemp -d "${TMPDIR:-/tmp}/openwrt-5ghz-ap-test.XXXXXX")
mock_bin="$test_root/bin"
mock_state="$test_root/state"
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

target_get() {
	option=$1
	file="$state/target.$option"
	[ -f "$file" ] || exit 1
	sed -n '1p' "$file"
}

target_set() {
	option=$1
	value=$2
	printf '%s\n' "$value" > "$state/target.$option"
}

radio1_htmode() {
	if [ -f "$state/radio1.htmode" ]; then
		sed -n '1p' "$state/radio1.htmode"
	else
		printf 'HT20\n'
	fi
}

uci_get() {
	path=$1
	case $path in
		wireless.radio0.band) printf '2g\n' ;;
		wireless.radio1.band) printf '5g\n' ;;
		wireless.radio1.htmode) radio1_htmode ;;
		wireless.wifinet3) printf 'wifi-iface\n' ;;
		wireless.wifinet3.mode) printf 'ap\n' ;;
		wireless.wifinet3.device) printf 'radio0\n' ;;
		wireless.wifinet3.network) printf 'lan\n' ;;
		wireless.wifinet3.ssid) printf 'dendelion\n' ;;
		wireless.wifinet3.encryption) printf 'sae-mixed\n' ;;
		wireless.wifinet3.key) printf 'test-secret-value\n' ;;
		wireless.wifinet4) printf 'wifi-iface\n' ;;
		wireless.wifinet4.mode) printf 'sta\n' ;;
		wireless.wifinet4.device) printf 'radio1\n' ;;
		wireless.wifinet4.network) printf 'clientwan\n' ;;
		wireless.dendelion_5g) target_get type ;;
		wireless.dendelion_5g.*)
			target_get "${path#wireless.dendelion_5g.}"
			;;
		*) exit 1 ;;
	esac
}

case $tool in
	uci)
		while [ "${1:-}" = -q ]; do shift; done
		command_name=${1:-}
		[ "$#" -gt 0 ] && shift
		case $command_name in
			get)
				uci_get "$1"
				;;
			show)
				value=$(uci_get "$1")
				printf "%s='%s'\n" "$1" "$value"
				;;
			set)
				assignment=$1
				path=${assignment%%=*}
				value=${assignment#*=}
				case $path in
					wireless.dendelion_5g)
						target_set type "$value"
						;;
					wireless.dendelion_5g.*)
						target_set "${path#wireless.dendelion_5g.}" "$value"
						;;
					wireless.radio1.htmode)
						printf '%s\n' "$value" > "$state/radio1.htmode"
						;;
					*) exit 1 ;;
				esac
				;;
			batch)
				while IFS= read -r line; do
					assignment=${line#set }
					path=${assignment%%=*}
					value=${assignment#*=}
					case $value in
						\'*\')
							value=${value#\'}
							value=${value%\'}
							;;
					esac
					target_set "${path#wireless.dendelion_5g.}" "$value"
				done
				;;
			delete)
				[ "$1" = wireless.dendelion_5g ]
				rm -f "$state"/target.*
				;;
			commit)
				[ "$1" = wireless ]
				;;
			*) exit 1 ;;
		esac
		;;
	ubus)
		[ "$1" = call ]
		case $2 in
			system) printf 'BOARD\n' ;;
			network.wireless) printf 'WIRELESS\n' ;;
			*) exit 1 ;;
		esac
		;;
	jsonfilter)
		expression=
		while [ "$#" -gt 0 ]; do
			case $1 in
				-e)
					expression=$2
					shift 2
					;;
				*) shift ;;
			esac
		done
		cat >/dev/null
		case $expression in
			'@.board_name') printf 'linksys,e8450-ubi\n' ;;
			'@.radio1.up') printf 'true\n' ;;
			'@.radio1.interfaces[*].section')
				printf 'wifinet4\n'
				if [ -f "$state/target.type" ] \
					&& [ ! -f "$state/prevent-ap-start" ]; then
					printf 'dendelion_5g\n'
				fi
				;;
			'@.radio1.interfaces[*].ifname')
				printf 'wl1-sta0\n'
				if [ -f "$state/target.type" ] \
					&& [ ! -f "$state/prevent-ap-start" ]; then
					printf 'wl1-ap0\n'
				fi
				;;
			*) exit 1 ;;
		esac
		;;
	iw)
		case "$1 $2" in
			"phy wl1")
				printf '%s\n' \
					'valid interface combinations:' \
					' * #{ AP, mesh point } <= 16, #{ managed } <= 19' \
					'HE Iftypes: AP'
				;;
			"dev wl1-sta0")
				printf 'Interface wl1-sta0\n\ttype managed\n'
				;;
			"dev wl1-ap0")
				printf 'Interface wl1-ap0\n\ttype AP\n'
				;;
			*) exit 1 ;;
		esac
		;;
	wifi)
		printf '%s %s\n' "$1" "$2" >> "$state/wifi-calls"
		if [ -f "$state/fail-next-reload" ]; then
			rm -f "$state/fail-next-reload"
			: > "$state/prevent-ap-start"
		fi
		;;
	sleep)
		:
		;;
	logger)
		:
		;;
	*) exit 1 ;;
esac
MOCK
chmod 755 "$mock_command"

for tool_name in uci ubus jsonfilter iw wifi sleep logger; do
	ln -s mock-command "$mock_bin/$tool_name"
done

export MOCK_STATE="$mock_state"
export UCI="$mock_bin/uci"
export UBUS="$mock_bin/ubus"
export JSONFILTER="$mock_bin/jsonfilter"
export IW="$mock_bin/iw"
export WIFI="$mock_bin/wifi"
export SLEEP="$mock_bin/sleep"
export LOGGER="$mock_bin/logger"
export GREP=/usr/bin/grep
export SED=/usr/bin/sed

preflight_output="$test_root/preflight.out"
apply_output="$test_root/apply.out"
status_output="$test_root/status.out"
remove_output="$test_root/remove.out"
failure_output="$test_root/failure.out"

"$helper" preflight > "$preflight_output" 2>&1
grep -F 'Preflight passed' "$preflight_output" >/dev/null
! grep -F 'test-secret-value' "$preflight_output" >/dev/null

"$helper" apply > "$apply_output" 2>&1
[ "$(sed -n '1p' "$mock_state/target.type")" = wifi-iface ]
[ "$(sed -n '1p' "$mock_state/target.device")" = radio1 ]
[ "$(sed -n '1p' "$mock_state/target.mode")" = ap ]
[ "$(sed -n '1p' "$mock_state/target.network")" = lan ]
[ "$(sed -n '1p' "$mock_state/target.ssid")" = dendelion ]
[ "$(sed -n '1p' "$mock_state/target.encryption")" = sae-mixed ]
[ "$(sed -n '1p' "$mock_state/target.key")" = test-secret-value ]
grep -F 'Enabled dendelion_5g' "$apply_output" >/dev/null
! grep -F 'test-secret-value' "$apply_output" >/dev/null

"$helper" status > "$status_output" 2>&1
grep -F 'are operational' "$status_output" >/dev/null

"$helper" optimize > "$test_root/optimize.out" 2>&1
[ "$(sed -n '1p' "$mock_state/radio1.htmode")" = HE80 ]
grep -F 'Set radio1 to HE80' "$test_root/optimize.out" >/dev/null

printf 'HT20\n' > "$mock_state/radio1.htmode"
: > "$mock_state/fail-next-reload"
if "$helper" optimize > "$test_root/optimize-failure.out" 2>&1; then
	printf 'expected failed HE80 canary to restore HT20\n' >&2
	exit 1
fi
[ "$(sed -n '1p' "$mock_state/radio1.htmode")" = HT20 ]
grep -F 'restored radio1 htmode to HT20' \
	"$test_root/optimize-failure.out" >/dev/null
rm -f "$mock_state/prevent-ap-start"

"$helper" remove > "$remove_output" 2>&1
[ ! -e "$mock_state/target.type" ]
grep -F 'Removed dendelion_5g' "$remove_output" >/dev/null

: > "$mock_state/prevent-ap-start"
if "$helper" apply > "$failure_output" 2>&1; then
	printf 'expected failed AP canary to roll back\n' >&2
	exit 1
fi
[ ! -e "$mock_state/target.type" ]
grep -F 'Deployment failed; removed dendelion_5g' "$failure_output" >/dev/null
! grep -F 'test-secret-value' "$failure_output" >/dev/null

printf 'openwrt-5ghz-ap: ok\n'
