# Raspberry Pi test layout

Tests shared by one feature live together:

- `compute/`: compute protocol, broker, deployment, upgrade, and worker tests.
- `dashboard/`: dashboard application, routes, assets, and tile tests.
- `media/`: Movies & TV service, durable identity/catalog, qBittorrent bridge,
  rollback deployment, and incomplete-to-final integration tests.
- `network/`: connectivity collection and UBNT Wi-Fi tests.
- `policy/`: storage/torrent policy CLI, reconciliation, watchdog, and deployment tests.
- `price_check/`: price-check application and cron-schedule tests.
- `storage/`: disk mounting, disk controls, and Samba mount/share safeguards.

Standalone test files remain directly under `pi/tests/`.

Python suites can be run by module, for example:

```bash
python3 -m unittest \
  pi.tests.policy.test_policyctl \
  pi.tests.price_check.test_price_cron_schedule
```

Shell suites are invoked by their repository path, for example:

```bash
bash pi/tests/storage/test_mount_disks.sh
bash pi/tests/policy/test_policy_reconciliation.sh
```

The bash shell tests under `pi/tests/storage/` source `pi/tests/lib.sh`
for the shared `fail` and `assert_eq` helpers. Each test keeps its own
`set -u`, temp dir, and trap.

The frozen `umount_disks.sh` phase regression needs Bash 4 or newer. It checks
`bash` on `PATH`, then `/opt/homebrew/bin/bash` and `/usr/local/bin/bash`; if no
suitable interpreter is available, the golden cases skip by default. Set
`UMOUNT_PHASE_TEST_REQUIRE=1` to make that condition fail the test run instead,
or set `UMOUNT_PHASE_TEST_BASH` to an explicit interpreter path. For example:

```bash
UMOUNT_PHASE_TEST_REQUIRE=1 python3 -m unittest \
  pi.tests.storage.test_umount_disks_phases_golden
```

Dashboard tests require Flask. See
[`../docs/dashboard/DASHBOARD_TESTING.md`](../docs/dashboard/DASHBOARD_TESTING.md)
for the isolated vanpi and local-venv runners.
