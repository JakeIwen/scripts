#!/bin/sh
set -eu

service=apsta-scan-gate
init=/etc/init.d/apsta-scan-gate
config_file=/etc/config/apsta-scan-gate
daemon=/usr/libexec/apsta-scan-gate.uc
module=/usr/share/ucode/apsta_scan_gate/policy.uc
temporary_path=
transaction_backup=
transaction_active=0

die() {
	printf 'apsta-scan-gate: %s\n' "$*" >&2
	exit 1
}

require_root() {
	[ "$(id -u)" -eq 0 ] || die 'router action requires root'
}

check_stage_path() {
	local stage=${1:-} suffix
	case $stage in /tmp/apsta-scan-gate.*) ;; *) die 'refusing unexpected staging path' ;; esac
	suffix=${stage#/tmp/apsta-scan-gate.}
	case $suffix in ''|*[!A-Za-z0-9]*) die 'refusing unexpected staging path' ;; esac
	[ -d "$stage" ] || die 'stage is missing'
	[ ! -L "$1" ] || die 'refusing symlinked staging directory'
}

cleanup_temporary() {
	[ -n "$temporary_path" ] && rm -f "$temporary_path"
	temporary_path=
}

abort_temporary() {
	trap - EXIT HUP INT TERM
	cleanup_temporary
	exit 130
}

arm_temporary_cleanup() {
	temporary_path=$1
	trap cleanup_temporary EXIT
	trap abort_temporary HUP INT TERM
}

disarm_temporary_cleanup() {
	cleanup_temporary
	trap - EXIT HUP INT TERM
}

wireless_station_count=0
count_station() {
	local section=$1 mode disabled device
	config_get mode "$section" mode
	config_get_bool disabled "$section" disabled 0
	config_get device "$section" device
	[ "$device" = radio1 ] && [ "$disabled" -eq 0 ] && [ "$mode" = sta ] &&
		wireless_station_count=$((wireless_station_count + 1))
	return 0
}

preflight() {
	require_root
	for command in ubus uci ucode jsonfilter sha256sum; do
		command -v "$command" >/dev/null 2>&1 || die "required command is missing: $command"
	done

	local board version device mode network disabled channel ap_device ap_mode ap_disabled
	board=$(ubus -S call system board | jsonfilter -e '@.board_name')
	version=$(ubus -S call system board | jsonfilter -e '@.release.version')
	[ "$board" = linksys,e8450-ubi ] || die 'board is not linksys,e8450-ubi'
	[ "$version" = 25.12.5 ] || die 'firmware is not OpenWrt 25.12.5'

	device=$(uci -q get wireless.wifinet4.device) || die 'wireless.wifinet4.device is missing'
	mode=$(uci -q get wireless.wifinet4.mode) || die 'wireless.wifinet4.mode is missing'
	network=$(uci -q get wireless.wifinet4.network) || die 'wireless.wifinet4.network is missing'
	disabled=$(uci -q get wireless.wifinet4.disabled || printf '0')
	channel=$(uci -q get wireless.radio1.channel) || die 'wireless.radio1.channel is missing'
	ap_device=$(uci -q get wireless.dendelion_5g.device) || die 'wireless.dendelion_5g.device is missing'
	ap_mode=$(uci -q get wireless.dendelion_5g.mode) || die 'wireless.dendelion_5g.mode is missing'
	ap_disabled=$(uci -q get wireless.dendelion_5g.disabled || printf '0')

	[ "$device" = radio1 ] || die 'wifinet4 is not attached to radio1'
	[ "$mode" = sta ] || die 'wifinet4 is not a station interface'
	case " $network " in *' clientwan '*) ;; *) die 'wifinet4 is not attached to clientwan' ;; esac
	case $disabled in 1|true|yes|on|enabled) die 'wifinet4 is disabled' ;; esac
	[ "$ap_device" = radio1 ] || die 'dendelion_5g is not attached to radio1'
	[ "$ap_mode" = ap ] || die 'dendelion_5g is not an AP interface'
	case $ap_disabled in 1|true|yes|on|enabled) die 'dendelion_5g is disabled' ;; esac
	case $channel in 36|40|44|48|149|153|157|161|165) ;; *) die 'radio1 channel is auto, DFS, or invalid' ;; esac
	if uci -q get wireless.radio1.scan_list >/dev/null 2>&1; then
		die 'wireless.radio1.scan_list must be absent'
	fi

	. /lib/functions.sh
	config_load wireless
	config_foreach count_station wifi-iface
	[ "$wireless_station_count" -eq 1 ] || die 'radio configuration must contain exactly one active station'

	printf 'preflight: OpenWrt %s on %s; radio1 fallback channel %s\n' \
		"$version" "$board" "$channel"
}

validate_stage() {
	local stage=$1 output
	check_stage_path "$stage"
	for path in config init daemon.uc ucode/apsta_scan_gate/policy.uc \
		service-policy-cases.uc service-action.sh manifest.sha256; do
		[ -f "$stage/$path" ] && [ ! -L "$stage/$path" ] ||
			die "staged file is missing or unsafe: $path"
	done

	(cd "$stage" && sha256sum -c manifest.sha256)
	/bin/sh -n "$stage/init"
	/bin/sh -n "$stage/service-action.sh"
	grep -q "^[[:space:]]*option enabled '0'[[:space:]]*$" "$stage/config" ||
		die 'staged UCI config is not disabled by default'

	output="$stage/daemon.ucb"
	arm_temporary_cleanup "$output"
	ucode -L "$stage/ucode/*.uc" \
		-cno-interp,dynlink=ubus,dynlink=uloop,dynlink=uci \
		-o "$output" "$stage/daemon.uc"
	ucode -L "$stage/ucode/*.uc" "$stage/service-policy-cases.uc"
	disarm_temporary_cleanup
	printf 'stage validation: passed\n'
}

service_running() {
	[ -x "$init" ] && "$init" running >/dev/null 2>&1
}

boot_links_present() {
	local link
	for link in /etc/rc.d/S??apsta-scan-gate /etc/rc.d/K??apsta-scan-gate; do
		[ -e "$link" ] || [ -L "$link" ] || continue
		return 0
	done
	return 1
}

canonical_boot_links_ready() {
	[ -L /etc/rc.d/S99apsta-scan-gate ] &&
		[ "$(readlink /etc/rc.d/S99apsta-scan-gate 2>/dev/null || true)" = \
			'../init.d/apsta-scan-gate' ] &&
		[ -L /etc/rc.d/K10apsta-scan-gate ] &&
		[ "$(readlink /etc/rc.d/K10apsta-scan-gate 2>/dev/null || true)" = \
			'../init.d/apsta-scan-gate' ]
}

procd_registered() {
	ubus -S call service list '{"name":"apsta-scan-gate"}' 2>/dev/null |
		grep -q '"apsta-scan-gate"'
}

api_present() {
	ubus -S list apsta_scan_gate 2>/dev/null | grep -qx apsta_scan_gate
}

daemon_process_present() {
	local command_line path
	for path in /proc/[0-9]*/cmdline; do
		[ -r "$path" ] || continue
		command_line=$(tr '\000' ' ' < "$path" 2>/dev/null || true)
		case " $command_line " in *' /usr/libexec/apsta-scan-gate.uc '*) return 0 ;; esac
	done
	return 1
}

live_daemon_present() {
	service_running || api_present || daemon_process_present
}

daemon_present() {
	live_daemon_present || procd_registered
}

backup_owned_files() {
	local backup timestamp source relative
	timestamp=$(date -u +%Y%m%dT%H%M%SZ)
	backup="/root/apsta-scan-gate-backup-$timestamp"
	[ ! -e "$backup" ] || backup="$backup-$$"
	mkdir -m 0700 "$backup"

	for source in "$init" "$config_file" "$daemon" "$module"; do
		[ ! -L "$source" ] || die "refusing symlinked owned path: $source"
		relative=${source#/}
		mkdir -p "$backup/${relative%/*}"
		if [ -f "$source" ]; then
			cp -p "$source" "$backup/$relative"
			touch "$backup/$relative.present"
		else
			touch "$backup/$relative.absent"
		fi
	done
	printf '%s\n' "$backup"
}

atomic_install() {
	local source=$1 destination=$2 mode=$3 temporary
	temporary="$destination.new.$$"
	case $temporary in
		/etc/*|/usr/libexec/*|/usr/share/ucode/apsta_scan_gate/*) ;;
		*) return 1 ;;
	esac
	mkdir -p "${destination%/*}" || return 1
	temporary_path=$temporary
	if ! cp "$source" "$temporary" || ! chmod "$mode" "$temporary" ||
	   ! mv "$temporary" "$destination"; then
		cleanup_temporary
		return 1
	fi
	temporary_path=
}

restore_owned_files() {
	local backup=$1 destination relative mode failed=0
	case $backup in /root/apsta-scan-gate-backup-*) ;; *) return 1 ;; esac
	[ -d "$backup" ] && [ ! -L "$backup" ] || return 1

	for destination in "$init" "$config_file" "$daemon" "$module"; do
		relative=${destination#/}
		case $destination in "$init") mode=0755 ;; *) mode=0644 ;; esac
		if [ -f "$backup/$relative.present" ] && [ -f "$backup/$relative" ]; then
			atomic_install "$backup/$relative" "$destination" "$mode" || failed=1
		elif [ -f "$backup/$relative.absent" ]; then
			rm -f "$destination" || failed=1
		else
			failed=1
		fi
	done
	[ "$failed" -eq 0 ]
}

rollback_transaction() {
	[ "$transaction_active" -eq 1 ] || return 0
	cleanup_temporary
	restore_owned_files "$transaction_backup"
}

transaction_exit() {
	if ! rollback_transaction; then
		printf 'apsta-scan-gate: interrupted install restore failed; recover from %s\n' \
			"$transaction_backup" >&2
	fi
}

transaction_signal() {
	trap - EXIT HUP INT TERM
	transaction_exit
	exit 130
}

begin_transaction() {
	transaction_backup=$1
	transaction_active=1
	trap transaction_exit EXIT
	trap transaction_signal HUP INT TERM
}

commit_transaction() {
	transaction_active=0
	transaction_backup=
	trap - EXIT HUP INT TERM
}

install_disabled() {
	local stage=$1 backup
	require_root
	preflight
	validate_stage "$stage"
	daemon_present && die 'refusing to replace a running or registered scan-gate daemon'
	boot_links_present && die 'refusing to replace a boot-enabled scan-gate service'

	backup=$(backup_owned_files)
	begin_transaction "$backup"
	if ! atomic_install "$stage/config" "$config_file" 0644 ||
	   ! atomic_install "$stage/init" "$init" 0755 ||
	   ! atomic_install "$stage/daemon.uc" "$daemon" 0644 ||
	   ! atomic_install "$stage/ucode/apsta_scan_gate/policy.uc" "$module" 0644; then
		restore_owned_files "$backup" ||
			die "disabled install failed and automatic restore failed; recover from $backup"
		commit_transaction
		die "disabled install failed; previous owned files were restored from $backup"
	fi
	"$init" disable >/dev/null 2>&1 || true

	if [ "$(uci -q get apsta-scan-gate.main.enabled)" != 0 ] ||
	   daemon_present || boot_links_present; then
		restore_owned_files "$backup" ||
			die "disabled-install verification failed and automatic restore failed; recover from $backup"
		commit_transaction
		die "disabled-install verification failed; previous owned files were restored from $backup"
	fi
	commit_transaction
	printf 'install-disabled: passed; previous owned files backed up at %s\n' "$backup"
}

validate_installed() {
	local output=/tmp/apsta-scan-gate-validate.$$.ucb
	for path in "$init" "$config_file" "$daemon" "$module"; do
		[ -f "$path" ] && [ ! -L "$path" ] || die "installed path is missing or unsafe: $path"
	done
	/bin/sh -n "$init"
	arm_temporary_cleanup "$output"
	ucode -L '/usr/share/ucode/*.uc' \
		-cno-interp,dynlink=ubus,dynlink=uloop,dynlink=uci \
		-o "$output" "$daemon"
	disarm_temporary_cleanup
}

wait_for_stock_resume() {
	local tries=0 status_json resumed
	while [ "$tries" -lt 6 ]; do
		status_json=$(ubus -S call apsta_scan_gate status 2>/dev/null || true)
		resumed=$(printf '%s\n' "$status_json" | jsonfilter -e '@.stock_resumed' 2>/dev/null || true)
		[ "$resumed" = true ] && return 0
		tries=$((tries + 1))
		sleep 1
	done
	return 1
}

activation_rollback() {
	local failed=0 was_present=0
	daemon_present && was_present=1
	uci set apsta-scan-gate.main.enabled=0 || failed=1
	uci commit apsta-scan-gate || failed=1
	"$init" disable >/dev/null 2>&1 || failed=1

	if [ "$was_present" -eq 1 ]; then
		if ubus -S call apsta_scan_gate resume_stock '{}' >/dev/null 2>&1 &&
		   wait_for_stock_resume; then
			"$init" stop >/dev/null 2>&1 || failed=1
		else
			# Preserve a possibly parked fallback AP until recovery can reset radio1.
			failed=1
		fi
	else
		"$init" stop >/dev/null 2>&1 || true
	fi
	boot_links_present && failed=1
	daemon_present && failed=1
	[ "$(uci -q get apsta-scan-gate.main.enabled || true)" = 0 ] || failed=1

	[ "$failed" -eq 0 ]
}

activate() {
	local recovery=${1:-} tries=0 status_json phase station_ifname ap_ifname \
		fallback ap_status ap_frequency runtime_error
	case $recovery in radio0|ethernet) ;; *) die 'activation requires confirmed radio0 or Ethernet recovery' ;; esac
	require_root
	preflight
	validate_installed

	if ! uci set apsta-scan-gate.main.enabled=1 ||
	   ! uci commit apsta-scan-gate ||
	   ! "$init" enable || ! canonical_boot_links_ready; then
		if activation_rollback; then
			die 'activation setup failed; configuration and service were rolled back'
		fi
		die 'activation setup failed and rollback was incomplete; boot/config state is unverified and recovery must inspect it before resetting radio1'
	fi
	if ! "$init" start; then
		if activation_rollback; then
			die 'service start failed; activation was rolled back'
		fi
		die 'service start failed and rollback was incomplete; boot/config state is unverified and recovery must inspect it before resetting radio1'
	fi

	while [ "$tries" -lt 25 ]; do
		status_json=$(ubus -S call apsta_scan_gate status 2>/dev/null || true)
		if [ -n "$status_json" ]; then
			phase=$(printf '%s\n' "$status_json" | jsonfilter -e '@.phase' 2>/dev/null || true)
			station_ifname=$(printf '%s\n' "$status_json" | jsonfilter -e '@.station_ifname' 2>/dev/null || true)
			ap_ifname=$(printf '%s\n' "$status_json" | jsonfilter -e '@.ap_ifname' 2>/dev/null || true)
			fallback=$(printf '%s\n' "$status_json" | jsonfilter -e '@.fallback_frequency' 2>/dev/null || true)
			ap_status=$(printf '%s\n' "$status_json" | jsonfilter -e '@.ap_status' 2>/dev/null || true)
			ap_frequency=$(printf '%s\n' "$status_json" | jsonfilter -e '@.ap_frequency' 2>/dev/null || true)
			runtime_error=$(printf '%s\n' "$status_json" | jsonfilter -e '@.last_error' 2>/dev/null || true)

			case $phase in
				connected|parked)
					if [ -n "$station_ifname" ] && [ -n "$ap_ifname" ] &&
					   [ -n "$fallback" ] && [ -z "$runtime_error" ] &&
					   [ "$ap_status" = ENABLED ] && [ -n "$ap_frequency" ] &&
					   service_running && canonical_boot_links_ready &&
					   [ "$(uci -q get apsta-scan-gate.main.enabled || true)" = 1 ]; then
						if [ "$phase" = connected ] || [ "$ap_frequency" = "$fallback" ]; then
							printf 'activate: control plane is stable; external Wi-Fi canary is still required; no wireless reload was issued\n'
							printf '%s\n' "$status_json"
							return 0
						fi
					fi
					;;
			esac
		fi
		tries=$((tries + 1))
		sleep 1
	done

	if activation_rollback; then
		die 'service did not reach a healthy connected or parked state within 25 seconds; activation was rolled back'
	fi
	die 'service did not stabilize and rollback was incomplete; boot/config state is unverified and recovery must inspect it before resetting radio1'
}

status() {
	local configured=missing installed=no
	require_root
	if [ -f "$config_file" ]; then
		installed=yes
		configured=$(uci -q get apsta-scan-gate.main.enabled || printf 'unknown')
	fi
	printf 'installed=%s configured_enabled=%s boot_links_present=%s canonical_boot_links=%s procd_registered=%s running=%s api_present=%s process_present=%s\n' \
		"$installed" "$configured" \
		"$(boot_links_present && printf yes || printf no)" \
		"$(canonical_boot_links_ready && printf yes || printf no)" \
		"$(procd_registered && printf yes || printf no)" \
		"$(service_running && printf yes || printf no)" \
		"$(api_present && printf yes || printf no)" \
		"$(daemon_process_present && printf yes || printf no)"
	ubus -S call apsta_scan_gate status 2>/dev/null || true
}

disable() {
	local live=0 registration_only=0 failed=0 release_failed=0 reset_required=0
	require_root
	if live_daemon_present; then
		live=1
	elif procd_registered; then
		registration_only=1
	fi
	if [ -x "$init" ]; then
		"$init" disable >/dev/null 2>&1 || failed=1
	fi

	if [ "$live" -eq 1 ]; then
		if ! ubus -S call apsta_scan_gate resume_stock '{}' >/dev/null 2>&1 ||
		   ! wait_for_stock_resume; then
			release_failed=1
		fi
	elif [ "$registration_only" -eq 1 ]; then
		# Nothing live can acknowledge its prior radio state. Remove the stale
		# procd registration, then require a recovery-side radio1 reset.
		[ -x "$init" ] && "$init" stop >/dev/null 2>&1 || failed=1
		reset_required=1
	fi
	if [ "$release_failed" -eq 0 ] && [ "$registration_only" -eq 0 ] &&
	   [ -x "$init" ]; then
		"$init" stop >/dev/null 2>&1 || failed=1
	fi
	if [ -f "$config_file" ]; then
		uci set apsta-scan-gate.main.enabled=0 || failed=1
		uci commit apsta-scan-gate || failed=1
	fi
	boot_links_present && failed=1
	if [ "$release_failed" -eq 1 ]; then
		die 'stock reconnect was not acknowledged, so the live service was not stopped; boot/config disable may also be incomplete—inspect status and reset radio1 from recovery'
	fi
	service_running && failed=1
	daemon_present && failed=1
	[ "$failed" -eq 0 ] || die 'disable safety steps were incomplete; inspect status and reset radio1 from recovery'
	if [ "$reset_required" -eq 1 ]; then
		die 'a stale procd registration was removed and boot/config were disabled, but radio ownership could not be acknowledged; reset radio1 from recovery'
	fi
	if [ "$live" -eq 1 ]; then
		printf 'disable: stock AP/STA ownership was acknowledged; no wireless reload was issued\n'
	else
		printf 'disable: service was not running; configuration is off; reset radio1 from recovery if station state is stale\n'
	fi
}

remove_service() {
	local backup
	require_root
	backup=$(backup_owned_files)
	disable
	rm -f /etc/rc.d/S99apsta-scan-gate /etc/rc.d/K10apsta-scan-gate
	rm -f "$init" "$config_file" "$daemon" "$module"
	rmdir /usr/share/ucode/apsta_scan_gate 2>/dev/null || true
	printf 'remove: owned files removed; backup retained at %s\n' "$backup"
}

action=${1:-}
case $action in
	preflight) preflight ;;
	validate-stage) validate_stage "${2:-}" ;;
	install-disabled) install_disabled "${2:-}" ;;
	activate) activate "${3:-}" ;;
	status) status ;;
	disable) disable ;;
	remove) remove_service ;;
	*) die 'unknown remote action' ;;
esac
