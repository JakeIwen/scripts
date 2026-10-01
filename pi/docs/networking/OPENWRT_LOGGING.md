# OpenWrt remote logging

[Pi documentation index](../../README.md)

OpenWrt sends classic syslog over the LAN to vanpi. Rsyslog always writes to
bounded RAM rings, even when the removable flash drive is unavailable:

```text
/run/vanpi-network/spool/dendelion.log
/run/vanpi-network/spool/network.jsonl
```

The router also retains a 256 KiB in-memory ring, readable with `logread`, for
short vanpi outages. That ring still disappears when OpenWrt reboots. Remote
delivery is UDP and best-effort so an unavailable Pi can never block routing.

The independent recorder mirrors these physical log generations to
`/mnt/EXFAT512/vanpi-network/openwrt/` only after verifying the configured UUID,
filesystem type and exact mount. It also keeps its incident database there.
When flash is unavailable, capture and reporting use bounded RAM. RAM history
is volatile across Pi reboot. The recorder never creates a substitute flash
directory on the underlying SD filesystem.

The structured `network.jsonl` stream retains diagnostic messages from the
existing router probes, hostapd/wpa_supplicant, netifd, mwan3, kernel, and
resolver failures. Ordinary DNS questions/answers and SSH login chatter are
excluded from this stream. The classic text format remains unchanged.

Each JSON line contains `schema_version` (string `"1"`), `received_at`,
`reported_at`, `protocol_version`, `source_ip`, `hostname`, `tag`, and `message`.
`received_at` is the Pi rsyslog receipt time. `reported_at` is rsyslog's parsed
device timestamp, never the import time. Classic syslog (`protocol_version`
`"0"`) has only second resolution and omits year/timezone: rsyslog infers those,
so this field is uncertain even if it happens to agree with the Pi. It contains
no device boot ID, monotonic clock, or delivery sequence. Historical
`dendelion.log*` files contain only Pi receipt timestamps; their original
device timestamp cannot be reconstructed.

Rsyslog rotates each RAM stream when it reaches 1 MiB, using
`/usr/local/libexec/vanpi-rotate-network-log legacy|json`. Each stream retains
three previous files (`.1` through `.3`) plus its active file: approximately
8 MiB total for both streams. Rsyslog can exceed a threshold by one complete message/batch; the limit
is not byte-exact. Rotation runs on the existing receiver, without a timer,
compression job, or router flash writes. The helper rejects unexpected
symlinks or directories before changing any rotation files. This bounded capture cannot
recover records lost while the Pi or UDP receiver was unavailable.

Vanpi's rsyslog listener starts on UDP/514 before DHCP is available, but its
dedicated ruleset discards messages unless their source address is
`192.168.6.1`. The RAM spool is `0750 root:adm`, and files are `0640 root:adm`.
`pi/tmpfiles.d/vanpi-network.conf` creates only RAM directories. The rsyslog
service drop-in ensures they exist before opening UDP/514. The recorder owns
flash retention; the old SD logrotate rule is disabled. EXFAT's existing mount
permissions apply to flash files and cannot provide per-file Unix privacy.

The old `/var/log/openwrt/` and `/var/lib/vanpi-network/` directories are frozen
migration rollback copies, explicitly excluded from new Borg archives. Active
flash and RAM are already outside Borg's `--one-file-system` root traversal.
Historical events retain their original receipt timestamps, byte offsets and
first-line generation identities; migration preserves file modification times
for archive retention without copying unsupported FAT ownership or mode bits.

## Install or restore the receiver

Use the targeted storage deployer from this checkout. It checks live file
hashes and mount identity, snapshots the affected configuration, migrates
SQLite and logs with verification, and preserves history on rollback:

```bash
python3 pi/deploy_network_storage.py check --plan /tmp/van-network-storage-plan.json
python3 pi/deploy_network_storage.py apply --plan /tmp/van-network-storage-plan.json
```

The old receiver setup and general recorder deployer refuse to overwrite an
installed storage migration. Do not redeploy a historical `/var/log/openwrt`
receiver configuration. See [recorder operations](NETWORK_FLIGHT_RECORDER.md)
for storage status, bounded fallback behavior and exact-release rollback.
No router configuration change is needed for this storage migration.

## Verify

```bash
# vanpi
sudo systemctl status rsyslog
sudo ss -lunp | grep ':514'
sudo tail -n 30 /run/vanpi-network/spool/dendelion.log
sudo logrotate --debug /etc/logrotate.d/openwrt-dendelion
sudo tail -n 2 /run/vanpi-network/spool/network.jsonl
/home/pi/scripts/network_flight_recorder.py status --json

# OpenWrt
uci show system | grep -E 'log_(size|ip|port|proto)'
logger -t remote-log-test 'dendelion remote logging test'
```

The test line should appear on vanpi within a second. If it does not, first
confirm that vanpi still owns `192.168.6.103` and that the router can reach it.
Do not expose UDP/514 through the WAN firewall.

On a Pi/Linux checkout with rsyslog installed, validate the receiver with an
isolated loopback listener and temporary files under `/run` (no live service
changes). The root receiver and separate `pi`/`adm` reader reproduce the old
post-rotation permissions failure, verify setgid inheritance and exercise the
bounded installer repair. Initial file readability alone is insufficient:

```bash
sudo python3 pi/tests/network/check_openwrt_network_spool.py pi/scripts/openwrt-logging/30-openwrt-dendelion.conf pi/scripts/openwrt-logging/rotate_network_log.py --tmpfiles pi/tmpfiles.d/vanpi-network.conf --repair-installer pi/deploy_network_storage.py --compare-modes
python3 -m unittest pi.tests.network.test_openwrt_network_spool
```
