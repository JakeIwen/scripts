# Van Dashboard testing

[Pi documentation index](../../README.md) · [Deployment and rollback](../deployment.md)

## Local package tests

Use a local venv with Flask; do not install dependencies into macOS system Python.
The production interpreter is Python 3.11. Local verification may use a newer
interpreter, but source must remain 3.11-compatible.

From the selected checkout:

```bash
REPO="/absolute/path/to/your/scripts-checkout"
cd "$REPO"
python3 -m venv /tmp/scripts-venv
/tmp/scripts-venv/bin/python -m pip install Flask
PYTHONDONTWRITEBYTECODE=1 /tmp/scripts-venv/bin/python -m unittest pi.tests.dashboard.test_van_dashboard
PYTHONDONTWRITEBYTECODE=1 /tmp/scripts-venv/bin/python -m unittest pi.tests.test_python_deployment
```

If the venv already exists, skip creation and check Flask with:

```bash
/tmp/scripts-venv/bin/python -c 'import importlib.metadata; print(importlib.metadata.version("Flask"))'
```

Tests import the package, not a flattened sibling copy. `pi.tests` supplies the
repository copy of `van_compute_metrics` under its separately installed runtime
module name, without editing sys.path. That fixture is never a package-deployment
input. For an interactive import outside the test harness, explicitly provide
both source roots:

```bash
PYTHONPATH="$REPO:$REPO/pi/van_compute/scripts" PYTHONDONTWRITEBYTECODE=1 /tmp/scripts-venv/bin/python -P -c 'from pi.apps.van_dashboard import create_app; print(create_app().url_map)'
```

The app factory does not start COP, connectivity or Starlink loops. It is **not**
an arbitrary safe API sandbox: some route calls start work. Unit tests substitute
controllers or injected commands. Tests must patch classes/constants at their
actual feature-module home and shared controller instances at `runtime.py`, not
a re-exporting facade. Do not weaken assertions while updating those targets.

Run each Python test module separately for comparisons with the recorded
baseline; several modules have process-global fixtures:

```bash
for f in $(find pi/tests -name 'test_*.py' | sort); do m=${f%.py}; m=${m//\//.}; printf '%s\n' "$m"; PYTHONDONTWRITEBYTECODE=1 /tmp/scripts-venv/bin/python -m unittest "$m"; done
```

For storage/torrent changes also run the policy and relevant shell suites:

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/scripts-venv/bin/python -m unittest pi.tests.policy.test_policyctl pi.tests.policy.test_policy_deployment
bash pi/tests/policy/test_policy_reconciliation.sh
```

Package deployment tests use temporary local trees and fake service managers.
CLI invocations are `--dry-run` only. They check checkout provenance, allowlist,
imports, corrupt/incomplete archives, preserved flat fallback, explicit activation,
selective restarts and retries. Never point their fixtures at a live release or
invoke a real system service manager from a test.

## React tests

Frontend source and its lockfile remain independently owned:

```bash
cd "$REPO/pi/apps/van_dashboard/frontend"
npm ci --no-audit --no-fund
npm run typecheck
npm test -- --run
npm run build
```

The preview Python tests use a fake upstream, not vanpi:

```bash
cd "$REPO"
PYTHONDONTWRITEBYTECODE=1 /tmp/scripts-venv/bin/python -m unittest pi.tests.dashboard.test_van_dashboard_preview
```

## On-Pi staging

Use the [package runbook](../deployment.md), not ad-hoc copying of a subset of
modules or running the broad sync as a test. Its factory-only loopback smoke has
an explicit alternate port and a restricted request list that avoids hardware
and controller side effects. No on-Pi validation is implied by local tests.

`pi/sync_scripts.sh` intentionally remains pinned to the primary trusted checkout
`/Users/jacobr/dev/scripts`, because broad sync publishes ignored secrets,
configuration and hooks. It refuses all deployment before explicit package
activation (and after rollback). For backend updates from this or another clone,
use that clone's `pi/deploy_python.py` directly: it is checkout-relative, records
provenance and ships no private inputs. Like the scoped preview/video deployers,
it replaces live code with the selected clone's version. Neither deployment path
is a replacement for isolated tests.
