# Pi Python deployment

**The owner runs these commands later. This refactor has not touched the Pi.**
Use this runbook for the dashboard package cutover or recovery. Do not use broad
sync for the first cutover. Keep the existing flat dashboard until a later,
separately reviewed cleanup.

## Quick recovery

If the first package restart fails, go directly to [Rollback](#rollback).
Do not flatten the new dashboard, delete a release, run a network installer's
UI deployment, or repeatedly run broad sync to try to repair it.

**Broad sync is blocked until cutover.** Once this change is in the primary
checkout/branch used for routine sync, `pi/sync_scripts.sh`—including the BTT
button that invokes it—exits **1** at the preflight with
`Python package activation preflight failed` until step 3 succeeds. It deploys
**nothing**: no shell scripts, units, home files, `smb.conf`, secrets or compute.
The same block returns after rollback until explicit re-activation. If a
non-Python change must ship first, use its specific scoped deployer when one
exists (for example the preview deployer for frontend assets); otherwise do the
cutover first. Do not bypass the preflight to force a broad sync. After setting
`REPO` as shown below, the scoped frontend command is
`bash "$REPO/pi/deploy_van_dashboard_preview.sh"`.

## Architecture and ownership

The stdlib-only `pi/deploy_python.py` resolves its checkout from `__file__`, not
cwd, `$HOME`, or another clone. Its JSON dry-run is entirely local: it runs Git
for provenance, reads allowlisted sources, and never opens a network connection.
The real transport is one SSH invocation to `pi@vanpi.lan`; only unit installation
and systemd operations use sudo. No dependency installation occurs on the Pi.

A source-tree release, rather than a wheel, minimizes changes to these personal
scripts and their independent dependency environments. Its explicit allowlist
is immediate Python modules in these directories, package initializers, the
three video runtime assets, and the dashboard unit:

- `pi/apps/{audiobooks,bme280,van_dashboard,video_library}`
- `pi/apps/van_dashboard/routes` (15 feature blueprints, including `projects`)
- `pi/apps/video_library/players` (explicit immediate modules, not recursive)
- `pi/scripts/python` and `shared/python`
- `pi/apps/video_library/templates/video_library.html`
- `pi/apps/video_library/static/{video_library.js,video_library.css}`

There is **no recursive transfer of `pi/`**. Secrets, configs, tests, frontend
source/build dependencies, node_modules, caches, bytecode and `pi/van_compute/`
are not inputs. Reject symlinked inputs and review every dry-run source before
shipping. React builds retain their independent preview-release deployment.

On the Pi:

```text
/home/pi/scripts/python-packages/
  releases/<content-and-provenance-digest>/
    manifest.json
    pi/apps/van_dashboard/...
    pi/apps/{audiobooks,bme280,video_library}/...
    pi/scripts/python/...
    shared/python/...
    pi/services/van-dashboard.service
  current -> releases/<digest>
  previous -> releases/<prior-digest>
  pre-package-units/                 # automatic first-cutover unit backup
  activated.json                     # opt-in cutover gate
  pending-restart                    # only while activation needs completion
  legacy-pending-restarts.json        # interrupted legacy copy/restart recovery
/home/pi/scripts/python-automation/  # existing flat fallback; never delete
/home/pi/van_compute/scripts/        # separately installed compute modules
```

Every release records absolute source checkout, Git commit, branch, dirty flag,
file checksums and per-service dependency digests. A receiver lock serializes
package and legacy installs. Incoming regular files are checked for paths,
hashes, completeness and Python syntax before an atomic rename publishes the
release. Only then can `current` be atomically replaced. Re-running identical
inputs/provenance verifies the existing release and does not restart an unchanged
service. Different checkout provenance can create a new release without a
runtime restart. Every successful mode also attempts conservative automatic
release retention under the same lock; see [Automatic release retention](#automatic-release-retention).

Only `van-dashboard.service` converts in this ticket. It keeps that exact name,
including independently managed `van-dashboard.service.d/network-storage.conf`.
It runs `/usr/bin/python3 -P -m pi.apps.van_dashboard`. Its relay-off safety hook
runs `-m pi.apps.van_dashboard.van_dashboard_cop --relay-off`. It retains GPIO
membership and all other runtime configuration. Python 3.11 is required; `-P`
omits the unsafe working-directory import entry. An explicit `pi/__init__.py`
prevents namespace merging and pins its package path to the resolved immutable
release, so a running process cannot lazily import modules from a newer release
when `current` changes. `shared` is pinned similarly. Bytecode writing is disabled
in the dashboard unit. Verify `pi.__path__` below rather than assuming a package
named `pi` is unambiguous on this host. The production `__main__` entrypoint
atomically writes that already-pinned path, its PID and systemd `INVOCATION_ID`
to `/run/van-dashboard/package-release` before starting the worker lifecycle.
It does not resolve `current` again: a concurrent link switch cannot change the
recorded import location. A failed write warns but does not prevent startup;
retention then fails closed. The relay-off helper and factory-only smoke never
write the production record.

The unit's PYTHONPATH contains the package release root and
`/home/pi/van_compute/scripts`. The dashboard imports `van_compute_metrics`
directly from the latter. There is no runtime sys.path mutation and no bundled
`pi.van_compute` copy. The coupled compute installer retains its flat import
branches and owns compute upgrades/restarts. **Follow-up:** van_compute package
conversion must update both its Pi and Mac installer layouts together.

The package installer compares the dashboard dependency digest and live unit.
A changed dashboard module or unit restarts an active dashboard; unrelated
staged apps and the independent preview server do not. Inactive services stay
inactive during routine updates. First activation starts the dashboard; an
unchanged repeat is a no-op.
A failed unit reload/restart leaves a retry marker; retries reload the unit and
complete the restart instead of incorrectly deciding that matching bytes mean
success. Service health still needs the operator's checks below.

The retention change leaves the service unit byte-for-byte unchanged, including
`-P -m`, PYTHONPATH, runtime-directory permissions and the relay-off ExecStopPost.
Its new entrypoint record **does change the dashboard dependency digest**, so the
first normal package update restarts an active dashboard once. Subsequent
unrelated-file updates still do not restart it.

### First cutover is opt-in

Default deployment **stages only**. `--activate` is the explicit first-cutover
operation; `--update` refuses unless activation was completed. After merging,
routine `pi/sync_scripts.sh` first performs a read-only activation-marker SSH
preflight, so a normal run cannot bypass this staged runbook on an unconverted
or deliberately rolled-back host.

After successful cutover, routine sync installs and waits for its home files,
hooks, secrets, Samba configuration, tmpfiles, shell scripts and other units.
Only after all those transfers succeed does it run package `--update` and the
explicit legacy subset, so Python never restarts ahead of its updated script
and tmpfiles dependencies. The conditional van_compute installer remains last.
Broad sync deliberately stays pinned to the primary trusted checkout,
`/Users/jacobr/dev/scripts`, even when its launcher is invoked from another clone.
It publishes ignored private inputs (`pi/secrets`, `.twilio`, `pi/configs`,
including `smb.conf`, and hooks); an alternate clone may have stale or incomplete
copies. Its package and compute installer calls use that same primary checkout.
The generic service updater still handles non-package units and their script
changes; it does not install the dashboard unit or remove the flat dashboard.

For Python backend deployment from an alternate clone, run that clone's
`pi/deploy_python.py` directly. All four modes—default stage, `--activate`,
`--update` and `--legacy-flatten`—resolve their own checkout, record provenance
and ship no private inputs. Like the scoped preview/video deployers, this
replaces live code with the selected clone's version; a later primary-checkout
broad sync can replace it again. For example:

```bash
python3 "$HOME/dev/scripts_3/pi/deploy_python.py" --dry-run --update
python3 "$HOME/dev/scripts_3/pi/deploy_python.py" --update
```

### Consumers deliberately staying flat

`--legacy-flatten` exists for one transition release. It copies only flat-safe
audiobook, BME280, video-library and utility modules plus video assets. It refuses
basename collisions. It **never** flattens dashboard modules or the preview
server, including after imports become package-only. Default stage/activate/update
never write the flat directory; the explicit legacy option does update its
non-dashboard subset. Legacy copies are not an atomic app release. Changed
active audiobook/BME280/video services restart; unchanged/inactive ones do not.
Restart intent is journaled before copying, so retrying an interrupted copy or
failed restart completes the originally active services' restarts even when
files now match (or the failed service is now inactive). Do not delete the
legacy retry journal to conceal an incomplete operation.

The subset contains **28 destinations: 25 Python modules and 3 video assets**.
Its 18 video modules are `video_library_server`, `video_asset_catalog`,
`video_qbittorrent`, `catalog_values`, `catalog`, `config`, `identity`,
`legacy_progress`, `library`, `media_models`, `naming`, `playback`, `routes`,
`schema`, `service`, `v1_bridge`, `vlc_player`, and `sonos_volume` (all `.py`).
The last two come from `players/` but flatten by basename like the former
`find -exec cp` transfer. The remaining modules are `audiobook_server.py`,
`bme280_mqtt.py`, `bme280_testread.py`, and the four utilities `ip_info.py`,
`vlc_property.py`, `sonos_tasks.py`, `ip_only.py`. Assets retain
`templates/video_library.html` and `static/{video_library.js,video_library.css}`.
Generic video basenames still pass the shared collision guard; any later
collision is an error, not permission to overwrite another app's module.
Changes anywhere under `pi/apps/video_library/`, including `players/`, restart
an active video service. The players directory remains a namespace subpackage
(no new `__init__.py` is needed); its flat-import branches remain load-bearing.

| Consumer | Disposition and update path |
| --- | --- |
| `video-library.service` | Flat, preserving the existing media/data migration contract and Rank 3's 18-module manifest (including flattened players). Keep package/flat import guards. Update through its checkout-relative `pi/deploy_video_library.sh` (including git-ref/data-safe rollback), or routine sync's complete legacy subset after cutover. Its unit and dedicated deployer are not converted independently of one another. |
| `audiobooks.service` | Flat; routine sync / explicit legacy subset updates `audiobook_server.py`. |
| `bme280-mqtt.service` | Flat; legacy subset, retaining `/home/pi/pyvenv/bin/python` and the sensor environment. |
| `van-dashboard-preview.service` | Existing independently owned preview `current` release. Continue `pi/deploy_van_dashboard_preview.sh`; the package copy of its source is not its running server. |
| `pi/.bashrc` (`ipinfo`, VLC helper, Sonos helper, PYTHONPATH) | Retains utility flat paths. Legacy subset updates `ip_info.py`, `vlc_property.py`, `sonos_tasks.py`, `ip_only.py`; broad sync still updates `.bashrc`. Do not use the shell's flat PYTHONPATH to launch the packaged dashboard. |
| `pi/sns.sh` | Runs `python3 -c "from sonos_tasks import ..."`, inheriting the flat-directory PYTHONPATH from `.bashrc` (or the video service). The legacy subset keeps `sonos_tasks.py` current alongside the video's flat `sonos_volume.py` player; neither becomes a dashboard package import. The Mac wrapper uses its own checkout's `shared/python` and is unaffected. |
| `pi/scripts/log_position.sh` | Retains `vlc_property.py` flat calls; source/guards unchanged, helper updated by legacy subset. |
| `pi/raspbian_setup.sh` | Historical setup still creates/configures the flat utility directory. It does not perform package activation; use this runbook afterward. |
| Mac BTT `repair_rps.py` / `repair_script_paths.py` | The former saves a button pointing at its checkout's `pi/sync_scripts.sh`; the latter remaps old sync paths. Neither directly launches flat Python today. Keep py/js pairing untouched; the saved sync entrypoint now observes the activation preflight. The archived July path-rewrite helper still mentions the former flat layout and is historical, not a new deploy path. |
| Compute installer cleanup of old `van_compute_protocol.py` | Independently owned migration cleanup; no compute file ships in this package. |
| `pi/scripts` programs, cron/timers, `system_event_monitor` | Rank 3's shim plus `system_monitor/` package still ship through broad sync under `/home/pi/scripts`, with existing service/cron paths. Keep script/package import guards. No service uses a package-release copy, so `pi/scripts/system_monitor` is not allowlisted. Package-release conversion is a follow-up. |

The exact base-commit consumer inventory is retained in local verification
artifacts, including all unit and cron/timer references. References in tests and
architecture docs describe contracts rather than additional live processes.

### Interaction with Rank 3 deployment

Rank 3 is merged but **undeployed**. Its system-monitor half ships with broad
sync, which this branch blocks until dashboard cutover. The owner must either
ship Rank 3 from master **before merging this branch**, or complete this
runbook's dashboard cutover before syncing it. Do not bypass the activation
preflight or remove the script/flat import guards in either Rank 3 component.

All commands below are owner-run on the Mac from the primary trusted checkout,
not this integration worktree. Before merging Rank 4, with the primary checkout
on the Rank 3 master containing `94620e3` and no package deployer yet:

```bash
cd /Users/jacobr/dev/scripts
git status --short
git branch --show-current
git merge-base --is-ancestor 94620e3 HEAD && test ! -f pi/deploy_python.py && bash pi/sync_scripts.sh
```

Otherwise, after merging Rank 4, first follow steps 1–3 below with
`REPO=/Users/jacobr/dev/scripts`. Only after supervised activation and health
checks, ship the system-monitor shim/package and other broad-sync dependencies:

```bash
cd /Users/jacobr/dev/scripts
bash pi/sync_scripts.sh
```

The video half can ship **at any time**, independent of dashboard activation,
through its scoped deployer (including its complete module manifest and unit):

```bash
cd /Users/jacobr/dev/scripts
bash pi/deploy_video_library.sh
```

Or after cutover, broad sync includes it through the legacy subset. A scoped
code/assets-only legacy update (not an installer for a missing video unit) is:

```bash
cd /Users/jacobr/dev/scripts
python3 pi/deploy_python.py --dry-run --legacy-flatten
python3 pi/deploy_python.py --legacy-flatten
```

The projects API is the `projects` blueprint in `routes/projects.py`.
`runtime.hosted_projects` is constructed immediately after the shared
`state_store`, preserving master's initialization order. Both the controller
module and route module are covered by the dashboard release dependency digest;
there is no flat projects prerequisite or file update in the dashboard unit.

### Network installer boundary

`pi/deploy_network_storage.py` and `pi/deploy_network_flight_recorder.py` remain
unchanged: they are hash-pinned, manifest-guarded installers with their own
rollback. Their dashboard writes still target
`/home/pi/scripts/python-automation/van_dashboard_history.py` and, in the older
recorder installer, `van_dashboard.py`. After cutover those writes **do not update
the package backend**. A `van-dashboard` restart only restarts the package.

Use the storage installer's existing `check/apply --recorder-only` workflow for
recorder-only updates. Preserve its existing storage validation and rollback
procedure. Do **not** rely on either installer's dashboard deployment or UI
rollback after cutover: it can overwrite the flat fallback without updating the
running package. The older flight-recorder installer's `TARGETS` also includes
`pi/services/van-dashboard.service`: from this branch it would install the
**package** unit, despite its flat backend-file targets. Its `inspect_remote`,
`apply_remote` and `rollback_remote` explicitly refuse whenever
`/etc/vanpi-network-storage.json` exists. A storage-managed host is therefore
blocked before those old operations; on an unmanaged host the mixed
flat-files/package-unit plan is unsafe and cannot bootstrap the package release.
Do not remove storage configuration to evade the refusal. **Follow-up:** teach
those installers package releases while preserving their hashes, manifests,
storage safeguards and independent frontend ownership. Do not work around their
guards with a repository-wide sync.

## Prerequisites and local plan

Run these on the Mac. Edit only the first line for your chosen clone. Keep this
checkout unchanged from staging through activation; avoid deploying unreviewed
dirty sources. The plan prints the dirty flag rather than silently ignoring them.

```bash
REPO="/absolute/path/to/your/scripts-checkout"
cd "$REPO"
mkdir -p "$REPO/tmp"
python3 "$REPO/pi/deploy_python.py" --dry-run > "$REPO/tmp/package-plan.json"
python3 -m json.tool "$REPO/tmp/package-plan.json"
RELEASE=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["release"])' "$REPO/tmp/package-plan.json")
printf 'Selected release: %s\n' "$RELEASE"
```

Verify that every
`source` starts with the selected checkout, provenance is right, and no forbidden
inputs are listed. Staging does not build React or install Flask/compute. Before
cutover ensure the existing frontend, `/home/pi/van_compute/scripts`, Flask and
GPIO dependencies are present. Do not create a second COP owner to test them.

## 1. Stage beside the live flat tree

On the Mac, using the same `REPO` and `RELEASE`:

```bash
python3 "$REPO/pi/deploy_python.py"
ssh pi@vanpi.lan "test -r /home/pi/scripts/python-packages/releases/$RELEASE/manifest.json"
ssh pi@vanpi.lan
```

The first command stages only: no unit changes, no current-link switch, no
restart, and no flat file writes. Confirm its printed release matches `RELEASE`;
if not, stop and re-review the plan. On the Pi, set the printed digest explicitly:

```bash
RELEASE="paste-the-reviewed-release-digest"
STAGED="/home/pi/scripts/python-packages/releases/$RELEASE"
test -r "$STAGED/manifest.json"
cd "$STAGED"
PYTHONPATH="$STAGED:/home/pi/van_compute/scripts" PYTHONDONTWRITEBYTECODE=1 /usr/bin/flock -s /home/pi/scripts/python-packages/.install.lock /usr/bin/python3 -P -c 'import pi,sys,van_compute_metrics; print(sys.version); print(list(pi.__path__)); print(van_compute_metrics.__file__)'
/usr/bin/python3 -c 'import importlib.metadata as m; print(m.version("flask"))'
```

Expect Python 3.11+, exactly this release's `pi` directory, and metrics from
`/home/pi/van_compute/scripts/van_compute_metrics.py`. Anything else is a stop,
not a reason to remove `-P` or reintroduce a sys.path hack. Check the effective
live unit/drop-ins before changing them:

```bash
sudo /usr/bin/systemctl cat van-dashboard.service
/usr/bin/test -r /home/pi/scripts/van-dashboard-preview/current/index.html
/usr/bin/test -r /home/pi/van_compute/scripts/van_compute_metrics.py
/usr/bin/test -r /home/pi/scripts/python-automation/van_dashboard.py
```

## 2. Side-effect-free staging smoke on 8791

Stay on the Pi. **Do not use `python -m pi.apps.van_dashboard` for a parallel
smoke:** production main starts COP/relay, connectivity and Starlink loops.
Instead Flask loads the factory directly and never enters production main.
The `flask --app` option requires **Flask 2.2 or newer**; use the version printed
in step 1 to select the command. With Flask >= 2.2:

```bash
SMOKE_STATE=$(mktemp -d /tmp/van-dashboard-package-smoke.XXXXXX)
cd "$STAGED"
PYTHONPATH="$STAGED:/home/pi/van_compute/scripts" PYTHONDONTWRITEBYTECODE=1 VAN_DASHBOARD_STATE_PATH="$SMOKE_STATE/state.json" VAN_DASHBOARD_RUNTIME_DIR="$SMOKE_STATE/runtime" VAN_DASHBOARD_FRONTEND_ROOT=/home/pi/scripts/van-dashboard-preview/current FLASK_DEBUG=0 /usr/bin/flock -s /home/pi/scripts/python-packages/.install.lock /usr/bin/python3 -P -m flask --app pi.apps.van_dashboard:create_app run --host 127.0.0.1 --port 8791 --no-reload --no-debugger
```

If the installed Flask is older, use its `FLASK_APP` environment-variable form
instead; do not upgrade the live Flask installation just to obtain `--app`:

```bash
SMOKE_STATE=$(mktemp -d /tmp/van-dashboard-package-smoke.XXXXXX)
cd "$STAGED"
PYTHONPATH="$STAGED:/home/pi/van_compute/scripts" PYTHONDONTWRITEBYTECODE=1 VAN_DASHBOARD_STATE_PATH="$SMOKE_STATE/state.json" VAN_DASHBOARD_RUNTIME_DIR="$SMOKE_STATE/runtime" VAN_DASHBOARD_FRONTEND_ROOT=/home/pi/scripts/van-dashboard-preview/current FLASK_DEBUG=0 FLASK_APP=pi.apps.van_dashboard:create_app /usr/bin/flock -s /home/pi/scripts/python-packages/.install.lock /usr/bin/python3 -P -m flask run --host 127.0.0.1 --port 8791 --no-reload --no-debugger
```

`--port 8791` is Flask CLI's port override, without code changes. It binds
loopback only. Factory/controller construction reads state and allocates objects;
it does not start worker threads, claim GPIO or issue network/process commands.
The factory preserves one controller set per process across app instances.
Both its state file and runtime/COP marker directory are isolated beneath
`SMOKE_STATE`, rather than using the live owner's paths. The shared `flock` holds
`.install.lock` for the smoke's entire lifetime, protecting its staged imports
from retention. **Installs, including broad sync, block until it exits. Stop the
smoke before deploying.** Any other manually launched release consumer must
hold this same shared lock for its full lifetime; only the production systemd
entrypoint has automatic running-release ownership.

**This is not a sandbox for arbitrary API requests.** Existing handlers can
perform work, even some GETs (for example UBNT refresh). Do not browse the staged
UI: its JavaScript polls APIs. Do not POST controls or run full dashboard API
smokes concurrently with the live owner. In a second Pi SSH terminal, curl only:

```bash
curl -fsS http://127.0.0.1:8791/manifest.webmanifest | /usr/bin/python3 -m json.tool
curl -fsS http://127.0.0.1:8791/app-icon.svg > /dev/null
curl -fsS http://127.0.0.1:8791/ > /dev/null
test "$(curl -sS -o /dev/null -w '%{http_code}' 'http://127.0.0.1:8791/api/vonstar?unexpected=1')" = 400
```

The malformed GET verifies blueprint routing/validation without reaching a
controller. `/` must be 200 when the independent React release exists; 503 means
its build is missing. Stop the staging server with **Ctrl-C before cutover**.
This Flask-CLI exit does not execute the production COP stop/relay hook because
it never started the owner. The temporary state directory can simply remain in
`/tmp`; do not recursively clean a guessed path.

## 3. Save live units and cut over

In a Pi terminal, before activation:

```bash
(
set -euo pipefail
umask 077
BACKUP="/home/pi/scripts/python-packages/operator-backup-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 700 "$BACKUP"
sudo cp -a /etc/systemd/system/van-dashboard.service "$BACKUP/van-dashboard.service"
if sudo test -d /etc/systemd/system/van-dashboard.service.d; then sudo cp -a /etc/systemd/system/van-dashboard.service.d "$BACKUP/van-dashboard.service.d"; fi
sudo /usr/bin/systemctl cat van-dashboard.service > "$BACKUP/van-dashboard.effective.txt"
printf '%s\n' "$BACKUP" > /home/pi/scripts/python-packages/LAST_OPERATOR_BACKUP
sudo grep -F '/home/pi/scripts/python-automation/van_dashboard.py' "$BACKUP/van-dashboard.service"
)
```

The final check must show the old flat ExecStart. If it does not, investigate the
actual layout; do not claim this is the flat-to-package first cutover. Keep the
flat directory in place. The installer also saves `pre-package-units` once,
without replacing an existing backup.

On the Mac:

```bash
python3 "$REPO/pi/deploy_python.py" --dry-run --activate
python3 "$REPO/pi/deploy_python.py" --activate
```

Activation verifies/copies the same release, switches `current`, installs the
package unit, reloads systemd and restarts `van-dashboard.service`. It does not
change/drop network-storage drop-ins. If any command fails, inspect the error
and use rollback; do not delete the pending-restart marker to fake success.

On the Pi, watch first restart:

```bash
sudo journalctl -u van-dashboard.service -n 100 --no-pager
sudo journalctl -u van-dashboard.service -f
```

Expect normal Flask startup listening on 8788, without repeated exits/restarts.
Stop on `ModuleNotFoundError`, `ImportError`, missing ExecStartPre inputs,
`Address already in use`, tracebacks, GPIO ownership/permission errors, or a
restart loop. In another Pi terminal:

```bash
/usr/bin/systemctl is-active van-dashboard.service
curl -fsS http://127.0.0.1:8788/manifest.webmanifest > /dev/null
curl -fsS http://127.0.0.1:8788/ > /dev/null
curl --retry 10 --retry-connrefused --retry-delay 1 --connect-timeout 2 --max-time 5 -fsS http://127.0.0.1:8788/api/status | /usr/bin/python3 -m json.tool
```

Now there is only one production controller owner. Inspect the COP/relay fields
for expected intent and no new errors; do not arm it merely to test deployment.
Existing unavailable external services are not automatically a packaging failure.
Compare against pre-cutover health and investigate new errors. If preview/media
is involved, its independent logs remain:

```bash
sudo journalctl -u van-dashboard-preview.service -f
sudo journalctl -u video-library.service -f
```

## Rollback

Rollback means restoring the saved old unit and starting the **untouched old flat
dashboard**, never re-flattening post-refactor sources. No package release needs
to be deleted. On the Pi:

```bash
(
  set -euo pipefail
  ROOT=/home/pi/scripts/python-packages
  exec 9>"$ROOT/.install.lock"
  /usr/bin/flock -x 9
  BACKUP=$(cat "$ROOT/LAST_OPERATOR_BACKUP")
  case "$BACKUP" in "$ROOT"/operator-backup-*|"$ROOT"/pre-package-units) ;; *) exit 1 ;; esac
  test "$(/usr/bin/readlink -e "$BACKUP")" = "$BACKUP"
  sudo test -r "$BACKUP/van-dashboard.service"
  sudo grep -F '/home/pi/scripts/python-automation/van_dashboard.py' "$BACKUP/van-dashboard.service"
  test -r /home/pi/scripts/python-automation/van_dashboard.py
  sudo install -m 644 "$BACKUP/van-dashboard.service" /etc/systemd/system/van-dashboard.service
  if test -f "$ROOT/activated.json"; then mv "$ROOT/activated.json" "$ROOT/activated.rolled-back.$(date -u +%Y%m%dT%H%M%SZ).$$.json"; fi
  sudo /usr/bin/systemctl daemon-reload
  sudo /usr/bin/systemctl restart van-dashboard.service
)
sudo journalctl -u van-dashboard.service -n 100 --no-pager
curl --retry 10 --retry-connrefused --retry-delay 1 --connect-timeout 2 --max-time 5 -fsS http://127.0.0.1:8788/api/status | /usr/bin/python3 -m json.tool
```

The activation marker is retired so routine sync cannot silently re-cut over.
The package installer never changed the drop-in directory, so leave it in place;
its archived copy is evidence/recovery material, not permission to overwrite a
later independent storage configuration. If `LAST_OPERATOR_BACKUP` is unavailable,
verify/select the automatic backup with these commands, then rerun the rollback
block. Do not roll back the media database or React release as part of backend
package rollback.

```bash
sudo grep -F '/home/pi/scripts/python-automation/van_dashboard.py' /home/pi/scripts/python-packages/pre-package-units/van-dashboard.service && printf '%s\n' /home/pi/scripts/python-packages/pre-package-units > /home/pi/scripts/python-packages/LAST_OPERATOR_BACKUP
```

After diagnosing a failed cutover, repeat staging/import checks, then use
`--activate` explicitly again. An installer retry keeps the saved flat unit and
retries a pending reload/restart even if release bytes already match.

## Automatic release retention

A new commit/branch/checkout provenance creates a release even when runtime
files are unchanged. After every successful **stage, activate, update or legacy**
install, the receiver attempts GC while still holding its exclusive
`.install.lock`. It keeps the union of:

- `current` and `previous` (if present);
- the release actually imported by the running dashboard;
- the **three newest** releases, ordered by release-directory modification time
  (digest breaks ties), for manual rollback;
- the just-installed release, even when retrying an older staged release.

Only retired real `releases/<24-lowercase-hex>` directories are eligible. At most
**16 releases per install** are removed, oldest first; later installs drain any
remaining backlog. This is a retention target, not a quota: safety always wins,
so an ambiguous host can retain everything until the cause is resolved.

### Running ownership and races

GC reads `systemctl show`'s `ActiveState`, `MainPID` and `InvocationID`. For an
active service, `/run/van-dashboard/package-release` must identify that exact PID
and invocation and name a real validated release. It is written by the process
from its pinned `pi.__path__`, not by the installer and not from a fresh `current`
lookup. Thus a crash/reboot/operator restart updates ownership correctly, while
an unrelated-file install can leave an older running release protected even
after both links move on.

Missing, unreadable, stale or unexpected records while active, transitional
states, or failed service queries cause **no deletion**. Only an inactive/failed
service with MainPID 0 needs no running-release protection. The record is under
systemd's existing RuntimeDirectory, which is cleared on stop; identity checks
also reject a stale record before a new process writes its own. A restart after
GC's snapshot can only start from the protected `current` while the installer
lock prevents another link switch. The relay-off ExecStopPost also uses this
protected `current`. A RuntimeDirectory override that moves the record away
from `/run/van-dashboard` conservatively disables GC until the collector's record
location is deliberately updated.

### Fail-closed deletion boundary

GC checks the held lock/inode, canonical root, ownership and permissions, exact
release names, manifest identity and declared tree structure before deleting
anything. It refuses symlinks (including nested links), hardlinked files, foreign
or writable nodes, unexpected files/directories, incomplete releases and a
missing protected target. Every release must be on the package root's filesystem.
Linux mountinfo detects mounts underneath `releases`, including same-device bind
mounts; mount discovery failures also stop GC. Tree and mount checks repeat
immediately before fd-safe recursive removal. Do not mutate releases or links
outside the installer lock, or mount filesystems in this immutable namespace.

GC never traverses the flat directory, `operator-backup-*`, `pre-package-units`,
activation/restart journals, compute installation or frontend releases. A
validation/deletion failure is reported but **never fails an otherwise successful
install**. A failed removal may leave a partial retired tree; subsequent GC
refuses that tree rather than silently deleting unrecognized leftovers.

The receiver's final JSON includes `gc.status` (`ok` or `skipped`), `kept`,
`removed`, a diagnostic `reason`, and on success `deferred` for the deletion-cap
backlog. A skipped collection can have already removed earlier validated retired
releases before encountering a deletion error; `removed` lists only completed
removals. Inspect skipped results rather than treating deployment success as
proof that retention ran. With this `Type=simple` service, systemd may return
from restart before Python writes the record; that install safely skips GC and
a later successful install retries it.

### Read-only checks and manual escape hatch

Run on the Pi after the normal sync:

```bash
/usr/bin/systemctl show -p ActiveState -p MainPID -p InvocationID van-dashboard.service
/usr/bin/python3 -m json.tool /run/van-dashboard/package-release
/usr/bin/readlink -e /home/pi/scripts/python-packages/current
/usr/bin/readlink -e /home/pi/scripts/python-packages/previous
/usr/bin/find /home/pi/scripts/python-packages/releases -mindepth 1 -maxdepth 1 -printf '%f %y\n'
/usr/bin/journalctl -u van-dashboard.service -n 100 --no-pager
```

Expect `ActiveState=active`, matching record PID/invocation, and a `package_path`
ending in `/releases/<digest>/pi`. That digest may intentionally differ from
`current` and `previous` after unrelated updates. All listed releases should be
real directories; the JSON should explain which were kept/removed. After the
first rollout, check for normal startup and no package-record warnings.

For manual recovery, **leave ambiguous releases in place**, stop any factory
smokes, diagnose the reported boundary, then retry a normal install to run the
same guarded collector. Do not replace it with a blanket `rm -rf`. If a separate
manual inspection must freeze retention, hold the existing lock in a Pi terminal:

```bash
/usr/bin/flock -s /home/pi/scripts/python-packages/.install.lock /bin/bash
```

Keep that shell open while inspecting and `exit` when finished; all installs
wait meanwhile. The flat rollback procedure above does not require release
pruning and its backups remain untouched.

## Verification and limits

Route paths and methods are unchanged. The comparison is sorted
`(rule, sorted(methods))`, including Flask's automatic HEAD/OPTIONS; endpoint names
change to `blueprint.function`. No `url_for`, `request.endpoint`, endpoint-specific
registration or `view_functions` consumer was found in the dashboard/frontend,
tests, network installers or dashboard docs. Consumers use URL paths.

The route bodies and existing controller definitions are mechanically equivalent
to the base commit; tests patch the actual owning feature or runtime module, not
facade re-exports. The process runtime preserves shared mutable controller
identity; a new Flask app does not mean a second safe hardware owner. Production
`-m` starts the original worker lifecycle and SIGTERM handling. Factory-only
staging intentionally does not test hardware startup; the first real restart
must be supervised as above.

Local verification uses Python 3.13 and checks Python 3.11 grammar. That is not a
claim of a live Python 3.11/Pi/GPIO deployment test. Review the recorded baseline
failures separately; do not hide them by weakening route or frontend assertions.
