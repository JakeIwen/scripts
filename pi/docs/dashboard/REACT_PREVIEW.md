# React dashboard frontend

[Backend architecture](ARCHITECTURE.md) · [Dashboard testing](DASHBOARD_TESTING.md)

The production dashboard and parallel canary share one atomic React build:

```text
8788  van-dashboard.service          React UI, Flask API, and /legacy fallback
8790  van-dashboard-preview.service  React canary and narrow API proxy
```

The production Flask process remains the sole dashboard manager and API owner.
It serves React at `/`, Vite assets at `/assets/`, and the previous template at
`/legacy`.

The preview service owns no dashboard managers, runtime directory, device
access, or CAN access. Browser requests to `/api/*` are proxied to the existing
backend on loopback. The proxy preserves the browser's original `Host`,
`Origin`, `Referer`, and `X-Van-Dashboard` headers so the production same-origin
and CSRF checks remain authoritative.

Mutating routes use an exact method-and-path allowlist in
`react_dashboard_preview.py`; there is no wildcard or method-wide enable
switch. The current allowlist covers the migrated controls for media, lighting,
telemetry, speed tests, Deal Watch, ignition, storage, USB, backups, UBNT,
Starlink, COP ALERT intent, vOnStar's fixed confirmed requests, dashboard
restart, reboot, and power-down.

## Deployment

From the active reviewed checkout:

```bash
./pi/deploy_van_dashboard_preview.sh
```

The helper runs `npm ci`, typechecks, builds on the Mac, transfers only the
preview release and service unit, switches an atomic `current` symlink, and
checks both `/healthz` and the proxied `/api/status` endpoint.

Rollback requires a prior preview release:

```bash
./pi/deploy_van_dashboard_preview.sh --rollback
```

Emergency disable removes only the parallel `8790` canary; React on `8788`
continues to be served by Flask:

```bash
ssh pi@vanpi.lan \
  'sudo systemctl disable --now van-dashboard-preview.service'
```

The build output is not committed. Source, dependency lockfile, proxy, service
unit, deployment helper, and tests are the reviewed artifacts.

## Migration rule

Keep the legacy dashboard available during the parallel review and rollback
window even after feature parity. Any new control still follows the original
risk order: read-only rendering, reversible controls, operational controls,
then physical or availability-sensitive actions last.
