# Van Dashboard testing

## Why Flask is required

`pi/tests/test_van_dashboard.py` imports
`pi.apps.van_dashboard.van_dashboard`. The application imports Flask at module
load time and its route tests use Flask's `test_client()`. Therefore the test
module cannot even be imported by a Python environment that lacks Flask.

Check the active Python environment with:

```bash
python3 -c 'import flask; import importlib.metadata as metadata; print(metadata.version("Flask"))'
```

The macOS system Python used during the dashboard work did not have Flask
installed. No virtual environment was created in `scripts`, `scripts_2`, or any
other repository checkout. Do not assume that a neighboring checkout contains
a usable venv, and do not install Flask globally into the macOS system Python.

## Option 1: stage tests temporarily on vanpi (preferred)

Prefer this workflow for dashboard testing. Vanpi already has the runtime
dependencies used by the deployed dashboard, while the temporary staging tree
keeps tests isolated from live application files and services.

The dashboard tests were commonly run with vanpi's existing Python/Flask
installation. This did **not** create a venv, deploy dashboard files, invoke
`sync_scripts.sh`, restart a service, or activate COP ALERT. The relevant source
and test files were copied into a unique directory under `/tmp`, tests ran from
that isolated tree, and the tree was deleted afterward.

From the root of the checkout being tested:

```bash
dashboard_test_dir="$(
  ssh -o BatchMode=yes -o ConnectTimeout=8 pi@vanpi.lan \
    'mktemp -d /tmp/van-dashboard-tests.XXXXXX'
)"

if [[ ! "$dashboard_test_dir" =~ ^/tmp/van-dashboard-tests\.[[:alnum:]]+$ ]]; then
  echo "unexpected remote test path: $dashboard_test_dir" >&2
  exit 1
fi

ssh -o BatchMode=yes pi@vanpi.lan \
  "install -d '$dashboard_test_dir/pi/apps' \
    '$dashboard_test_dir/pi/tests' '$dashboard_test_dir/pi/scripts' \
    '$dashboard_test_dir/pi/van_compute/scripts'"

scp -q -r pi/apps/van_dashboard \
  "pi@vanpi.lan:$dashboard_test_dir/pi/apps/"
scp -q pi/tests/test_van_dashboard.py \
  "pi@vanpi.lan:$dashboard_test_dir/pi/tests/"
scp -q pi/van_compute/__init__.py \
  "pi@vanpi.lan:$dashboard_test_dir/pi/van_compute/"
scp -q pi/van_compute/scripts/__init__.py \
  pi/van_compute/scripts/van_compute_metrics.py \
  "pi@vanpi.lan:$dashboard_test_dir/pi/van_compute/scripts/"
scp -q pi/sync_scripts.sh \
  "pi@vanpi.lan:$dashboard_test_dir/pi/"
scp -q pi/scripts/ntfy_send.sh \
  "pi@vanpi.lan:$dashboard_test_dir/pi/scripts/"
scp -q pi/scripts/usb_watch.py \
  "pi@vanpi.lan:$dashboard_test_dir/pi/scripts/"

ssh -o BatchMode=yes pi@vanpi.lan \
  "cd '$dashboard_test_dir' && \
    python3 -m unittest pi.tests.test_van_dashboard; \
    dashboard_test_status=\$?; \
    find '$dashboard_test_dir' -depth -delete; \
    exit \$dashboard_test_status"
```

The copied `sync_scripts.sh` is only a fixture for a test that inspects its
dashboard asset rules. It is not executed.

If transfer or setup fails before the final command, the temporary directory
may remain. Confirm that its path still matches the validated
`/tmp/van-dashboard-tests.<suffix>` form, then remove that exact tree with:

```bash
ssh -o BatchMode=yes pi@vanpi.lan \
  "find '$dashboard_test_dir' -depth -delete"
```

Do not substitute a broad path, `/tmp`, `$HOME`, or `~` in that cleanup command.

## Option 2: use an isolated local venv

Use a local venv only when vanpi is unavailable or local iteration is
materially more convenient. Create it outside the repository so it cannot be
mistaken for shared project state:

```bash
dashboard_test_tmp="${TMPDIR:-/tmp}"
dashboard_test_tmp="${dashboard_test_tmp%/}"
dashboard_test_venv="$(mktemp -d "$dashboard_test_tmp/van-dashboard-venv.XXXXXX")"
python3 -m venv "$dashboard_test_venv"
"$dashboard_test_venv/bin/python" -m pip install Flask
"$dashboard_test_venv/bin/python" -m unittest pi.tests.test_van_dashboard
```

When finished, validate that the variable still names the temporary venv and
remove that exact tree:

```bash
case "$dashboard_test_venv" in
  "$dashboard_test_tmp"/van-dashboard-venv.*)
    find "$dashboard_test_venv" -depth -delete
    ;;
  *)
    echo "refusing unexpected venv cleanup path: $dashboard_test_venv" >&2
    ;;
esac
```

This repository does not currently pin Flask in a requirements or project
metadata file. If reproducible local environments become important, add and
review a pinned dependency declaration rather than relying on an undocumented
global installation.

## Combined policyctl and dashboard tests

When a dashboard change involves Disks & Torrents, also stage these files:

```text
pi/tests/test_policyctl.py
pi/scripts/policyctl
pi/scripts/disk_policy.sh
```

Then run:

```bash
python3 -m unittest pi.tests.test_policyctl pi.tests.test_van_dashboard
```

`disk_policy.sh` is required because a policyctl test verifies that its managed
disk labels remain consistent with the disk lifecycle policy.

## Copied-checkout warning

`pi/sync_scripts.sh` currently contains an absolute source path for the primary
`scripts` checkout. Running it from `scripts_2`, `scripts_3`, or another copy
can deploy files from the wrong checkout. Use the temporary testing procedure
above for tests. For an intentional dashboard deployment from a copied
checkout, use that checkout's reviewed local deployment helper or copy the
exact dashboard files explicitly; do not assume `sync_scripts.sh` uses the
current directory.
