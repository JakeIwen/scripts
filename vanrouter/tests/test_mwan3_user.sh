#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd -- "$(dirname "$0")" && pwd)
hook="$script_dir/../etc/mwan3.user"
temp_dir=${TMPDIR:-/tmp}
test_root=$(mktemp -d "${temp_dir%/}/mwan3-user-test.XXXXXX")
mock_bin="$test_root/bin"
root="$test_root/root"
conntrack="$root/proc/net/nf_conntrack"
count_file="$root/proc/sys/net/netfilter/nf_conntrack_count"
state_file="$root/var/run/mwan3/iface_state/clientwan"
mkdir -p "$mock_bin" "${conntrack%/*}" "${count_file%/*}" "${state_file%/*}"

cleanup() {
	rm -rf "$test_root"
}
trap cleanup EXIT HUP INT TERM

cat > "$mock_bin/mock-command" <<'MOCK'
#!/bin/sh
set -eu
fixture=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)
tool=${0##*/}
case $tool in
	ubus)
		[ "$*" = 'call network.interface.clientwan status' ]
		printf 'ubus\n' >> "$fixture/calls"
		[ ! -f "$fixture/ubus-fail" ] || exit 1
		printf '{"ipv4-address":[{"address":"172.20.10.2"}]}\n'
		;;
	jsonfilter)
		[ "$#" -eq 2 ] && [ "$1" = -e ]
		[ "$2" = '@["ipv4-address"][0].address' ]
		/bin/cat > "$fixture/jsonfilter-input"
		printf 'jsonfilter\n' >> "$fixture/calls"
		[ ! -f "$fixture/jsonfilter-fail" ] || exit 1
		/bin/cat "$fixture/address"
		;;
	ip)
		[ "$*" = '-4 -o addr show' ]
		printf 'ip\n' >> "$fixture/calls"
		[ ! -f "$fixture/ip-fail" ] || exit 1
		/bin/cat "$fixture/addresses"
		;;
	cat)
		if [ "$#" -eq 1 ] && [ "$1" = "$fixture/root/proc/sys/net/netfilter/nf_conntrack_count" ]; then
			printf 'count\n' >> "$fixture/calls"
			/bin/cat "$1" || exit 1
			# Simulate a changed table size between the two observations.
			if [ -f "$fixture/next-count" ]; then
				mv "$fixture/next-count" "$1"
			fi
		else
			/bin/cat "$@"
		fi
		;;
	logger)
		[ "$#" -eq 3 ] && [ "$1" = -t ] && [ "$2" = mwan3-conntrack ]
		printf '%s\n' "$3" >> "$fixture/log"
		;;
	*) exit 1 ;;
esac
MOCK
chmod 755 "$mock_bin/mock-command"
for tool in ubus jsonfilter ip logger cat; do
	ln -s mock-command "$mock_bin/$tool"
done

reset_case() {
	printf 'offline\n' > "$state_file"
	printf '172.20.10.2\n' > "$test_root/address"
	printf '42\n' > "$count_file"
	printf 'untouched\n' > "$test_root/untouched"
	cp "$test_root/untouched" "$conntrack"
	cat > "$test_root/addresses" <<'ADDRESSES'
1: br-lan    inet 192.168.6.1/24 brd 192.168.6.255 scope global br-lan
2: wl1-sta0    inet 172.20.10.2/28 brd 172.20.10.15 scope global wl1-sta0
3: eth1    inet 172.20.10.20/24 brd 172.20.10.255 scope global eth1
ADDRESSES
	: > "$test_root/log"
	: > "$test_root/calls"
	rm -f "$test_root/ubus-fail" "$test_root/jsonfilter-fail" \
		"$test_root/ip-fail" "$test_root/next-count"
}

run_hook() {
	hook_status=0
	env -i PATH="$mock_bin:/usr/bin:/bin:/usr/sbin:/sbin" \
		MWAN3_USER_ROOT="$root" ACTION="$1" INTERFACE="$2" DEVICE=wl1-sta0 \
		"$hook" > "$test_root/stdout" 2> "$test_root/stderr" || hook_status=$?
	[ ! -s "$test_root/stdout" ]
	[ ! -s "$test_root/stderr" ]
}

expect_result() {
	if [ "$hook_status" -ne "$1" ]; then
		printf 'Expected status %s, got %s\n' "$1" "$hook_status" >&2
		exit 1
	fi
	: > "$test_root/expected-log"
	if [ -n "$2" ]; then
		printf '%s\n' "$2" > "$test_root/expected-log"
	fi
	cmp "$test_root/expected-log" "$test_root/log"
}

expect_untouched() {
	cmp "$test_root/untouched" "$conntrack"
}

for action in ifdown ifup connected online '' disconnectedx; do
	reset_case
	run_hook "$action" '../invalid'
	expect_result 0 ''
	expect_untouched
	[ ! -s "$test_root/calls" ]
done

for state in online '' unknown missing; do
	reset_case
	if [ "$state" = missing ]; then
		rm "$state_file"
	else
		printf '%s\n' "$state" > "$state_file"
	fi
	run_hook disconnected clientwan
	expect_result 0 'skip interface=clientwan reason=not-offline'
	expect_untouched
	[ ! -s "$test_root/calls" ]
done

# An empty address is a safe skip, not an empty write to the flush API.
reset_case
: > "$test_root/address"
run_hook disconnected clientwan
expect_result 0 'skip interface=clientwan reason=no-ipv4'
expect_untouched

for address in '1.2.3' 'fe80::1' f '1.2.3.4; f' '1.2.3.4444' \
	'1.2.3.4.5' '.1.2.3' ' 1.2.3.4' '1.2.3.4
f' '1.2.3.4
5.6.7.8'; do
	reset_case
	printf '%s\n' "$address" > "$test_root/address"
	run_hook disconnected clientwan
	expect_result 1 'error interface=clientwan reason=invalid-ipv4'
	expect_untouched
done

reset_case
printf '4: eth2    inet 172.20.10.2/24 scope global eth2\n' >> "$test_root/addresses"
run_hook disconnected clientwan
expect_result 0 'skip interface=clientwan address=172.20.10.2 reason=shared-ipv4'
expect_untouched

reset_case
printf '37\n' > "$test_root/next-count"
run_hook disconnected clientwan
expect_result 0 'cleanup interface=clientwan address=172.20.10.2 before=42 after=37'
# No trailing newline: on the router a lone "\n" write is a global flush.
printf '172.20.10.2' > "$test_root/expected-conntrack"
cmp "$test_root/expected-conntrack" "$conntrack"
printf 'ubus\njsonfilter\nip\ncount\ncount\n' > "$test_root/expected-calls"
cmp "$test_root/expected-calls" "$test_root/calls"

for interface in '' '../clientwan' 'clientwan/other' 'clientwan-bad' 'clientwan; f'; do
	reset_case
	run_hook disconnected "$interface"
	expect_result 1 'error invalid-interface'
	expect_untouched
	[ ! -s "$test_root/calls" ]
done

# OpenWrt's jsonfilter exits 1 without output when the address list is empty.
reset_case
: > "$test_root/address"
: > "$test_root/jsonfilter-fail"
run_hook disconnected clientwan
expect_result 0 'skip interface=clientwan reason=no-ipv4'
expect_untouched

for tool in ubus ip; do
	reset_case
	: > "$test_root/$tool-fail"
	run_hook disconnected clientwan
	expect_result 1 "error interface=clientwan reason=$tool-failed"
	expect_untouched
done

reset_case
rm "$count_file"
run_hook disconnected clientwan
expect_result 1 'error interface=clientwan reason=count-before-failed'
expect_untouched

# A directory rejects writes even when the test runs as root.
reset_case
rm "$conntrack"
mkdir "$conntrack"
run_hook disconnected clientwan
expect_result 1 'error interface=clientwan address=172.20.10.2 reason=write-failed'
[ -d "$conntrack" ]

/bin/sh -n "$hook"
printf 'mwan3.user: ok\n'
