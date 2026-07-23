#!/bin/bash
set -euo pipefail

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

if (( EUID != 0 )); then
  fail "run this setup through sudo"
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RSYSLOG_SOURCE="$SCRIPT_DIR/openwrt-logging/30-openwrt-dendelion.conf"
LOGROTATE_SOURCE="$SCRIPT_DIR/openwrt-logging/openwrt-dendelion.logrotate"
RSYSLOG_TARGET=/etc/rsyslog.d/30-openwrt-dendelion.conf
LOGROTATE_TARGET=/etc/logrotate.d/openwrt-dendelion
LOG_DIR=/var/log/openwrt
LOG_FILE="$LOG_DIR/dendelion.log"

[[ -r "$RSYSLOG_SOURCE" ]] || fail "missing template: $RSYSLOG_SOURCE"
[[ -r "$LOGROTATE_SOURCE" ]] || fail "missing template: $LOGROTATE_SOURCE"

export DEBIAN_FRONTEND=noninteractive
/usr/bin/apt-get install -y --no-install-recommends rsyslog logrotate

for required_path in \
  /usr/bin/chmod \
  /usr/bin/chown \
  /usr/bin/install \
  /usr/bin/ss \
  /usr/bin/systemctl \
  /usr/sbin/logrotate \
  /usr/sbin/rsyslogd
do
  [[ -x "$required_path" ]] || fail "required command is missing: $required_path"
done

# Validate the receiver on its own before replacing the deployed copy.
/usr/sbin/rsyslogd -N1 -f "$RSYSLOG_SOURCE" >/dev/null 2>&1

/usr/bin/install -d -o root -g adm -m 0750 "$LOG_DIR"
if [[ ! -e "$LOG_FILE" ]]; then
  /usr/bin/install -o root -g adm -m 0640 /dev/null "$LOG_FILE"
else
  /usr/bin/chown root:adm "$LOG_FILE"
  /usr/bin/chmod 0640 "$LOG_FILE"
fi
/usr/bin/install -o root -g root -m 0644 "$RSYSLOG_SOURCE" "$RSYSLOG_TARGET"
/usr/bin/install -o root -g root -m 0644 "$LOGROTATE_SOURCE" "$LOGROTATE_TARGET"

# Validate the complete system configuration, then activate it persistently.
/usr/sbin/rsyslogd -N1 >/dev/null 2>&1
/usr/sbin/logrotate --debug "$LOGROTATE_TARGET" >/dev/null 2>&1
/usr/bin/systemctl enable rsyslog.service >/dev/null
/usr/bin/systemctl restart rsyslog.service
/usr/bin/ss -lun | /usr/bin/grep -Eq '(^|[[:space:]])0\.0\.0\.0:514([[:space:]]|$)|(^|[[:space:]])\*:514([[:space:]]|$)' \
  || fail "rsyslog is not listening on UDP/514"

echo "OpenWrt syslog receiver installed; awaiting 192.168.6.1 on UDP/514"
