#!/bin/bash
# Pull and verify the primary NanoStation's complete persistent configuration.
# The last valid snapshot is replaced atomically; any failure leaves it intact.
set -euo pipefail
. /home/pi/scripts/backup/backup_conf.sh

log() { echo "[$(date '+%F %T')] UBNT backup: $*"; }
fail() { log "ERROR: $1" >&2; exit 1; }

for required_file in "$UBNT_BACKUP_KEY" "$UBNT_BACKUP_KNOWN_HOSTS"; do
	[ -r "$required_file" ] || fail "required SSH file is unreadable: $required_file"
done
for required_command in /usr/bin/ssh /usr/bin/timeout /usr/bin/tar \
	/usr/bin/gzip /usr/bin/stat /usr/bin/mktemp /usr/bin/install \
	/usr/bin/awk /usr/bin/sort /usr/bin/uniq /usr/bin/head /usr/bin/du \
	/usr/bin/cut /bin/grep /bin/mv /bin/rm /bin/chmod /bin/date; do
	[ -x "$required_command" ] || fail "required command is missing: $required_command"
done

umask 077
/usr/bin/install -d -o root -g root -m 0700 "$UBNT_SNAPSHOT_DIR"
incoming=$(/usr/bin/mktemp "$UBNT_SNAPSHOT_DIR/.ubnt.incoming.XXXXXX") \
	|| fail "could not create incoming snapshot"
members_file=
child=
cleanup() {
	/bin/rm -f "$incoming"
	[ -z "$members_file" ] || /bin/rm -f "$members_file"
}
interrupted() {
	[ -z "$child" ] || /bin/kill -TERM "$child" 2>/dev/null || true
	exit 143
}
trap cleanup EXIT
trap interrupted HUP INT TERM

members_file=$(/usr/bin/mktemp /tmp/ubnt-backup-members.XXXXXX) \
	|| fail "could not create member-list workspace"
case "$members_file" in
	/tmp/ubnt-backup-members.*) ;;
	*) fail "refusing unexpected member-list workspace: $members_file" ;;
esac

/usr/bin/timeout --signal=TERM --kill-after=5s 120s \
	/usr/bin/ssh \
		-T \
		-i "$UBNT_BACKUP_KEY" \
		-o BatchMode=yes \
		-o IdentitiesOnly=yes \
		-o ClearAllForwardings=yes \
		-o StrictHostKeyChecking=yes \
		-o UserKnownHostsFile="$UBNT_BACKUP_KNOWN_HOSTS" \
		-o HostKeyAlgorithms=+ssh-rsa \
		-o PubkeyAcceptedAlgorithms=+ssh-rsa \
		-o ConnectTimeout=10 \
		-o ServerAliveInterval=15 \
		-o ServerAliveCountMax=2 \
		-o LogLevel=ERROR \
		"$UBNT_BACKUP_HOST" \
		'exec /bin/tar -czf - -C /etc persistent' > "$incoming" &
child=$!
if wait "$child"; then
	child=
else
	rc=$?
	child=
	fail "device export failed with status $rc; keeping the last valid snapshot"
fi

[ -s "$incoming" ] || fail "device returned an empty backup"
snapshot_bytes=$(/usr/bin/stat -c %s "$incoming") \
	|| fail "could not measure incoming snapshot"
[ "$snapshot_bytes" -le "$UBNT_BACKUP_MAX_BYTES" ] \
	|| fail "snapshot is unexpectedly large (${snapshot_bytes} bytes; limit ${UBNT_BACKUP_MAX_BYTES})"
/usr/bin/gzip -t "$incoming" || fail "snapshot gzip stream is corrupt"
/usr/bin/tar -tzf "$incoming" > "$members_file" \
	|| fail "snapshot tar archive is unreadable"

# Reject absolute paths, parent traversal, duplicate members, and anything
# outside /etc/persistent before retaining a credential-bearing archive.
if /usr/bin/awk '
	BEGIN { bad = 0 }
	/^\// { bad = 1 }
	/(^|\/)\.\.(\/|$)/ { bad = 1 }
	!/^persistent(\/|$)/ { bad = 1 }
	END { exit bad }
' "$members_file"; then
	:
else
	fail "snapshot contains an unsafe or unexpected path"
fi
duplicate_member=$(LC_ALL=C /usr/bin/sort "$members_file" \
	| /usr/bin/uniq -d | /usr/bin/head -n 1)
[ -z "$duplicate_member" ] || fail "snapshot contains duplicate archive members"

for required_path in \
	persistent/rc.postsysinit \
	persistent/config/.profile \
	persistent/config/cron \
	persistent/config/wifi-priority \
	persistent/profiles/system.cfg \
	persistent/scripts/wifi_manager.sh; do
	/bin/grep -Fx "$required_path" "$members_file" >/dev/null \
		|| fail "snapshot is missing required path: $required_path"
done

profile_count=$(/usr/bin/awk '
	/^persistent\/profiles\/[^/]+$/ { count++ }
	END { print count + 0 }
' "$members_file")
[ "$profile_count" -ge 1 ] || fail "snapshot contains no saved profiles"
/usr/bin/tar -xzOf "$incoming" persistent/profiles/system.cfg \
	| /bin/grep '^wireless\.1\.ssid=' >/dev/null \
	|| fail "system.cfg is not a valid saved wireless profile"

/bin/chmod 0600 "$incoming"
/bin/mv -f "$incoming" "$UBNT_SNAPSHOT_FILE"
/bin/date '+%F %T' > "$UBNT_BACKUP_STAMP"
snapshot_size=$(/usr/bin/du -h "$UBNT_SNAPSHOT_FILE" | /usr/bin/cut -f1)
log "saved and verified $profile_count profiles in $UBNT_SNAPSHOT_FILE ($snapshot_size)"
