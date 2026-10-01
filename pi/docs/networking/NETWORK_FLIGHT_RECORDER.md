# Network flight recorder

[Pi documentation index](../../README.md) · [Verification report](NETWORK_FLIGHT_RECORDER_VERIFICATION.md)

The recorder turns the existing router, antenna and Pi observations into a
durable incident timeline. Open **Network History** in the dashboard at
`http://vanpi.lan:8788/#network-history?hours=6`. Select a range, uplink, device
or text search, then select an incident to inspect its observations, hypotheses
and underlying evidence. Each observation exposes its source, timing quality,
receipt/import timestamps and physical-record provenance. The address-bar link
preserves the selection; an incident can download a bounded JSON bundle.

The separate `network-flight-recorder.service` collects even when the dashboard
is stopped. It adds no internet probes, speed tests, scans or recovery actions.
The existing probe schedules and mwan3 routing semantics remain authoritative.
The dashboard and CLI need only local access to the Pi, not a cloud service.

## Status and routine use

Run these from the Mac. They affect only the recorder when a service operation
is requested:

```bash
ssh pi@vanpi.lan 'systemctl status network-flight-recorder.service --no-pager'
```

```bash
ssh pi@vanpi.lan 'sudo systemctl start network-flight-recorder.service'
```

```bash
ssh pi@vanpi.lan 'sudo systemctl stop network-flight-recorder.service'
```

```bash
ssh pi@vanpi.lan 'sudo systemctl restart network-flight-recorder.service'
```

```bash
ssh pi@vanpi.lan 'python3 /home/pi/scripts/network_flight_recorder.py status --hours 1'
```

For an explicit storage-aware status check that fails if its configuration is
missing:

```bash
ssh pi@vanpi.lan 'python3 /home/pi/scripts/network_flight_recorder.py --storage-config /etc/vanpi-network-storage.json status --hours 1 --json'
```

Readable reports accept a recent range or explicit timestamps. Explicit ISO
timestamps require an offset; UTC epoch seconds are also supported. Examples:

```bash
ssh pi@vanpi.lan 'python3 /home/pi/scripts/network_flight_recorder.py report --hours 24 --uplink clientwan --limit 100'
```

```bash
ssh pi@vanpi.lan 'python3 /home/pi/scripts/network_flight_recorder.py report --start 2026-09-24T09:00:00Z --end 2026-09-24T10:00:00Z --search gateway --json'
```

Use an incident ID from the report or dashboard link for export. Replace the
quoted ID below with that ID and choose an unused output filename:

```bash
ssh pi@vanpi.lan 'python3 /home/pi/scripts/network_flight_recorder.py export --incident INCIDENT_ID_FROM_REPORT --limit 500 --json' > /private/tmp/van-network-incident.json
```

This observed antenna incident was verified during installation; the command
is runnable while that incident remains within retention:

```bash
ssh pi@vanpi.lan 'python3 /home/pi/scripts/network_flight_recorder.py export --incident 6e549b26183cdfab1308525b --limit 500 --json'
```

Exports contain schema version, clock semantics, source coverage, limitations,
the incident, and observations from its interval plus 120 seconds on either
side. Boundary evidence is prioritized when the 500-row cap is reached.
`counts` and `truncated` disclose omitted rows. Nearby context is not itself
evidence of causation. Use a smaller interval/filter to inspect a busy timeline;
full-range counts do not depend on the displayed row limit.

## Evidence coverage

This foundation map comes from the initial read-only live inspection on
2026-09-30 UTC and comparison with the checkout. The legacy persistence paths
in the table describe the pre-migration sources; active storage paths are
documented below. Freshness is evaluated anew in every report. See the
verification report for deployment status and remaining gaps.

| Source reused | Persistence and cadence | What it establishes; limits |
| --- | --- | --- |
| OpenWrt syslog | Existing `/var/log/openwrt/dendelion.log*`; 30 daily generations, 5 MiB active threshold evaluated by logrotate. Router keeps a 256 KiB RAM ring. | hostapd/wpa_supplicant association/authentication, AP/radio transitions, netifd/DHCP, mwan3 decisions, resolver faults and relevant kernel messages. UDP has no delivery guarantee or exact lost-message count. |
| `clientwan-path` | Existing syslog; 5-second sleep plus bounded sequential pings; immediate changes and 12-sample summaries. | Interface state, gateway response, and two public test destinations. Available only for clientwan. An administratively down interface is unknown/down context, not a measured provider outage. |
| `uplink-https` | Existing syslog and router `/tmp/uplink-https/` snapshots; two requests then 30-second sleep per uplink; changes and every second sample logged. | HTTP status and curl exit code for two destinations. Existing mwan3 device **and socket mark** isolate routing; curl interface binding alone is insufficient. DNS still uses the shared router resolver. |
| UBNT manager | Existing `/var/log/ubnt-wifi.log` in RAM; automatic manager runs each minute, healthy heartbeat at most hourly. RAM log rolls at 256 KiB to its last 1,000 lines. | Selections, connection attempts, changes and failures. Recorder reads the bounded log, boot ID and uptime through existing SSH once a minute; it never invokes a scan or manager action. Polling can miss records removed between snapshots. |
| Pi system monitor | Existing `/var/lib/vanpi-monitor/events.sqlite3`; 5-second samples for 48 hours, minute rollups for 90 days and persistent notable events. | Selected restart/boot, power, thermal, resource, service and kernel context. Reads are read-only; initial context import is capped at 2,000 relevant events/30 days. The original health reports retain their full-range counts, indexes and caching. |
| Recorder availability | New independent service heartbeat every minute; source receipts checked separately. | Collector gaps and source-read failures. A functioning collector is not proof of working internet. |

In the storage-aware installation, the additive Pi receiver spool,
`/run/vanpi-network/spool/network.jsonl`, preserves both
the Pi receipt timestamp and rsyslog's parsed source timestamp. It selects
diagnostic tags and resolver failures, excluding ordinary DNS questions,
answers and login churn. The legacy text format is preserved in the sibling
`dendelion.log`. Both bounded RAM streams are mirrored under
`/mnt/EXFAT512/vanpi-network/openwrt/` when the verified flash volume is present.
The old `/var/log/openwrt/` files remain frozen migration/rollback evidence;
ongoing router log writes no longer target the boot SD. See
[OpenWrt logging](OPENWRT_LOGGING.md) for the exact schema and receiver setup.
No router or antenna instrumentation changes are needed.

## Clock, provenance and incident rules

Schema version 1 stores separate `time`, `source_time`, `received_at` and
`imported_at` fields, in UTC epoch seconds. The dashboard prints UTC. Original
timestamp text, timezone/offset, file/generation/offset or source row identity
remain in provenance when available. Boot/session identity and monotonic values
are preserved where the source supplies them.

Storage-aware reports also expose `record_id`, the stable physical-record
fingerprint. Numeric event `id` values are local database row IDs and can change
when RAM evidence is merged into flash. Compare `record_id` across storage
tiers. Incident IDs use physical identity; migration aliases preserve older
incident links. These identities establish record continuity, not authenticity
of best-effort UDP delivery.

- Legacy syslog's leading RFC3339 timestamp is **Pi receipt time**. The original
  router event timestamp was discarded and cannot be reconstructed.
- New structured syslog retains `reported_at`. Classic RFC3164 omits year and
  timezone, which rsyslog infers; this is explicitly marked uncertain. Device
  clock corrections do not replace the receipt-time timeline coordinate.
- The antenna's wall clock was years wrong during inspection. Manager records
  use `uptime=...`; the recorder anchors those to a current boot ID, uptime and
  Pi receipt observation. These times are approximate, with uncertainty shown.
- Historical ingestion retains the old observation time and marks backfill.
  Import time is never substituted for an old incident's occurrence.
- Receiver clock reversals and observed reboot/session boundaries interrupt
  continuity. Long forward gaps cannot manufacture a long measured outage.
  Receipt timestamps across devices do not establish exact causal order.

Incidents are persisted observation episodes grouped by stream/session/domain
within a UTC day. Consecutive observations can continue an episode only within
180 seconds. Matching success closes a measured failure; unknown state, a gap,
reboot or day boundary does not prove recovery. Reports distinguish `recovered`,
`ongoing` at recent periodic probe evidence, and `unknown-end`. Discrete Pi
resource/monitoring, antenna and shared-resolver faults without a matching
recovery remain `unknown-end`, even when recent: one logged fault does not prove
the fault continues. Periodic gateway/public and HTTPS probes, including curl
DNS failures, may be ongoing while their failure evidence is fresh. A matching
explicit recovery still closes the episode. Durations measure observation
times, not exact outage duration. Overlapping uplinks retain separate episodes.

Rules distinguish local Wi-Fi/authentication, gateway/hotspot, external test
failure with a responding gateway, DNS, ambiguous HTTPS, mwan3, UBNT and
monitoring domains. Curl exit 6 supports name-resolution failure; a timeout
alone cannot distinguish DNS, connection, TLS, destination or routing trouble.
Two failed test destinations never prove the whole provider is down.
Observations and hypotheses remain separate in both CLI and UI.

Coverage uses `current`, `stale`, `unavailable` and `unknown`, rather than
assuming missing evidence means success. Periodic probe freshness is checked
separately from unrelated syslog traffic. Sparse event sources may be quiet
without being broken. The frontend also ages its last received report while an
HTTP request is hung, and qualifies retained results after refresh failures.
The separate `historical-import` coverage row reports a pending backlog as
unknown; current live receipts do not imply that an old interval is fully
imported. Counts describe the evidence currently retained, and can increase as
historical import catches up.

## Storage and resource bounds

The storage-aware recorder keeps its database and SQLite sidecars under
`/mnt/EXFAT512/vanpi-network/`, separate from the existing multi-gigabyte system
monitor. The active persistent database is `events.sqlite3`; bounded raw router
generations are under `openwrt/`. This keeps network retention and schema
ownership independent while reusing selected system context through indexed,
read-only queries. The original system-monitor storage policy is unchanged.
Dashboard requests never initialize or migrate either database.

`/etc/vanpi-network-storage.json` records the verified flash mountpoint, UUID,
filesystem and storage limits. `/run/vanpi-network/storage-status.json` records
the active tier and database. The CLI's `--database auto` selector is also the
dashboard default; it follows this state rather than a fixed boot-SD path.
Explicit `--database` paths remain available for isolated fixtures and
deliberate read-only inspection. The dashboard retains its explicit
`VAN_DASHBOARD_NETWORK_RECORDER_DB` override, and
`VANPI_NETWORK_STORAGE_CONFIG` selects an alternate configuration for isolated
tests. An explicitly configured but missing storage configuration fails closed.

If the expected flash volume is absent, mismatched or unavailable, the recorder
uses bounded `/run/vanpi-network/events.sqlite3`. It does not create a database
inside the empty `/mnt/EXFAT512` directory on the boot SD. RAM-mode evidence can
survive a recorder process restart but **does not survive a Pi reboot**. Older
flash history is unavailable while the volume is absent; an empty RAM interval
does not prove there was no incident. The dashboard tile reports a storage
warning, and timeline/incident views show the warning without opening a
coverage dropdown. CLI/exports include the same `storage` coverage explanation.

A RAM warning can also mean a receiver permission error or failed replay while
the drive remains mounted. Inspect the current error category and operation:

```bash
ssh pi@vanpi.lan 'cat /run/vanpi-network/storage-status.json; journalctl -u network-flight-recorder --since "1 hour ago" --no-pager'
```

Errors expose fixed diagnostic categories, not exception contents or raw logs.
They are journaled when the category/operation changes, rather than every retry.
Do not reboot to clear a RAM warning: first diagnose it and preserve buffered
evidence. Successful replay clears the current error.

The receiver spool directory must be `02750 root:adm`. On the deployed rsyslog
8.2302, size-limit rotation reopens files without reapplying `fileGroup`; the
setgid directory preserves `adm` inheritance. The installer repairs permissions
on only the two active streams and their three rotated generations through
verified directory/file descriptors. Tmpfiles `z` rules cannot perform that
repair here: systemd rejects the transition from the Pi-owned runtime directory
to the root-owned spool. Rotation acceptance must use a root receiver and a separate
unprivileged reader with supplementary `adm`; a same-user test misses this
failure. Existing raw files remain private to root and the read group in RAM.

The collector owns return-to-flash reconciliation. Physical record identities
deduplicate replay, and incident aliases preserve links when RAM rows receive
new numeric database IDs. Status and coverage distinguish durable flash from
volatile RAM. Do not manually move an open SQLite database/WAL pair or create
mountpoint contents to bypass a failed volume check.

While return-to-flash replay is succeeding, saved RAM observations drain before
new source import or RAM pruning resumes. The independent receiver keeps
capturing, and raw-file mirroring continues during replay. This prevents a
backlog import from evicting queued observations before they reach flash. A
failed replay resumes bounded RAM collection; the `replaying` status flag and
coverage explanation distinguish this deliberate import pause.

Persistent defaults are 30 days, 150,000 observations and a 256 MiB
database/sidecar budget; the RAM database/sidecar budget is 16 MiB.
The first applicable bound reached wins. SQLite page limits reserve headroom for WAL,
small ingestion commits constrain WAL growth, and count/page pressure evicts
old observations. Age pruning runs hourly. Retention can remove an incident's
original onset; exports disclose this limitation. Eviction/limit metadata is
included in structured reports. This retention does not change the original
system-monitor or legacy syslog policies.

Each RAM receiver stream holds four 1 MiB generations including the active
file: approximately 8 MiB total for legacy text and diagnostic JSON, plus a
complete-message/batch overrun. Rotation occurs in the rsyslog writer; it
performs no recursive removal and rejects unexpected symlinks/directories.
No new router flash writes are introduced. The flash mirror keeps bounded
physical generations, capped at 35 MiB of diagnostic JSON and 160 MiB of
legacy text. Those are separate from the database budget; old raw generations
can be evicted before every historical record is imported.

The former `/var/lib/vanpi-network/` database and `/var/log/openwrt/` logs are
frozen rollback copies, not writable fallback locations. They are excluded from
future Pi-root Borg archives. The active flash data is on another filesystem,
and `/run` is volatile; the existing Borg `--one-file-system` boundary excludes
their contents from the Pi-root archive; mountpoint directory metadata can
still be represented. A backup process that already loaded its configuration
is not interrupted and may include the frozen SD copies once. Subsequent jobs
load the new exclusions. Migration must add only the narrow exclusions
and must preserve unrelated deployed backup-script differences. This project
does not move or change the existing system monitor's database.

The collector checks file ingestion every five seconds with a 512 KiB total read
budget and 128 KiB per file, prioritizing the current JSON spool ahead of
historical files. It polls antenna/system context and its own heartbeat once a
minute; unchanged antenna snapshots are not reparsed. It tracks file generation and uncompressed byte offsets,
checkpoints ingestion durably, retries partial lines, reads gzip archives, and
deduplicates reimports of the same physical records. Identical text at distinct
offsets remains distinct evidence. Unparseable/overlong records are not inferred
to be successful observations. The service uses `Nice=10`, idle I/O priority
and a 20% CPU quota. A 128 MiB `MemoryMax` is configured, but the inspected Pi
kernel has no memory cgroup controller, so that limit is **not enforced** on
this Pi. Explicit read/storage bounds still apply; measured startup RSS was
23,516 KiB. The RAM runtime directory uses `0750`, with read-only system/home
access and the existing `adm` read group. Flash permissions follow the existing
exFAT mount options: the live filesystem canary observed mode `0755` despite
`chmod(0640)`. Per-file Unix permission changes are therefore not a privacy
boundary on that volume; use redacted exports when sharing evidence.

Dashboard reports cap displayed events and incidents at 200, detail/export at
500. The Flask adapter shares a ten-second bounded cache between clients and
uses a 15-second subprocess deadline. SQL computes full-range counts before
bounded row retrieval. Text search remains bounded by retained storage.

Messages and provenance are redacted before network-database persistence and
again at report boundaries. URLs, credential fields and authentication/command
arguments are removed; useful local device/uplink identities remain. The
raw syslog spool is not an export format. Share the redacted bundle,
not raw device logs or configurations.

## Targeted installation, update and rollback

For recorder code or receiver-permission maintenance on an existing
storage-managed installation, use a narrow checked update. It preserves the
deployed storage configuration and only restarts the recorder; receiver,
dashboard, frontend and backup configuration remain outside its target set.
Choose a new plan filename for each update:

```bash
python3 /Users/jacobr/dev/scripts/pi/deploy_network_storage.py check --recorder-only --plan /private/tmp/network-recorder-update.json
python3 /Users/jacobr/dev/scripts/pi/deploy_network_storage.py apply --recorder-only --plan /private/tmp/network-recorder-update.json
```

The manifest captures exact pre-update hashes and supports the same `rollback
--release` command below. Recorder-only rollback preserves database/raw evidence
in place, including pending RAM records, and restores just the recorder code
and tmpfiles configuration. Repaired live spool permissions remain in place;
restoring an older tmpfiles file can reintroduce its permissions bug at the next
boot or receiver restart. Readiness allows up to ten minutes for bounded RAM
replay and checks a fresh flash selection plus a read-only CLI report.

Build the frontend from the repository root:

```bash
npm --prefix pi/apps/van_dashboard/frontend run build
```

Use the storage-aware installer after the initial recorder installation.
`check` verifies the exact flash mount/UUID/filesystem, available space, managed
live files and the deployed Borg exclusion configuration. It records checksums
and service states and refuses unexpected live differences. It does not change
mounts or start a migration. Use an unused plan path:

```bash
python3 pi/deploy_network_storage.py check --plan /private/tmp/van-network-storage-plan.json
```

```bash
python3 pi/deploy_network_storage.py apply --plan /private/tmp/van-network-storage-plan.json
```

`apply` rechecks live and local fingerprints, preserves the existing database
and raw history on the verified flash volume, and saves exact rollback files
under `/var/lib/vanpi-network-deploy/storage/`. Only the managed recorder,
dashboard-storage adapter/frontend release, receiver configuration and service setup are
installed. The backup change appends `/var/lib/vanpi-network` and
`/var/log/openwrt` to the **deployed**
`/home/pi/scripts/backup/backup_conf.sh` `BORG_EXCLUDES` array, preserving every
other byte and validating shell syntax. It does not replace `pi_backup.sh` or
copy unrelated checkout backup edits. Mounts, network policies, CAN recording,
backup jobs and the global disk lifecycle are not changed. No Git staging or
commit occurs. The frontend build containing the storage banner is deployed
through the existing guarded dashboard release mechanism.

Rollback the release recorded in that plan with this single command:

```bash
python3 pi/deploy_network_storage.py rollback --release "$(python3 -c 'import json; print(json.load(open("/private/tmp/van-network-storage-plan.json"))["release"])')"
```

The current recorder/storage release is
`network-storage-20261001T102241Z-0dc55a92`. To undo its replay-order update while
retaining flash storage:

```bash
python3 pi/deploy_network_storage.py rollback --release network-storage-20261001T102241Z-0dc55a92
```

To undo the preceding permission/diagnostic update and original flash migration
as well, run that command first, then these in order:

```bash
python3 pi/deploy_network_storage.py rollback --release network-storage-20261001T101434Z-45aed0a4
python3 pi/deploy_network_storage.py rollback --release network-storage-20260930T093830Z-fba885dc
python3 pi/deploy_network_storage.py rollback --release network-storage-20260930T090204Z-e02b828b
```

Full storage rollback stops the recorder and receiver and consolidates pending RAM
evidence onto verified flash through `storage-sync`. Rolling back an update
restores the preceding flash-aware version. Rolling back the original migration
also backs up the latest consolidated database into the former SD location
before restoring the saved code/configuration, service setup and Borg exclusions.
The flash history is retained. It fails closed if the flash volume is missing or identity validation
fails; it does not silently restore a stale SD snapshot and lose new evidence.
It also refuses to overwrite managed files changed after deployment. Undoing
the original migration intentionally restores SD writes under the prior installation.

The original `pi/deploy_network_flight_recorder.py` installer and receiver setup
refuse their legacy workflow while storage configuration is installed. Roll
back the storage migration first if a full recorder rollback is intended.
Do not use `pi/sync_scripts.sh` to bypass those checks.

The initial recorder installation had three releases. **Only after storage
rollback**, these historical rollback steps undo the recorder in reverse order:

```bash
python3 pi/deploy_network_flight_recorder.py rollback --release network-20260930T022426Z-10d451d2
```

Then the two preceding releases:

```bash
python3 pi/deploy_network_flight_recorder.py rollback --release network-20260930T021028Z-87126f5d
```

```bash
python3 pi/deploy_network_flight_recorder.py rollback --release network-20260930T015418Z-e164e3ea
```

All steps preserve the collected database. See the verification report for the
last observed deployment state; create a fresh checked plan for future updates.

## Layout and verification

| Path | Responsibility |
| --- | --- |
| `pi/scripts/network_flight_recorder.py` | CLI: collector, import, reports and exports |
| `pi/scripts/network_recorder/` | Parsers/redaction, durable ingestion, storage, explicit incident rules and read-only reports |
| `pi/services/network-flight-recorder.service` | Collector lifecycle and filesystem sandbox |
| `pi/apps/van_dashboard/van_dashboard_history.py` | Validated, cached read-only CLI adapter |
| `pi/apps/van_dashboard/frontend/src/features/networkHistory/` | Dashboard ranges, filters, timeline, evidence and export UI |
| `pi/tests/network/fixtures/network_flight_recorder/` | Labeled observed/redacted and synthetic evidence |
| `pi/deploy_network_storage.py` | Verified flash/RAM migration, narrow backup exclusions and current-history-preserving rollback |
| `pi/deploy_network_flight_recorder.py` | Initial recorder deployment/rollback; guarded against use after storage migration |

Run the focused backend cases and established dashboard suite using the
[documented Flask test environment](../dashboard/DASHBOARD_TESTING.md):

```bash
python3 -m unittest pi.tests.network.test_network_flight_recorder pi.tests.network.test_openwrt_network_spool pi.tests.dashboard.test_network_history
```

```bash
npm --prefix pi/apps/van_dashboard/frontend test -- src/features/networkHistory src/features/network src/features/systemHealth src/features/ubnt src/dashboard
```

Client-specific end-to-end reachability remains unobserved. WAN/lifiwan have no
equivalent gateway/public ICMP decomposition. Passive silence cannot establish
an internet outage; UDP and antenna rotation can lose evidence; historical
source clocks cannot be recovered. These gaps remain visible limitations, not
reasons to run automatic network recovery.
