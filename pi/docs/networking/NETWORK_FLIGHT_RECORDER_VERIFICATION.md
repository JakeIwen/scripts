# Network flight recorder verification

[Operations and semantics](NETWORK_FLIGHT_RECORDER.md) · [Pi documentation index](../../README.md)

Current storage deployment: `network-storage-20261001T102241Z-0dc55a92`, using
verified flash with bounded RAM fallback. The final section records the October
1 permission/replay repair, recovery integrity and current browser checks. The
earlier sections retain the original installation and migration history.

Verification date: 2026-09-30 UTC. Release
`network-20260930T022426Z-10d451d2` was deployed through the checked targeted
installer. The live history view is verified at
`http://vanpi.lan:8788/#network-history?hours=1`, including fresh source coverage,
CLI/UI agreement, a real retained incident and bundle download. Recorder-only
restart, retained-row integrity and steady-state resource checks also passed.
Historical import continues with explicit pending coverage. Isolated test
results below are distinguished from observed live behavior.

## Discovered coverage and implementation boundary

Read-only inspection reached the Pi, router and antenna through existing
access. Source code for the deployed Pi monitor, router probes, antenna manager
and dashboard matched the relevant checkout files. Evidence artifacts are
retained locally under ignored `tmp/network-flight-recorder/`; facts in this
report come from that inspection, not private operational context.

| Evidence already available | Verified state and diagnostic limits |
| --- | --- |
| Durable OpenWrt syslog | Fresh during inspection; approximately 22 MiB with 30 daily generations. Tags contained real gateway/public probe failures, HTTPS failures, authentication, radio, DHCP, mwan3 and resolver observations. Leading RFC3339 was Pi receipt time; old device timestamps were absent. |
| `clientwan-path` | Deployed source matched checkout; existing 5-second sleep plus ping work, transitions and 12-sample summaries. Historical examples independently showed gateway responding with public tests failing and gateway failure. At inspection clientwan was down/disabled, not evidence of a new outage. |
| Per-uplink HTTPS | Existing 30-second sleep plus two bounded requests; mwan3 device/socket marking, shared DNS. WAN returned 204/0 to both tests during inspection. Clientwan/lifiwan were down/disabled. No forced switching or failure was performed. |
| UBNT manager | Existing log was 68,564 bytes in RAM, with `uptime=` records. Boot ID/uptime were readable. Wall date was July 2019 while Pi/router were September 2026, making wall-clock correlation invalid. |
| Pi monitor | Approximately 6.17 GB SQLite database; Pi NTP synchronized. Existing boot/power/resource/service context and report indexes reused read-only, without replacing the monitor or importing its USB storm corpus wholesale. |
| Existing dashboard | Current network, UBNT and system-health views were present; a durable combined network incident/evidence view was absent. |

Added components are one bounded network SQLite database/collector, explicit
incident materialization, read-only CLI/API reports and exports, a dashboard
feature, and an additive Pi syslog spool preserving both clocks. No router or
antenna code, probe budget, routing/failover policy, DNS setting, Wi-Fi setting,
storage/backup policy or CAN tooling was changed for the recorder.

The network database has separate ownership and retention because the existing
system monitor is large and serves independent health-report requirements.
Shared report caches and its covering report index remain intact. Collection
does not depend on dashboard availability.

A final live structured record also demonstrated why reported time is kept
unverified: its receipt was `2026-09-29T20:34:52-06:00`, while rsyslog parsed the
classic source timestamp as `2026-09-30T02:34:52-06:00` (six hours later).
Classic syslog omits timezone; this is consistent with receiver timezone
inference, not proof of device clock drift. Ordering/freshness use receipt time,
and both original timestamp strings remain visible in provenance.

## Acceptance evidence

`pi/tests/network/test_network_flight_recorder.py` uses independently specified
counts, domains, durations and expected end states. The observed fixture is
explicitly labeled `observed_redacted`, with original file/line provenance;
other records are explicitly synthetic. Live connectivity was not interrupted
to create a test case.

| Case | Verification |
| --- | --- |
| Gateway answers; public tests fail | Synthetic failure/recovery asserts `upstream-tests` and a measured short interval. Redacted real September 17/24 log samples independently contain this condition. |
| Gateway failure versus local authentication | Separate gateway/hotspot and local-Wi-Fi episodes, including client handshake recovery. Client identities remain available for local correlation. |
| DNS versus ambiguous HTTPS | Curl exit 6 produces DNS evidence; exit 28 remains an ambiguous HTTPS timeout. Shared DNS is explicitly disclosed. |
| Short interruption, overlap and failover | Independently specified success boundaries, overlapping uplinks, and retained mwan3 transition context. No provider-wide causal attribution. |
| Unsynchronized/corrected clocks and reboot | Wrong source wall clock, forward/backward correction, receiver reversal, boot separation and UBNT uptime-anchor uncertainty. Old import time never becomes incident occurrence time. |
| Collector restart, rotation, partial/gzip/repeated import | Durable IDs/checkpoints, partial-line retry, rotation session inheritance, compressed replay and structured/legacy cutover checks; distinct equal-text records remain distinct. |
| Missing coverage | Long gaps leave unknown end, empty sources are unavailable, old success is stale, source-read errors do not invent a confirmed internet outage. |
| High volume and retention | Full counts with bounded displayed rows; count and age deletion; SQLite main/WAL/SHM combined budget under synthetic pressure. |
| Secrets and browsing exclusion | Separate credential/URL/header/argument forms checked in parsing, persisted rows, reports and exports. Ordinary dnsmasq query/reply/cache records are excluded. |
| Read-only HTTP integration | Real temporary Store → CLI subprocess → Flask returns two observations and one recovered 30-second episode; report/export omit the synthetic secret and leave database modification time unchanged. |

Final focused results after independent-review fixes:

- **53/53 recorder acceptance tests passed**, including redacted observed
  samples, independently expected episode counts/durations, clocks, replay,
  privacy, and CLI reports/exports with all socket connections blocked.
- The same **53/53 recorder cases passed on the Pi** in an isolated temporary
  checkout (64.107 s; peak RSS 47,104 KiB). An initial Pi run hit a Mac-oriented
  15-second bulk-ingestion hang guard at 20.189 s; the portable guard was changed
  to 60 seconds before rerunning. Exact counts, row limits and the under-three-
  second report guard were unchanged. This was a test portability adjustment,
  not a daemon throughput promise or deployed algorithm change.
- **64 new cases passed** across recorder (53), receiver-spool (3) and
  deployment (8) suites, including current-file priority, unchanged antenna
  snapshots and late-backfilled reboot/session corrections.
- Final discrete-fault cases verify that a recent monitoring/resource, UBNT or
  shared-resolver fault without a matching recovery has an unknown end. Fresh
  periodic path/HTTPS/DNS-probe failures may be ongoing; explicit recovery still
  closes an episode. One sparse fault does not establish continuing failure.
- **204/204 Python dashboard tests passed**, including flat deployment and the
  new real CLI/Flask integration. Local isolated Flask environment was used;
  `VAN_DASHBOARD_FRONTEND_ROOT` pointed to the production build.
- **71/71 relevant React tests passed** across 22 files: network history,
  existing network, system health, UBNT, tile order/grid and polling.
- Frontend formatting, TypeScript checking and production build passed.
- **8/8 targeted deployment tests passed** on both Mac (0.044 s) and Pi
  (0.273 s), exercising isolated filesystem/service fakes. A separate read-only
  live manifest check confirmed existing managed files matched HEAD. Those
  checks did not modify live services or files.
- An unrelated baseline `App.test.tsx` expectation still names `COP ALERT`
  while repository HEAD's rendered heading is `STRANGERDANGER`. This was
  reproduced without changing the COP component; it is not a recorder
  regression. The relevant existing feature suites passed.

The complete **53-case recorder suite also passed on the Pi** in an isolated
staging tree: 64.107 seconds, zero failures/errors, peak RSS 47,104 KiB.
An initial run passed all functional/count/report checks but exceeded the
Mac-oriented 15-second bulk-ingestion guard (20.19 seconds for 10,000 records).
That hang guard is now 60 seconds for the supported Pi; exact counts, display
bounds and the three-second report guard remain unchanged. Both run results
are retained in `tmp/network-flight-recorder/pi-acceptance-*.json`. All temporary
Pi benchmark/canary and acceptance databases were removed after verification;
deployment rollback files and the live recorder database remain intact.

## Browser and offline verification

Used `browser_clean` only. The final production frontend ran through the real
Flask application, real CLI subprocesses and a temporary synthetic SQLite
database at a loopback-only test instance. Other dashboard domains were disabled
before controller access in that isolated instance. These are actual stack
tests using synthetic evidence, not claims about a live failure.

- CLI, HTTP and rendered dashboard agreed on **six observations and three
  recovered incidents**: DNS, local Wi-Fi and upstream tests.
- Uplink/device filters returned **two observations and one incident**; search,
  selected ranges, incident details, source/import timing and provenance worked.
- Incident deep links survived reload. A custom 2020 UTC interval showed zero
  observations/episodes without claiming uninterrupted connectivity.
- A 390 × 844 viewport had document width 390 and dialog client/scroll widths
  both 360: no horizontal overflow.
- External HTTP(S) was blocked in the isolated browser while local requests
  continued to return 200. A deliberate external fetch failed as expected.
- Synthetic recorder 503 displayed an unavailable/stale banner while retaining
  qualified old evidence. A separate fake-clock test held a fetch indefinitely
  and verified the old `Recording` badge became `Stale report` after 105 seconds.
- Clean production reloads produced **zero console errors**. Expected network
  errors occurred only when deliberately injecting the external block/503.

Screenshots remain in ignored local artifacts:

- [Actual Flask evidence view](../../../tmp/network-flight-recorder/screenshots/network-history-actual-flask-evidence.png)
- [Actual Flask narrow/custom empty view](../../../tmp/network-flight-recorder/screenshots/network-history-actual-flask-narrow.png)
- [Synthetic unavailable state](../../../tmp/network-flight-recorder/screenshots/network-history-fixture-unavailable.png)

The deployed dashboard was also exercised through `browser_clean`, with
external HTTP(S) blocked except the verified local dashboard/loopback hosts:

- At 01:57:37 UTC, all eight source/probe coverage rows were current: OpenWrt,
  system monitor, UBNT, recorder, clientwan path and all three HTTPS streams.
- The fixed interval **2026-09-30 01:54:00–01:57:00 UTC** produced exactly
  **10 observations / zero incidents** in the rendered view, HTTP API and SSH
  CLI, with identical event IDs. This proves report consistency, not universal
  connection health.
- Real retained UBNT incident `6e549b26183cdfab1308525b` opened with
  `unknown-end` and `monotonic-anchor-approximate` evidence. Its deep link
  survived reload; the 390 × 844 viewport again had no horizontal overflow.
- The actual download button saved a valid 7,705-byte schema-1 bundle with one
  observation, eight source-coverage entries, provenance and all clock
  semantics. It did not invent a recovery or exact antenna wall-clock time.
- History requests returned 200 and produced no JavaScript errors. The live
  page had one unrelated Sonos album-art request returning 502; it
  did not affect history requests or evidence rendering.

Live screenshots: [wide incident evidence](../../../tmp/network-flight-recorder/screenshots/network-history-live-incident-wide.png)
and [narrow incident view](../../../tmp/network-flight-recorder/screenshots/network-history-live-incident-narrow.png).

The final update was rechecked in the live browser at 02:13 UTC: eight current
source/probe rows plus a ninth `historical-import` row marked **unknown**.
Its expanded explanation reported 24 of 32 source files still unread/partial;
the one-hour report showed 96 observations and two incidents. Original receipt
offset metadata was present, for example a `receipt_time_raw` value ending in
`-06:00`. History remained usable with external HTTP(S) blocked; the same
unrelated Sonos artwork 502 was the only console error. The test browser was
closed afterward to stop its ordinary dashboard polling.
[Final live coverage screenshot](../../../tmp/network-flight-recorder/screenshots/network-history-live-final-coverage.png).

After the final discrete-fault status update, a live monitoring incident
(`0bf14e2d8f9f650b50b3fcb0`) rendered **End unknown**; its API detail agreed on
`status=unknown-end`, `end=null` and the monitoring domain. The final browser
smoke passed, then the test page was closed. Fresh discrete-fault versus
periodic-probe distinctions are additionally covered by the focused fixtures.

## Measured bounds and overhead

| Measurement | Result and scope |
| --- | --- |
| 50,000 synthetic observations, independently expected 5,000 recovered episodes | Exactly 50,000 events / 5,000 incidents. Final correlation-v2 M4 MacBook Python 3.13 ingestion 6.322 s; full-range reports with 200 displayed events/incidents took 86.6–89.4 ms; bounded 50-observation export took 49.3 ms. Main database plus sidecars: 43,036,672 bytes, including receipt-offset provenance and the additional correlation index. |
| Isolated Pi first collector pass | 15.124 s wall / 9.631 s user CPU for initial bounded syslog backlog, existing monitor import and antenna read. This is startup/backfill work, not steady-state overhead. |
| Deployed Pi initial service pass | All four collector source groups became current by 01:56:32 UTC. After approximately 120 s under the 20% CPU quota: 22.24 CPU-seconds, RSS 23,516 KiB, 5,436 imported observations. Startup/backfill work, not steady-state overhead. |
| Live Pi report latency | 1-hour report 139 ms; 24-hour report 181 ms; 30-day report 429 ms for 8,719 observations / 276 incidents, displaying 200 observations. Database 8,318,976 bytes at that snapshot; historical backfill still in progress. |
| Later live Pi report | 35,541 observations / 788 incidents in 660 ms; database/sidecars 32,388,216 bytes and recorder RSS 22,028 KiB. Backfill remained in progress. |
| Pi 50,000-observation isolated fixture | Exactly 50,000 observations / 5,000 episodes; ingestion 134.852 s; three full-range reports 767–833 ms; database/sidecars 39,358,464 bytes; benchmark process RSS 78,340 KiB. Separate temporary database, no live network interruption. |
| Pi steady-state isolated copy | 61.131 s of actual passive current-source work used 0.435 CPU-seconds including SSH children (approximately 0.71% of one core), added eight observations, and used RSS 22,836 KiB. Ordinary passes took 16–161 ms; two minute-context passes took 1.12–1.15 s. Database/sidecar allocation changed by −28,888 bytes through page/WAL reuse; this short sample is not a long-term byte/event growth rate. |
| Actual spool rotation test on Pi rsyslog 8.2302 | At a reduced 4 KiB threshold, a 450-message synthetic burst left seven files totaling 30,919 bytes. Verified both clocks, JSON escaping, ordinary DNS/login exclusion and fail-closed rotation. Production uses 5 MiB × seven files, with complete-message/batch overrun. |
| Database count/age test | Inserting 1,500 records into a 1,000-event limit retained exactly 1,000; advancing the retention clock by two days with a one-day policy removed all events/incidents. |
| Database byte-pressure test | 4,000 large synthetic authentication observations under a 4 MiB main/WAL/SHM budget retained 250 observations using 1,232,896 physical bytes, within the configured total budget. |
| Production UI build | 203 modules; JS 461.77 kB / gzip 130.33 kB; CSS 87.28 kB / gzip 14.65 kB. No external assets or hosted analysis. |

Mac and Pi measurements are listed separately; throughput is not assumed to be
interchangeable. The first live deployment exposed startup/backfill latency
under the CPU quota. The deployed follow-up reduces each poll to 512 KiB total /
128 KiB per file, prioritizes current JSON records, skips unchanged antenna
snapshots and explicitly exposes historical import as pending/unknown.
The steady-state measurement advanced archive offsets **only in a temporary
database copy**, then performed current passive reads at their existing
cadence; production checkpoints were unchanged. An earlier contaminated canary
that still imported legacy backlog is excluded from the reported steady-state
figures. Production backfill completion is not claimed.

The Pi kernel exposes no memory cgroup controller. `MemoryMax=128M` is
configured but `MemoryCurrent` is unset, so no enforced 128 MiB memory bound is
claimed. The CPU quota and explicit database, spool and source-read bounds are
active. Kernel/boot configuration was not changed.

## Completion and remaining gaps

### Follow-up after several hours: 2026-09-30 07:03 UTC

Read-only live checks confirmed the same recorder process had run for about
4 hours 37 minutes with zero automatic restarts and no service failures.
All eight source/probe coverage entries were current, with evidence/receipt
ages below 60 seconds. Historical import was caught up: every currently
available generation was checkpointed.

The preceding four hours contained 1,763 router observations, 240 collector
heartbeats and four antenna observations (its healthy log is hourly). The
mean heartbeat interval was 60.01 seconds; the longest was 70.51 seconds.
The database held 59,810 events, passed SQLite `quick_check`, and occupied
61.6 MiB including sidecars; recorder RSS was 23.5 MiB. A six-hour report took
0.60 seconds. CLI and dashboard API agreed exactly on a fixed one-hour range
(624 observations, 22 incident episodes and identical displayed event IDs).
Incident export still returned bounded evidence with provenance. The live
browser rendered the timeline and current coverage; its only console error
was an unrelated Sonos album-art HTTP 502.

Storage-pressure retention had evicted 98,986 older imported observations;
the oldest retained event was 2026-09-23 13:05:10 UTC, approximately seven days
of searchable history at this volume. The configured 30 days is an upper age
limit, not a guaranteed retention span. Original source-log retention is
unchanged. Evidence for this follow-up is in the ignored local artifact
`tmp/network-flight-recorder/check-after-hours-20260930.json`.

### Deployment verification at installation

The final targeted update installed release
`network-20260930T022426Z-10d451d2`. The preceding update was
`network-20260930T021028Z-87126f5d`, and the initial recorder release was
`network-20260930T015418Z-e164e3ea`. Saved rollback files for each
are under `/var/lib/vanpi-network-deploy/releases/RELEASE_ID`.
The new live history view loaded successfully, all available sources became
current, and fixed-interval CLI/UI comparison plus real-incident export passed.
A recorder-only restart stopped gracefully in 7.855 seconds, started a new PID
and collector session, resumed eight current source/probe streams, retained
schema version 1 and reported zero automatic restarts. Network interfaces and
uplink selections were not restarted or changed.

The pre-restart prefix contained 28,987 rows; afterward it contained 28,984.
Independent comparison against the pre-restart database copy established that
all 28,984 retained rows were byte-for-byte identical. The three removed rows
had expired beyond the verified 30-day cutoff; the count difference was
retention, not restart loss. SQLite `quick_check` passed. Detailed local
measurements are in `tmp/network-flight-recorder/restart-verified.json`,
`pi-measurements.json` and `steady-canary-final.json`.

At the original installation check the recorder was enabled and running.
Initial historical import remained explicitly pending; its source-status snapshot showed 12 of 32 files still unread or
partial while current-source receipts continued. Existing history counts can
increase during catch-up. This is normal bounded background ingestion, not a
claim that old intervals are complete.

These historical recorder rollback commands apply only **after storage
rollback**; the legacy installer now refuses them while storage configuration
is present. Undo the last original recorder update from the repository root:

```bash
python3 pi/deploy_network_flight_recorder.py rollback --release network-20260930T022426Z-10d451d2
```

To return all the way to the state before this project, first run that command,
then roll back the two preceding releases in this order:

```bash
python3 pi/deploy_network_flight_recorder.py rollback --release network-20260930T021028Z-87126f5d
```

```bash
python3 pi/deploy_network_flight_recorder.py rollback --release network-20260930T015418Z-e164e3ea
```

Unresolved observability limits are explicit in every bundle: no every-client
association/end-to-end proof; no WAN/lifiwan gateway/public decomposition;
unknown UDP loss and potential antenna rotation loss; unrecoverable historical
source timestamps; approximate UBNT anchoring; bounded context/import/retention;
and no exact cross-device causal order. No automatic recovery is implemented.

## Flash storage and RAM fallback follow-up

Storage migration was deployed as `network-storage-20260930T090204Z-e02b828b`.
The active database is `/mnt/EXFAT512/vanpi-network/events.sqlite3`; router
history is mirrored on that verified flash volume, with bounded volatile
spooling/fallback under `/run/vanpi-network/`. The former SD database/log
directories remain frozen rollback copies. Writer-descriptor/SD-stability
and Borg dry-run checks passed. No real USB unmount or
CAN-recorder interruption was used to test absence.

The backup that started at 03:00 local time was already in its media-rsync
phase before migration and was left running. Its already-loaded exclusion
array may include the frozen SD copies once; future backup processes load the
new narrow exclusions. No backup job or production Borg repository was reset.

Dashboard integration is implemented and tested:

- Default CLI selection is `--database auto`; explicit database/environment
  overrides remain supported. The storage configuration and active-tier status
  select the database, rather than a hardcoded boot-SD path.
- The tile reports **Storage warning** during fallback. Timeline and incident
  views show an uncollapsed warning that RAM evidence is volatile, disappears
  on reboot, and cannot establish the contents of unavailable flash history.
- Evidence exposes stable `record_id` alongside the explicitly local numeric
  database row ID. Aliased incident links remain compatible across storage
  migration and RAM replay.
- **13/13 dashboard history backend cases passed on the Pi**, including a real
  CLI/Flask path using a unique `/dev/shm` fixture and absent flash UUID. It
  reported two RAM observations/one incident, rejected a valid SQLite shadow
  beneath an unmounted path, and returned unavailable when the explicitly
  selected configuration was removed. This exercised real Linux mount guards;
  the Linux-only case is skipped on macOS.
- **74/74 relevant React cases passed**, including warning visibility on the
  closed dashboard tile, timeline and direct incident details. Formatting,
  TypeScript checking and the production build passed.
- The full local dashboard suite completed **207 cases: 206 passed and one
  Linux-specific case skipped**. That skipped case passed in the real Pi run
  described above.

Storage acceptance also passed **28/28 cases on the Pi** in an isolated
temporary checkout (45.918 s test runtime; peak RSS 42,576 KiB). Cases cover
mount identity, absent/read-only paths, prevention of SD fallback, bounded RAM,
restart/replay and overflow, interrupted merges, retained readers, incident
aliases, exFAT permissions, automatic selection and service/Borg guards. No live
mounts, services or probes were changed by that suite. An additional canary on
the actual flash and tmpfs filesystems used simulated availability loss only:
four observations/two incidents, stable IDs, restart replay and SQLite
integrity all passed in 1.505 s.

`browser_clean` exercised the production frontend through an isolated Flask
instance invoking the actual staged Pi CLI and temporary tmpfs database. With
external browser HTTP(S) blocked, the warning remained visible without
expanding coverage details; counts were two observations/one incident and
physical record identities were displayed. The 390 × 844 viewport had document
width 390 and dialog client/scroll widths 360; no horizontal overflow or console
errors occurred. This was an **absent-flash fixture**, not a live USB removal.
The test server/browser were stopped and only the owned RAM fixture was removed.

[RAM warning, wide view](../../../tmp/network-flight-recorder/screenshots/network-history-ram-storage-warning-wide.png) ·
[RAM warning, narrow incident view](../../../tmp/network-flight-recorder/screenshots/network-history-ram-storage-warning-narrow.png).

Live migration and browser verification:

- All **60,743 pre-migration observations** were preserved, with none missing
  or changed. The migrated database passed SQLite `quick_check`; **1,383 legacy
  incident aliases** were retained.
- A final comparison checked **every stored event column** against the frozen
  original and again found zero missing or changed observations. At that check
  the flash database contained 60,803 observations and a report took 1.01 s.
- The report reader and HTTP API agreed on **329 observations / four
  incidents** over **2026-09-30 08:05:06–09:05:06 UTC**, including matching
  physical record identities. The HTTP adapter invokes the CLI with automatic
  storage selection. The initial flash report took 1.44 s. The measured flash data
  footprint was 87,275,310 bytes; runtime RAM data was 51,907 bytes at that
  snapshot. These are observed values, not retention guarantees.
- At 09:10 UTC, the live browser showed **all ten coverage entries current**,
  including storage and historical import. The 24-hour view reported 8,496
  observations and 116 incidents; no volatile-storage warning was shown.
- Legacy deep link `6e549b26183cdfab1308525b` resolved transparently to canonical
  incident `02c28d8d18539791f546f2cf`, preserving its unknown-end state. Its 64
  evidence/context observations exposed stable physical record identities.
- Expanded evidence remained readable at 390 × 844 with document width 390 and
  dialog client/scroll widths 360. History requests succeeded with external
  HTTP(S) blocked. No history JavaScript errors occurred; an unrelated Sonos
  artwork request still returned 502. The browser was closed after the check.

The final write-location check observed **36 retired SD files for 65.159
seconds**: no size or modification-time changes, and no managed recorder
writer descriptors targeting SD. Open database/WAL/SHM descriptors pointed to
the verified flash filesystem; the collector lock pointed to tmpfs. Meanwhile,
the observation count increased from 60,812 to 60,817 and import time advanced,
showing that the recorder was still collecting. This verifies managed recorder
data paths; it does not claim that unrelated Pi services make no SD writes.

An actual Borg dry run using the deployed exclusion array and an isolated
tmpfs repository/cache exited successfully. Traversal was bounded to the
verification paths and a positive SD control, `/etc/hostname`, which was
selected; unrelated trees were pruned from this test walk. Both retired SD paths were
explicitly excluded, and zero descendants of `/mnt/EXFAT512` or `/run` were
selected from the root source. Borg represented the mountpoint directories as
metadata (`-`), which is distinct from including their data. No archive was
created and the production Borg repository
was untouched. The already-running pre-migration backup caveat above still
applies.

Evidence artifacts: `tmp/network-flight-recorder/flash-deployment-verified-all-fields.json`,
`flash-runtime-canary.json`, `storage-acceptance-JvaGux.json`,
`storage-write-locations-verified.json` and
`borg-storage-exclusions-verified.json`.

[Live flash coverage](../../../tmp/network-flight-recorder/screenshots/network-history-flash-live-storage.png) ·
[Legacy incident on a narrow viewport](../../../tmp/network-flight-recorder/screenshots/network-history-flash-live-legacy-narrow.png).

### Final timer correction

The initial storage version had one observed automatic restart at **09:20:47
UTC**, with a `ValueError`; collection recovered by **09:20:53 UTC**. The old
journal recorded no code frame, so the precise origin of that occurrence is
not proven. Do not interpret the earlier zero-restart snapshots as a claim
that the entire storage observation period had no automatic restart.

Review reproduced a concrete race in both collector wait loops: preemption
between two monotonic clock reads could produce a negative sleep duration.
Both loops now use a shared bounded wait with one clock read per calculation.
Seven focused regression cases cover preemption and error reporting; CLI
failures now retain only filename/function/line and error type, excluding
exception messages and command arguments that could contain secrets.

The fix was deployed as `network-storage-20260930T093830Z-fba885dc`, active at
**09:40:16 UTC**, with migration disabled: data was not recopied. The new
process initially reported zero automatic restarts; the final bounded stability
sample then kept the same PID (3592207) active for **120.040 seconds**, with
**zero additional automatic restarts**. Flash mode remained selected, storage
check timestamps advanced, and all ten source/probe coverage entries were
current afterward. This is a bounded post-fix observation, not a guarantee of
future uptime or a claim that the earlier restart did not occur. Evidence is in
`tmp/network-flight-recorder/flash-stability-after-timer-fix.json`.

The combined local run passed **110 cases with
four platform-specific skips**, including **60 recorder cases**. The earlier
28-case isolated Pi storage run remains applicable; no UI code changed for this
fix, so the previously verified flash browser flows remain valid.

The following commands describe the September 30 release chain. Later
recorder-only updates must be rolled back first; use the current chain in
[operations](NETWORK_FLIGHT_RECORDER.md#targeted-installation-update-and-rollback).

Historical update rollback, preserving flash storage:

```bash
python3 pi/deploy_network_storage.py rollback --release network-storage-20260930T093830Z-fba885dc
```

To undo flash storage entirely, first run that command, then roll back the
original migration:

```bash
python3 pi/deploy_network_storage.py rollback --release network-storage-20260930T090204Z-e02b828b
```

Both steps require verified flash and consolidate pending RAM history. The
second step restores the preceding SD-backed service using current consolidated
history, while retaining the flash copy. The original recorder rollback commands
above remain guarded until the storage migration rollback completes.

## October 1 RAM warning: permissions, replay and recovery

Current release: **`network-storage-20261001T102241Z-0dc55a92`**. At the final
10:24–10:25 UTC checks, the recorder selected verified flash, reported no
storage error or pending replay, and received fresh router evidence. The
recorder, OpenWrt and historical-import coverage entries were current. This is
observed live state, separate from the isolated failure fixtures below.

The warning was real. The flash volume was mounted read/write with 39 GiB free,
but rotated RAM spool files were `root:root 0640`; the collector runs as `pi`
with supplementary `adm` and received `PermissionError` opening them. The old
storage path swallowed that error and retried in RAM. A separate root receiver
and unprivileged reader reproduced the failure with the Pi's rsyslog 8.2302,
global file mode `0640` and umask `0022`. The earlier same-user fixture did not
test this ownership boundary.

The spool now inherits `adm` through a `02750 root:adm` directory. Installation
repairs only the eight active/rotated files through pinned, validated file
descriptors. Actual tmpfiles `z` testing rejected the ownership hierarchy, so
no such rules were deployed. The real repair preserved all eight contents and
left `.4` sentinel files untouched; repeated isolated rotations subsequently
remained readable. Status/coverage now expose safe error categories and
operations, with transition-only journal messages and no exception payloads.

Before repair, a SQLite backup and all surviving raw generations were preserved
on verified flash under `ram-recovery-20261001T0945Z/`. Recovery exposed a second
defect: incoming raw backlog could evict RAM observations between replay batches.
Replay now drains existing observations before further source import or RAM
pruning; raw reception and mirroring continue. Failure resumes RAM collection.
The 164 prematurely evicted observations were restored from that safety copy
under the recorder's writer lock. **All 3,242 saved fingerprints are present on
flash.** All fields match for the 3,241 newly recovered records. One record was
already present before repair and retains its earlier receipt/import/backfill
metadata; that difference was measured before recovery. SQLite quick-check
passed and repeated physical records were not duplicated.

Router evidence remains missing between **2026-09-30 13:21:25.674 UTC and
22:19:14.244 UTC** (about nine hours). Those older raw generations had already
rotated away before inspection. This is a monitoring coverage gap, not proof
of a network outage; other source observations survived. Current historical
import completion does not imply that missing generations were recovered.

Validation and operational evidence:

- **151 local tests run: 145 passed, six platform skips.** All 28 installer
  cases passed on the Pi. The two new replay-order cases also passed on the Pi
  in 17.6 seconds with 37.9 MiB peak RSS, using only `/run` fixtures. They verify
  1,001 pending records survive a subsequent 1,001-record burst, and failure
  during replay resumes RAM capture then recovers all 1,002 records.
- The initial update's 180-second readiness allowance expired during healthy
  replay under the unchanged 20% CPU quota. Its automatic code rollback kept
  evidence and repaired live permissions. Readiness now allows a bounded ten
  minutes, with a 220-second fixture. The retry completed; the final replay-order
  update became ready in 4.1 seconds with zero pending RAM records.
- Recorder-only deployment preserved the receiver, dashboard, preview and CAN
  recorder PIDs/states. Retired SD file sizes/mtimes and the checked receiver,
  backup and dashboard-adapter hashes remained unchanged. No routing, mount,
  probe, backup-policy or network-interface changes were made.
- CLI and HTTP reports for the same fixed ten-minute interval agreed on
  **84 observations and two incidents**. Measured latency during final checks
  was 1.37 seconds for CLI and 2.91 seconds for HTTP. The database was about
  61.5 MiB; existing limits remain in effect.
- `browser_clean` verified the live history modal at
  `http://vanpi.lan:8788/#network-history?hours=24`, including a 390-pixel
  viewport. The RAM warning cleared and history requests returned HTTP 200.
  The only console error was the pre-existing, unrelated Sonos artwork 502.

Ignored evidence artifacts are under `tmp/network-flight-recorder/`: the
rotation ownership/setgid JSON files, replay-order Pi results, before/final
storage snapshots, snapshot restoration result, CLI/API agreement, final
deployment result and desktop/mobile screenshots. The safety copy remains on
flash; it is a one-time recovery artifact, not a growing log stream.

Current update rollback, preserving evidence and flash storage:

```bash
python3 pi/deploy_network_storage.py rollback --release network-storage-20261001T102241Z-0dc55a92
```
