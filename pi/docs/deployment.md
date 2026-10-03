# Pi Python package deployment

Status: implementation in progress; **do not deploy this intermediate commit**.
Nothing in this refactor has been run on the Pi. Production Python is 3.11.

## Decisions

Use a source-tree release rather than a wheel. An explicit allowlist preserves
`pi/apps/...`, `pi/scripts/python/...` and `shared/python/...` paths, along with
package initializers and the video library's three runtime assets. No recursive
copy of `pi/` is permitted: secrets, configs, tests, frontend sources,
node_modules, caches and bytecode are not deployment inputs. `pi/van_compute/`
is excluded entirely. Its coupled installer continues owning the metrics module
in `/home/pi/van_compute/scripts`; dashboard imports that module using the unit's
explicit PYTHONPATH, never by modifying sys.path.

The new entrypoint derives the checkout from its own location, not cwd or a
particular clone. Each immutable release records the absolute checkout, commit,
branch and dirty flag. Its content digest identifies its directory beneath
`/home/pi/scripts/python-packages/releases/`. Copies finish and are verified
before a same-filesystem rename publishes a release. Activation atomically
replaces `python-packages/current`. A lock serializes installers; a failed copy
never changes current. There is no automatic release garbage collection.

Convert only `van-dashboard.service` in this ticket. It runs
`/usr/bin/python3 -P -m pi.apps.van_dashboard`, with PYTHONPATH containing the
release root and installer-owned compute scripts. `-P` (Python 3.11+) prevents
`/home/pi` shadowing imports. An explicit `pi/__init__.py` prevents namespace
merging; the runbook must verify the actual imported package path on the Pi.
The unit name and its network-storage drop-in remain unchanged. The relay-off
ExecStopPost becomes `-m pi.apps.van_dashboard.van_dashboard_cop --relay-off`.

Per-service dependency digests, not a list of ExecStartPre module names,
determine restart necessity. Dashboard Python changes change its digest; unrelated
staged audiobook/media modules do not. Unit changes also require a restart.
An unchanged release/unit is a no-op. The generic script/service updater retains
its existing non-package change detection and does not install the dashboard unit.

Initial cutover requires an explicit activation option. Routine sync must fail
closed before doing any work when package cutover has not happened: merging this
branch must not silently switch live units or replace the rollback dashboard.
After cutover, routine sync updates the package through its own installer, then
continues its existing home files, hooks, secrets, Samba, tmpfiles, shell scripts
and coupled compute installation. All source paths become checkout-relative.

## Legacy boundary and rollback

The pre-cutover `/home/pi/scripts/python-automation/` directory is never deleted
or overwritten by default package deployment. Rollback restores the saved live
unit files and starts their **old, untouched flat dashboard**, not a flattened
copy of post-refactor sources. Save units and drop-ins before activation.

For one transition release `--legacy-flatten` updates only the explicitly
allowlisted flat-safe apps and utilities: audiobooks, BME280, video library,
ip_info, vlc_property, ip_only and sonos_tasks, including video runtime assets.
Dashboard Python is never flattened, even with that flag. Duplicate basenames
are rejected. The option is explicit because these updates are not an atomic
package cutover. No legacy dashboard asset cleanup is performed.

Services staying flat:

| Consumer | Update path and reason |
| --- | --- |
| video-library.service | Existing checkout-relative dedicated media deployer, including its git-ref/data-safe rollback; left intact to avoid changing the media migration contract. |
| audiobooks.service | Explicit legacy subset through routine sync / legacy option; no dashboard dependency. |
| bme280-mqtt.service | Same subset; preserves `/home/pi/pyvenv/bin/python` and sensor dependencies. |
| van-dashboard-preview.service | Existing checkout-relative preview deployer; immutable frontend release remains independently owned. |
| Shell aliases, position logger, setup helpers, Mac BTT repair helpers | Keep their flat-safe utility paths during the transition; update via the legacy subset. No BTT pairing changes. |
| system_event_monitor and other pi/scripts programs | Keep existing `/home/pi/scripts` deployment and service/cron paths; no unrelated controller or filesystem-policy changes. |

The manifest-guarded `deploy_network_storage.py` and
`deploy_network_flight_recorder.py` are intentionally unchanged. Their dashboard
file installs still target the flat directory and **do not update the package
backend after cutover**; a restart of van-dashboard will only restart the package
backend. Use the storage installer's `check/apply --recorder-only` workflow for
recorder-only changes. Do not rely on either installer's dashboard deploy or
rollback after cutover: they could overwrite the preserved flat fallback.
Follow-up: teach those installers about package releases without weakening their
hash pinning or manifest-specific rollback. Follow-up: convert van_compute to
packages in its coupled Mac/Pi installer; its flat imports remain required today.

## Application boundary

The dashboard becomes an application factory with feature blueprints. Route
bodies and error messages are unchanged. Controllers retain their injection
seams. Factory startup does not run background threads; production `-m` startup
runs COP, connectivity and Starlink threads and installs SIGTERM cleanup as
before. A staging instance must avoid those threads and controller actions;
precise restrictions and commands are supplied in the completed runbook.

Route comparison is over sorted `(rule, sorted(methods))` pairs, not endpoint
names. Blueprint endpoint names are expected to change and are reported
separately. Frontend and Python callers must be checked for endpoint-name use.
Tests patch the real owning module, not historical star-import re-exports.

## Operator procedure

The completed runbook will provide exact staging, isolated smoke, activation,
journal and saved-unit rollback commands. Do not execute an intermediate version.
