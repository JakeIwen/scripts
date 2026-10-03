# Pi Python deployment

**The owner runs these commands later. This refactor has not touched the Pi.**
Use this runbook for the dashboard package cutover or recovery. Do not use broad
sync for the first cutover. Keep the existing flat dashboard until a later,
separately reviewed cleanup.

## Quick recovery

If the first package restart fails, go directly to [Rollback](#rollback).
Do not flatten the new dashboard, delete a release, run a network installer's
UI deployment, or repeatedly run broad sync to try to repair it.

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
- `pi/apps/van_dashboard/routes` (explicit feature blueprint package)
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
runtime restart. No release garbage collection is automatic.

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
named `pi` is unambiguous on this host.

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
It now derives **all** source paths from its own checkout. The generic service
updater still handles
non-package units and their script changes; it does not install the dashboard
unit or remove anything from the preserved flat dashboard. Only use broad sync
from a checkout containing the intended ignored secrets/configs; checkout-relative
does not mean every clone has those private inputs.

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

| Consumer | Disposition and update path |
| --- | --- |
| `video-library.service` | Flat, preserving the existing media/data migration contract. Update through its checkout-relative `pi/deploy_video_library.sh` (including git-ref/data-safe rollback), or routine sync's legacy subset. Its unit and dedicated deployer are not converted independently of one another. |
| `audiobooks.service` | Flat; routine sync / explicit legacy subset updates `audiobook_server.py`. |
| `bme280-mqtt.service` | Flat; legacy subset, retaining `/home/pi/pyvenv/bin/python` and the sensor environment. |
| `van-dashboard-preview.service` | Existing independently owned preview `current` release. Continue `pi/deploy_van_dashboard_preview.sh`; the package copy of its source is not its running server. |
| `pi/.bashrc` (`ipinfo`, VLC helper, Sonos helper, PYTHONPATH) | Retains utility flat paths. Legacy subset updates `ip_info.py`, `vlc_property.py`, `sonos_tasks.py`, `ip_only.py`; broad sync still updates `.bashrc`. Do not use the shell's flat PYTHONPATH to launch the packaged dashboard. |
| `pi/scripts/log_position.sh` | Retains `vlc_property.py` flat calls; source/guards unchanged, helper updated by legacy subset. |
| `pi/raspbian_setup.sh` | Historical setup still creates/configures the flat utility directory. It does not perform package activation; use this runbook afterward. |
| Mac BTT `repair_rps.py` / `repair_script_paths.py` | The former saves a button pointing at its checkout's `pi/sync_scripts.sh`; the latter remaps old sync paths. Neither directly launches flat Python today. Keep py/js pairing untouched; the saved sync entrypoint now observes the activation preflight. The archived July path-rewrite helper still mentions the former flat layout and is historical, not a new deploy path. |
| Compute installer cleanup of old `van_compute_protocol.py` | Independently owned migration cleanup; no compute file ships in this package. |
| `pi/scripts` programs, cron/timers, `system_event_monitor` | Existing `/home/pi/scripts` deployment, unit and cron paths unchanged. The separate system-monitor/package split has not happened. |

The exact base-commit consumer inventory is retained in local verification
artifacts, including all unit and cron/timer references. References in tests and
architecture docs describe contracts rather than additional live processes.

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
PYTHONPATH="$STAGED:/home/pi/van_compute/scripts" PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -P -c 'import pi,sys,van_compute_metrics; print(sys.version); print(list(pi.__path__)); print(van_compute_metrics.__file__)'
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
Instead Flask loads the factory directly and never enters production main:

```bash
SMOKE_STATE=$(mktemp -d /tmp/van-dashboard-package-smoke.XXXXXX)
cd "$STAGED"
PYTHONPATH="$STAGED:/home/pi/van_compute/scripts" PYTHONDONTWRITEBYTECODE=1 VAN_DASHBOARD_STATE_PATH="$SMOKE_STATE/state.json" VAN_DASHBOARD_FRONTEND_ROOT=/home/pi/scripts/van-dashboard-preview/current FLASK_DEBUG=0 /usr/bin/python3 -P -m flask --app pi.apps.van_dashboard:create_app run --host 127.0.0.1 --port 8791 --no-reload --no-debugger
```

`--port 8791` is Flask CLI's port override, without code changes. It binds
loopback only. Factory/controller construction reads state and allocates objects;
it does not start worker threads, claim GPIO or issue network/process commands.
The factory preserves one controller set per process across app instances.

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

## Manual release pruning

A new commit/branch/checkout provenance creates a new release directory even if
runtime files are unchanged. There is no automatic GC. **Follow-up:** bounded
retention with explicit running-release ownership, including staged processes.
Do not delete everything except `current`/`previous` while running: an unchanged
service may still have an older release pinned after an unrelated-file update.

For manual pruning, schedule brief dashboard downtime, stop all factory-smoke
processes, and run the following **Bash block on the Pi**, only after cutover has
passed health checks. It keeps `current` and `previous`, serializes against
installers, refuses symlinks/mounts, and restores the previously active dashboard
on exit. Do not use it if another custom process consumes these releases.

```bash
(
  set -euo pipefail
  ROOT=/home/pi/scripts/python-packages
  test "$(/usr/bin/readlink -e "$ROOT")" = "$ROOT"
  test -f "$ROOT/activated.json"
  test -d "$ROOT/releases"
  test ! -L "$ROOT/releases"
  exec 9>"$ROOT/.install.lock"
  /usr/bin/flock -x 9
  CURRENT=$(/usr/bin/readlink -e "$ROOT/current")
  PREVIOUS=$(/usr/bin/readlink -e "$ROOT/previous")
  for keep in "$CURRENT" "$PREVIOUS"; do
    test "$(/usr/bin/dirname "$keep")" = "$ROOT/releases"
    test -d "$keep"
    test ! -L "$keep"
  done
  /usr/bin/systemctl is-active --quiet van-dashboard.service
  sudo /usr/bin/systemctl stop van-dashboard.service
  trap 'sudo /usr/bin/systemctl start van-dashboard.service' EXIT
  if /usr/bin/pgrep -af 'pi[.]apps[.]van_dashboard'; then
    printf '%s\n' 'Another dashboard/Flask process remains; refusing prune.' >&2
    exit 1
  else
    test "$?" = 1
  fi
  for old in "$ROOT"/releases/*; do
    test -e "$old" || continue
    name=${old##*/}
    [[ "$name" =~ ^[0-9a-f]{24}$ ]] || continue
    test "$old" != "$CURRENT" && test "$old" != "$PREVIOUS" || continue
    test -d "$old"
    test ! -L "$old"
    test "$(/usr/bin/readlink -e "$old")" = "$old"
    mounts=$(/usr/bin/findmnt -rn -o TARGET)
    test -n "$mounts"
    if ! printf '%s\n' "$mounts" | while IFS= read -r point; do
      case "$point" in "$old"|"$old"/*) exit 1 ;; esac
    done; then
      printf 'Mounted path beneath %s; refusing prune.\n' "$old" >&2
      exit 1
    fi
    printf 'Removing retired release %s\n' "$old"
    /bin/rm -rf --one-file-system -- "$old"
  done
)
curl --retry 10 --retry-connrefused --retry-delay 1 --connect-timeout 2 --max-time 5 -fsS http://127.0.0.1:8788/api/status | /usr/bin/python3 -m json.tool
```

If `previous` does not exist yet, do not prune: wait until a later verified
update provides a rollback package. This never removes the flat fallback,
operator/automatic unit backups, compute installation, or frontend releases.
Inspect `journalctl -u van-dashboard.service -n 100 --no-pager` after the restart.

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
