# React van dashboard

[Backend architecture](ARCHITECTURE.md) · [Dashboard testing](DASHBOARD_TESTING.md)

React is the sole van-dashboard frontend. Both listeners use one atomic build:

```text
8788  van-dashboard.service          React UI and authoritative Flask API
8790  van-dashboard-preview.service  React canary and narrow API proxy
```

The Flask process on port 8788 is the only dashboard manager and controller
owner. It serves the React entry point at `/`, hashed Vite assets at `/assets/`,
and all authoritative `/api/*` routes. If the React build is missing, `/`
returns HTTP 503 rather than substituting another control surface.

The port-8790 canary owns no dashboard managers, runtime directory, device
access, or CAN access. It serves the same React build and proxies browser API
requests to the backend on loopback. The proxy preserves the original `Host`,
`Origin`, `Referer`, and `X-Van-Dashboard` headers so the production same-origin
and CSRF checks remain authoritative.

Mutating canary requests use the exact method-and-path allowlist in
`react_dashboard_preview.py`; there is no wildcard or method-wide enable
switch. Every entry corresponds to a reviewed React control with explicit
confirmation, single-flight behavior, and authoritative reconciliation.

## Deployment

From the active reviewed checkout:

```bash
./pi/deploy_van_dashboard_preview.sh
```

The helper runs `npm ci`, typechecks, builds on the Mac, transfers only the
versioned React release and canary service unit, switches an atomic `current`
symlink, and checks both `/healthz` and the proxied `/api/status` endpoint. The
production Flask service reads that same `current` release, so the atomic switch
updates both ports without introducing a second backend.

Rollback swaps the shared `current` and `previous` release symlinks:

```bash
./pi/deploy_van_dashboard_preview.sh --rollback
```

Emergency canary disable leaves the production React dashboard on 8788 intact:

```bash
ssh pi@vanpi.lan \
  'sudo systemctl disable --now van-dashboard-preview.service'
```

Build output is not committed. Human-editable React source, the dependency
lockfile, proxy, service unit, deployment helper, and tests are the reviewed
artifacts.
