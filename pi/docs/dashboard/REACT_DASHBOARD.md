# React van dashboard

[Backend architecture](ARCHITECTURE.md) · [Dashboard testing](DASHBOARD_TESTING.md)

React is the sole van-dashboard frontend. `van-dashboard.service` on port 8788
serves it from an atomic release directory:

```text
/home/pi/scripts/van-dashboard-frontend/
  releases/<release-id>/   index.html, assets/, BUILD_ID
  current -> releases/<release-id>
  previous -> releases/<prior-release-id>
```

The dashboard unit's `VAN_DASHBOARD_FRONTEND_ROOT` and `ExecStartPre` name
`current`, so the dashboard cannot start without it.

The Flask process on port 8788 is the only dashboard manager and controller
owner. It serves the React entry point at `/`, hashed Vite assets at `/assets/`,
and all authoritative `/api/*` routes. If the React build is missing, `/`
returns HTTP 503 rather than substituting another control surface.

## Deployment

From the active reviewed checkout:

```bash
./pi/deploy_van_dashboard_frontend.sh
```

The helper runs `npm ci`, typechecks, builds on the Mac, transfers only the
versioned React release, and switches the atomic `current` symlink. Flask
resolves `current` on every request, so the switch needs no restart. The helper
then checks that port 8788 serves the new `index.html` byte for byte and that
`/api/status` answers. It needs no sudo on the Pi.

Rollback swaps the `current` and `previous` release symlinks and runs the same
check:

```bash
./pi/deploy_van_dashboard_frontend.sh --rollback
```

The network-storage installer also publishes a release into this directory
when it ships a frontend build; see the
[flight recorder runbook](../networking/NETWORK_FLIGHT_RECORDER.md).

Build output is not committed. Human-editable React source, the dependency
lockfile, the deployment helper, and tests are the reviewed artifacts.
