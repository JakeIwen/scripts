# Pi Python deployment

The owner performs the live cutover after review/merge. Local tests and dry-runs
are not evidence of Pi health. **Never delete or write the existing
`/home/pi/scripts/python-automation/` tree:** it is the frozen flat rollback copy.

## Architecture and ownership

`pi/deploy_python.py` resolves its checkout from `__file__`, records Git commit,
branch, checkout and dirty status, and transfers an allowlisted source release
with one SSH invocation to `pi@vanpi.lan`. `--dry-run` and `--list-units` are local
only. Only systemd operations and unit installation use sudo; it does not install
dependencies. Review dirty sources and provenance before deploying.

Allowlisted inputs are immediate Python modules in:

- `pi/apps/{audiobooks,bme280,van_dashboard,video_library}`;
- `pi/apps/van_dashboard/routes` and `pi/apps/video_library/players`;
- `pi/scripts/python` and `shared/python`;
- explicit `pi`, `pi/apps`, `pi/scripts`, and `shared` initializers plus
  `pi/package_runtime.py`;
- video `templates/video_library.html` and
  `static/{video_library.js,video_library.css}`;
- the four units named in the installer's `SERVICES` table.

There is no recursive transfer of `pi/`. Secrets, configs, tests, frontend builds,
node_modules, caches, bytecode, system-monitor code and `pi/van_compute/` are not
inputs. Source symlinks are rejected. Python 3.11+ is required, including the
existing BME280 venv (currently 3.11.2).

```text
/home/pi/scripts/python-packages/
  releases/<24-hex-digest>/manifest.json
  releases/<24-hex-digest>/{pi,shared}/...
  current -> releases/<digest>
  previous -> releases/<prior-digest>
  .install.lock
  activated.json                            # dashboard/host activation gate
  service-state/<unit>.json                  # completed service dependency digest
  pending-restarts/<unit>.json               # durable reload/restart intent
  pre-package-units/<unit>.backup/<unit>     # first flat unit, saved once
  pre-package-units/<unit>.backup/<unit>.d/   # effective local drop-ins, if any
  pre-package-units/van-dashboard.service    # earlier dashboard backup, preserved
/home/pi/scripts/python-automation/          # frozen recovery copy, never written
```

The receiver validates regular-file paths, hashes, completeness and Python syntax
before publishing an immutable release. It serializes installation and collection
under `.install.lock`. `current`/`previous` are atomically replaced. Identical
inputs/provenance verify the existing release; provenance-only differences can
publish a release without restarting any service.

### Consumers

| Consumer | Owner and runtime |
| --- | --- |
| `van-dashboard.service` | Package; `/usr/bin/python3 -P -m pi.apps.van_dashboard`; port 8788. Compute metrics remain separately installed in `/home/pi/van_compute/scripts`. Existing relay-off hook, GPIO group and network-storage drop-in are preserved. |
| `video-library.service` | Package; `/usr/bin/python3 -P -m pi.apps.video_library`; port 8789. Original DISPLAY, DBUS, Sonos/VLC and data/migration behavior is unchanged. |
| `audiobooks.service` | Package; `/usr/bin/python3 -P -m pi.apps.audiobooks`; port 8787. |
| `bme280-mqtt.service` | Package; `/home/pi/pyvenv/bin/python -P -m pi.apps.bme280`; existing sensor/MQTT environment. |
| `pi/.bashrc` | Package-root/utility PYTHONPATH and current-release `ipinfo`, VLC and Sonos helper paths; installed by broad sync. |
| `pi/sns.sh` | Sonos wrapper; preserves an inherited import path (including the saved flat video unit on rollback) and supplies package utility paths for clean interactive use. No Sonos command semantics change. |
| `pi/scripts/log_position.sh` | Calls the current release's VLC helper; installed by broad sync. |
| `van-dashboard-preview.service` | Independent frontend/preview current release, not the staged package copy of `react_dashboard_preview.py`. Use its existing preview deployer. |
| `system_event_monitor.py` and `system_monitor/`, other script services/cron/timers | Coherent broad-sync layout under `/home/pi/scripts`. No package-release conversion in this change. Keep Rank 3 flat/package import guards. |
| `van_compute` | Coupled independent Mac/Pi installer; no package copy. |
| historical `raspbian_setup.sh` and archived path-repair tools | Not package bootstrap or deployment paths. Do not use them to refresh the frozen flat directory. |

`-P` removes the unsafe working-directory import entry. Each unit sets
`PYTHONPATH=/home/pi/scripts/python-packages/current`; dashboard additionally
includes compute, video additionally includes `current/shared/python` for the
`sns.sh` subprocess. `pi.__path__` (and `shared.__path__`) pins the resolved release
at import time. Bytecode writing is disabled for all four services and the shell
utility entrypoints, so interactive imports cannot dirty releases with `__pycache__`.

The video unit's existing Python/asset prechecks now point into the package tree,
including nested `players/` and `shared/python/sonos_tasks.py`. Its `/home/pi/sns.sh`
and `/usr/bin/vlc` checks and DISPLAY/DBUS settings remain unchanged. Audiobooks
and BME280 gain PYTHONPATH, no-bytecode and private runtime-directory settings.
All three gain a record-writing `__main__.py`; application logic is unchanged.
`players/` remains a namespace subpackage; all video flat-import guards remain.
The dashboard unit is byte-for-byte unchanged; its record writer moves into the
shared helper, changing its dependency digest and causing one active restart.

Each service has its own digest: its entire allowlisted app (video includes assets
and players), common Pi initializers/record helper, and for video the Sonos helper
and shared initializers. The independent preview module is excluded from the
dashboard digest. A unit-only change reloads/restarts that service. An unrelated
app change does not restart it. Selection with `--service` affects unit installation
and restarts, not release contents: all allowlisted sources are staged and the
shared `current` changes. Utilities use current at each invocation.

## Cutover and retry policy

Default deployment **stages only**. First switch of **each** flat service requires
`--activate --service <unit>`; ordinary `--update` refuses an unactivated or
flat-restored selected service. This explicit gate makes the supervised service
order deliberate and prevents a future broad sync from undoing a rollback.
Omitting `--service` selects all four; the option may be repeated.

Before any link/unit changes, all selected services are checked. First activation
requires the exact expected flat ExecStart interpreter and script, not a comment
containing a familiar pathname. The live unit and effective local drop-ins are
saved once. Symlinked units/drop-ins, nonlocal or stale effective drop-ins and
drop-ins overriding package launch/runtime settings are refused for operator
review, never silently ignored. Existing unrelated drop-ins are left untouched.
The dashboard's older `activated.json` state is adopted from that marker's release
manifest (not the possibly advanced shared current link) without backing up its
package unit over the original flat backup.

Changed active services restart; deliberately inactive services stay inactive,
**even on first activation**. A restart intent is persisted before changing
`current` or copying any selected unit. A failed reload/restart leaves per-service
pending state; repeat the same activation/update command. A formerly active
service still retries if the failed restart left it inactive. Successful services
save their own digests immediately, so a failure later in a multi-service deploy
does not cause completed services to restart again. Matching unit bytes do not
hide an unfinished daemon-reload. Existing dashboard `pending-restart` is migrated
on retry. Do not erase pending state to claim success; use the rollback below.

### Broad sync and scoped video deployment

`pi/sync_scripts.sh` remains pinned to `/Users/jacobr/dev/scripts`, the primary
trusted checkout, because it publishes ignored private inputs. Its read-only
preflight requires `activated.json` and all four service-state markers before any
upload. The block remains until the supervised service cutovers complete and
returns after a rollback. Do not bypass it.

After the preflight, sync waits for home files/hooks/secrets/Samba/tmpfiles/scripts
and non-package units, then runs **only** `deploy_python.py --update`, then the
conditional compute installer last. The package `SERVICES` table supplies the
local `--list-units` exclusion list; generic service staging cannot install any
of the four package units. The independent storage exclusion file continues to
protect recorder/storage ownership. `update_services.sh` is otherwise unchanged.

`--legacy-flatten` is retained only to fail with an explicit retired/frozen-tree
error, including dry-run; its mapping and copy implementation are gone.
`pi/deploy_video_library.sh` also refuses without connecting and points to the
package installer. Its old `--rollback` to v1 is deliberately retired: rewriting
the only frozen fallback or installing a flat unit behind package state is not a
safe escape hatch. Use saved-unit rollback instead. Video database rollback and
v1 migration recovery are separate from package rollback; neither this cutover
nor its rollback restores, replaces or deletes a media database.

## Orchestrator cutover checklist

Run these commands **after merge**, from the primary Mac checkout. No commands in
this checklist were run against the Pi by the implementation agent. Stop other
installers/factory smokes first. Recheck live units, dependencies and health; this
runbook is not evidence they match the recorded 2026-10-03 state.

```bash
cd /Users/jacobr/dev/scripts
git status --short
python3 pi/deploy_python.py --dry-run
python3 pi/deploy_python.py --dry-run --update
ssh pi@vanpi.lan 'for u in van-dashboard video-library audiobooks bme280-mqtt; do /usr/bin/systemctl cat "$u.service"; /usr/bin/systemctl is-active "$u.service"; done'
ssh pi@vanpi.lan '/usr/bin/python3 --version; /home/pi/pyvenv/bin/python --version; test -r /home/pi/sns.sh; test -x /usr/bin/vlc; test -r /home/pi/scripts/python-automation/video_library_server.py; test -r /home/pi/scripts/python-automation/audiobook_server.py; test -r /home/pi/scripts/python-automation/bme280_mqtt.py'
python3 pi/deploy_python.py
```

Expect the plan to name all four units and sources solely under this checkout.
Staging prints `RELEASE .../releases/<digest>`, restarts nobody and does not switch
current. Review its provenance/dirty flag. Check Python >=3.11 in both environments.
Do not start parallel media/sensor production workers as a smoke.

**1. Dashboard migration/update** (already packaged; no new flat cutover).

```bash
python3 pi/deploy_python.py --update --service van-dashboard.service
ssh pi@vanpi.lan '/usr/bin/systemctl is-active van-dashboard.service; /usr/bin/journalctl -u van-dashboard.service -n 60 --no-pager'
ssh pi@vanpi.lan 'curl --retry 10 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:8788/api/status; curl -fsS http://127.0.0.1:8788/manifest.webmanifest'
```

Expect `active`, normal port-8788 startup and unchanged safety/controller health;
`restarted` includes dashboard once because its entrypoint/helper changed. Do not
arm COP/relay as a deployment test. Existing external-service errors require
comparison with the pre-deploy baseline, not automatic attribution to packaging.

**2. Video first switch**, then verify before proceeding.

```bash
python3 pi/deploy_python.py --dry-run --activate --service video-library.service
python3 pi/deploy_python.py --activate --service video-library.service
ssh pi@vanpi.lan '/usr/bin/systemctl is-active video-library.service; /usr/bin/journalctl -u video-library.service -n 60 --no-pager; curl --retry 10 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:8789/api/status'
```

Expect `active`, HTTP 200 JSON on 8789, no import/precheck errors, and a saved
`pre-package-units/video-library.service.backup/video-library.service`. Confirm
normal UI/library/resume state. Sonos routing, desktop VLC playback and audio
volume need an intentional owner playback test, not an automated mutation.

**3. Audiobooks first switch**.

```bash
python3 pi/deploy_python.py --activate --service audiobooks.service
ssh pi@vanpi.lan '/usr/bin/systemctl is-active audiobooks.service; /usr/bin/journalctl -u audiobooks.service -n 60 --no-pager; curl --retry 10 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:8787/ > /dev/null'
```

Expect `active`, HTTP 200 on 8787 and unchanged audiobook/resume state.

**4. BME280 first switch**.

```bash
python3 pi/deploy_python.py --activate --service bme280-mqtt.service
ssh pi@vanpi.lan '/usr/bin/systemctl is-active bme280-mqtt.service; /usr/bin/journalctl -u bme280-mqtt.service -n 100 --no-pager'
ssh pi@vanpi.lan "/usr/bin/timeout 30 /home/pi/pyvenv/bin/python -c 'import paho.mqtt.subscribe as s; [print(m.payload.decode()) for m in s.simple(\"vanpi/sensors/vanpi_bme280_1/state\", hostname=\"127.0.0.1\", msg_count=2, retained=False)]'"
```

Expect `active`, no import, I2C, broker connection or restart-loop errors, and two
fresh JSON readings with temperature, humidity and pressure roughly ten seconds
apart. The existing publisher does **not** log each publication, so clean journal
lines alone are not proof; the subscription deliberately ignores retained samples.
A timeout or missing samples is a failed verification. Preserve the existing
venv/interpreter.

**5. All records, idempotence, then utility/home-file sync**.

```bash
ssh pi@vanpi.lan 'for u in van-dashboard video-library audiobooks bme280-mqtt; do /usr/bin/systemctl show -p ActiveState -p MainPID -p InvocationID "$u.service"; /usr/bin/python3 -m json.tool "/run/$u/package-release"; done'
python3 pi/deploy_python.py --update
bash pi/sync_scripts.sh
ssh pi@vanpi.lan '/usr/bin/readlink -e /home/pi/scripts/python-packages/current; /usr/bin/readlink -e /home/pi/scripts/python-packages/previous'
```

Each active record must match systemd PID/invocation and name
`.../releases/<digest>/pi`. The repeated identical update should report
`"restarted": []`. Broad sync now supplies the utility shell edits without
flattening anything. It may independently update non-Python dependencies/compute;
review their own validation. Expect no package-record warnings. Before broad
sync, the old interactive helpers intentionally still use the frozen flat copy;
the package video unit already supplies the Sonos path, so it has no prerequisite
on a new `.bashrc` or `sns.sh`.

If any step fails, stop and inspect logs. New tracebacks, import errors, missing
prechecks, ownership errors, restart loops or API regressions are rollback
triggers. Retry only understood transient reload/restart failures.

## Per-service flat rollback

These exact commands run from the primary Mac checkout. **Select exactly one**
service; repeat for others only if needed. They hold the installer lock, verify
the saved unit contains the expected flat command, restore it, retire only that
service's activation/retry state, reload systemd and restart it. No release links,
flat files, application data or frontend assets are changed.

For video:

```bash
cd /Users/jacobr/dev/scripts
UNIT=video-library.service
FLAT=video_library_server.py
```

For audiobooks instead:

```bash
UNIT=audiobooks.service
FLAT=audiobook_server.py
```

For BME280 instead:

```bash
UNIT=bme280-mqtt.service
FLAT=bme280_mqtt.py
```

For dashboard instead (also retires the host gate):

```bash
UNIT=van-dashboard.service
FLAT=van_dashboard.py
```

Then execute this single command (not a heredoc):

```bash
ssh pi@vanpi.lan "bash -s -- '$UNIT' '$FLAT'" < pi/rollback_python_service.sh
```

The reviewed rollback script restores `pre-package-units/<unit>.backup/<unit>`;
for the previously converted dashboard it accepts the original
`pre-package-units/van-dashboard.service`. It prints the retired state directory
and restored unit. Check `active`, logs and the relevant HTTP/MQTT checks above.
Broad sync is now blocked until explicit reactivation; an ordinary package update
also refuses the flat unit even if someone forgot to retire its state marker.

The installer never edits drop-ins. Their saved copies are recovery evidence;
leave current drop-ins in place, especially the independently owned dashboard
network-storage configuration. The rollback script requires them to match the
saved copies when a new-style backup exists; if a separate owner changed them,
stop and reconcile that owner's version instead of overwriting it. For the older
dashboard backup, review current versus saved drop-ins before rollback. Never
concurrently run package rollback and a network or other service installer.
After diagnosis, run `--activate --service "$UNIT"` explicitly and reverify; the
original flat backup is never replaced.

## Automatic release retention

Every successful stage/activate/update attempts best-effort GC under the same
exclusive lock. It keeps current, previous, just-installed, the newest three
(by directory mtime, digest tie-break), and **every service's verified running
release**, even if none of the links points to it. At most 16 retired releases
are removed per install, oldest first. This is a retention target, not a quota.

Each production entrypoint writes its pinned `pi.__path__`, PID and
`INVOCATION_ID` atomically to `/run/<service-name>/package-release` before starting
the original worker. Dashboard uses its existing runtime directory; the other
three have `RuntimeDirectory=` and mode 0750. Write failure only warns. The
record is not an installer's guess based on current. GC checks PID/invocation
against `systemctl show` independently for all four services.

Any missing/unreadable/mismatched record, transitional/unknown state or query
failure skips deletion. Only inactive/failed with MainPID 0 needs no record.
This also deliberately retains everything while an active service still runs
flat during partial cutover or rollback. Immediately after a Type=simple restart,
systemd can return before the entrypoint writes its record; a later install
retries GC. Do not treat an install's success as proof collection ran.

The collector retains the existing safety boundary: held lock/inode, canonical
owned root, exact release names, recognized manifests/trees, filesystem identity,
no symlinks/hardlinks/foreign or writable nodes, no unknown files, and complete
mountinfo checks including same-device bind mounts. It revalidates immediately
before fd-safe recursion. Ambiguity keeps everything; a deletion failure never
fails the install. JSON `gc` reports status, kept, removed, reason and any deferred
backlog. It never traverses flat files, backups, state journals, compute or
frontend trees. Do not replace it with a blanket recursive cleanup.

Factory-only dashboard smokes and custom long-lived package consumers must hold
a shared `.install.lock` for their entire lifetime and stop before deployment:

```bash
ssh pi@vanpi.lan '/usr/bin/flock -s /home/pi/scripts/python-packages/.install.lock /bin/bash'
```

Never smoke with production `-m pi.apps.van_dashboard` alongside the live owner:
it starts COP/relay and connectivity workers. A reviewed Flask factory-only
smoke needs an isolated state/runtime directory, loopback port 8791, no debug or
reloader, and only non-mutating manifest/icon/root requests. Even some GET APIs
have side effects. The factory/relay-off helper never claims the production
record. Short flat-import utility subprocesses use standalone modules; do not
hold the installer lock inside `sns.sh` invoked by a restarting video service,
which would deadlock that restart.

## Network installer boundary

Only `deploy_python.py` owns the dashboard backend and base unit. The storage
installer owns recorder/storage/logging, independent React releases and
`van-dashboard.service.d/network-storage.conf`, never package or frozen flat
backend files. Full apply/rollback may restart the backend to pick up its drop-in
or frontend. Use `deploy_network_storage.py check/apply --recorder-only` for
routine recorder/spool changes; it preserves unrelated UI/backend/drop-in state.

Historical storage manifests containing retired flat dashboard targets are
rejected in their entirety, including rollback, before reads/stops/writes. Do
not edit manifests or bypass guards using an old installer. Recover recorder
code with a reviewed forward deployment retaining the current ownership guards.
The legacy recorder installer refuses storage-managed or package-activated hosts,
including malformed/dangling activation markers. Neither installer bootstraps
package services. See `networking/NETWORK_FLIGHT_RECORDER.md` for storage/retention,
provenance and manifest-specific rollback safeguards.

For a coupled recorder/dashboard change, ship a backward-compatible recorder
first with the storage installer's recorder-only workflow, verify flash/readiness,
then package `--update`. Never downgrade a provider underneath a consumer needing
its new contract. React follows independently. No network installer, monitor,
compute or vehicle transmission behavior changes in this retirement.

## Verification limits

Offline tests validate archive integrity, frozen-tree preservation, per-service
cutover/retry/idempotence, unit ownership, entrypoint dispatch/records and fail-
closed GC. Application regression suites remain authoritative for behavior.
Python 3.11 grammar checks on another interpreter do not prove Pi dependencies,
GPIO/I2C, MQTT publication, desktop DBUS/VLC/Sonos playback, systemd permissions,
actual frozen-backup freshness or runtime record identity. Those require the
supervised live checklist above.
