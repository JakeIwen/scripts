#!/bin/bash
# Pull and verify dendelion's forced-command backup stream. The last valid
# snapshot is replaced atomically; any failure leaves it untouched.
set -euo pipefail
. /home/pi/scripts/backup/backup_conf.sh

log() { echo "[$(date '+%F %T')] openwrt backup: $*"; }
fail() { log "ERROR: $1" >&2; exit 1; }

for required_file in "$OPENWRT_BACKUP_KEY" "$OPENWRT_BACKUP_KNOWN_HOSTS"; do
	[ -r "$required_file" ] || fail "required SSH file is unreadable: $required_file"
done
for required_command in /usr/bin/ssh /usr/bin/timeout /usr/bin/tar \
	/usr/bin/sha256sum /usr/bin/jq; do
	[ -x "$required_command" ] || fail "required command is missing: $required_command"
done

umask 077
/usr/bin/install -d -m 0700 "$OPENWRT_SNAPSHOT_DIR"
incoming=$(/usr/bin/mktemp "$OPENWRT_SNAPSHOT_DIR/.dendelion.incoming.XXXXXX") \
	|| fail "could not create incoming snapshot"
verify_dir=$(/usr/bin/mktemp -d /tmp/openwrt-backup-verify.XXXXXX) \
	|| fail "could not create verification workspace"
case "$verify_dir" in
	/tmp/openwrt-backup-verify.*) ;;
	*) fail "refusing unexpected verification workspace: $verify_dir" ;;
esac

child=
cleanup() {
	/bin/rm -f "$incoming"
	/bin/rm -f \
		"$verify_dir/dendelion-sysupgrade.tar.gz" \
		"$verify_dir/system-board.json" \
		"$verify_dir/apk-installed-manifest.txt" \
		"$verify_dir/sysupgrade-file-list.txt" \
		"$verify_dir/created-at-utc.txt" \
		"$verify_dir/SHA256SUMS"
	/bin/rmdir "$verify_dir" 2>/dev/null || true
}
interrupted() {
	[ -z "$child" ] || /bin/kill -TERM "$child" 2>/dev/null || true
	exit 143
}
trap cleanup EXIT
trap interrupted HUP INT TERM

/usr/bin/timeout --signal=TERM --kill-after=5s 90s \
	/usr/bin/ssh \
		-T \
		-i "$OPENWRT_BACKUP_KEY" \
		-o BatchMode=yes \
		-o IdentitiesOnly=yes \
		-o ClearAllForwardings=yes \
		-o StrictHostKeyChecking=yes \
		-o UserKnownHostsFile="$OPENWRT_BACKUP_KNOWN_HOSTS" \
		-o ConnectTimeout=10 \
		-o ServerAliveInterval=15 \
		-o ServerAliveCountMax=2 \
		-o LogLevel=ERROR \
		"$OPENWRT_BACKUP_HOST" openwrt-backup > "$incoming" &
child=$!
if wait "$child"; then
	child=
else
	rc=$?
	child=
	fail "router export failed with status $rc; keeping the last valid snapshot"
fi
[ -s "$incoming" ] || fail "router returned an empty backup"

actual_members=$(/usr/bin/tar -tzf "$incoming" | LC_ALL=C /usr/bin/sort) \
	|| fail "outer backup archive is unreadable"
expected_members=$(printf '%s\n' \
	SHA256SUMS \
	apk-installed-manifest.txt \
	created-at-utc.txt \
	dendelion-sysupgrade.tar.gz \
	system-board.json \
	sysupgrade-file-list.txt | LC_ALL=C /usr/bin/sort)
[ "$actual_members" = "$expected_members" ] \
	|| fail "outer backup archive has missing, duplicate, or unexpected members"

/usr/bin/tar -xzf "$incoming" -C "$verify_dir"
(
	cd "$verify_dir"
	/usr/bin/sha256sum -c SHA256SUMS >/dev/null
) || fail "backup checksum verification failed"

/usr/bin/jq -e \
	'(.board_name == "linksys,e8450-ubi") and (.release.version | length > 0)' \
	"$verify_dir/system-board.json" >/dev/null \
	|| fail "backup came from an unexpected router model or lacks release metadata"
[ -s "$verify_dir/apk-installed-manifest.txt" ] \
	|| fail "installed package manifest is empty"

inner_members=$(/usr/bin/tar -tzf "$verify_dir/dendelion-sysupgrade.tar.gz" \
	| /usr/bin/sed '/\/$/d' | LC_ALL=C /usr/bin/sort) \
	|| fail "nested sysupgrade archive is unreadable"
declared_members=$(/usr/bin/sed 's#^/##' "$verify_dir/sysupgrade-file-list.txt" \
	| LC_ALL=C /usr/bin/sort)
while IFS= read -r declared_path; do
	printf '%s\n' "$inner_members" | /bin/grep -Fx "$declared_path" >/dev/null \
		|| fail "nested archive omits declared path: $declared_path"
done <<< "$declared_members"
while IFS= read -r archived_path; do
	if printf '%s\n' "$declared_members" | /bin/grep -Fx "$archived_path" >/dev/null; then
		continue
	fi
	# sysupgrade generates this restore-time service-state helper itself, so it
	# appears in the archive but not in the pre-backup `sysupgrade -l` output.
	[ "$archived_path" = 'etc/uci-defaults/10_disable_services' ] \
		|| fail "nested archive has unexpected path: $archived_path"
done <<< "$inner_members"
for required_path in \
	etc/config/dhcp \
	etc/config/dropbear \
	etc/config/firewall \
	etc/config/mwan3 \
	etc/config/network \
	etc/config/sqm \
	etc/config/system \
	etc/config/wireless \
	etc/dropbear/authorized_keys \
	etc/firewall.ttl-clientwan.nft \
	etc/sysupgrade.conf \
	usr/libexec/openwrt-backup-export; do
	printf '%s\n' "$inner_members" | /bin/grep -Fx "$required_path" >/dev/null \
		|| fail "nested archive is missing required path: $required_path"
done

/bin/chmod 0600 "$incoming"
/bin/mv -f "$incoming" "$OPENWRT_SNAPSHOT_FILE"
/bin/date '+%F %T' > "$OPENWRT_BACKUP_STAMP"
snapshot_size=$(/usr/bin/du -h "$OPENWRT_SNAPSHOT_FILE" | /usr/bin/cut -f1)
log "saved and verified $OPENWRT_SNAPSHOT_FILE ($snapshot_size)"
