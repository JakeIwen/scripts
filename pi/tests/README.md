# Raspberry Pi test layout

Tests shared by one feature live together:

- `compute/`: compute protocol, broker, deployment, upgrade, and worker tests.
- `dashboard/`: dashboard application, routes, assets, and tile tests.
- `media/`: Movies & TV service tests, including
  `test_video_library_server.py`.
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

Dashboard tests require Flask. See
[`../docs/dashboard/DASHBOARD_TESTING.md`](../docs/dashboard/DASHBOARD_TESTING.md)
for the isolated vanpi and local-venv runners.
