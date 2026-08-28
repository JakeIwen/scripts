# Van Dashboard React frontend

This directory contains the human-editable React preview for the van dashboard.
It is deliberately a client-side application: the existing Flask dashboard on
port `8788` remains the only backend and the only process that owns device or
service controls.

The preview is served on port `8790` by `react_dashboard_preview.py`. That small
server serves the production build and proxies `/api/*` to the existing Flask
backend. It does not import dashboard controllers or create a second copy of
their runtime state.

## Local commands

Run these from this directory:

```bash
npm install
npm run dev
npm run typecheck
npm test
npm run format
npm run format:check
npm run build
```

The Vite development server listens on `127.0.0.1:5173` and proxies API calls
to `http://vanpi.lan:8788` by default. Override that only when deliberately
testing another backend:

```bash
VAN_DASHBOARD_BACKEND_ORIGIN=http://127.0.0.1:8788 npm run dev
```

## Source layout

```text
src/
  api/          transport and small JSON-validation primitives
  components/   reusable, presentation-only UI pieces
  dashboard/    tile ordering and grid behavior
  features/     one explicit folder per dashboard domain
  hooks/        generic polling and single-flight mechanics
  styles/       global tokens, reset, and shared component layout
  utils/        display formatting without domain policy
```

Each feature normally contains:

- `types.ts`: the smallest useful domain model;
- `decoders.ts`: the runtime trust boundary from unknown JSON to that model;
- `api.ts`: exact endpoint paths and forms;
- `hooks.ts`: polling owned by that domain;
- a `*Tile.tsx` summary and optional `*Sheet.tsx` details;
- a feature CSS file and focused tests;
- `index.ts`: the public exports used by `App.tsx`.

Keep domain components explicit. A generic JSON-driven tile renderer would make
small edits harder to understand and would hide the substantial behavioral
differences between COP ALERT, USB, backups, networking, and media controls.

## Data and polling rules

- Treat API JSON as `unknown` until a domain decoder validates it.
- Keep money as decimal text and preserve nullable timestamps.
- GET requests use `cache: 'no-store'`.
- POST requests remain URL-encoded and include `X-Van-Dashboard: 1`.
- Poll sequentially: schedule the next request only after the current request
  settles. Slow requests must never stack.
- Pause ordinary polling while the page is hidden and refresh immediately when
  it becomes visible again.
- Keep the last valid data visible when a later refresh fails.
- Never automatically retry an ambiguous physical mutation.
- After any mutation failure, refresh the authoritative resource because a
  timeout or 502 does not prove that nothing changed.

Domain-specific convergence loops belong next to the control that needs them.
Do not force COP arming, dashboard restart, speed tests, UBNT operations, and
voltage checks through one configurable polling abstraction.

## Styling and accessibility

Global colors and dimensions live in `styles/tokens.css`. Shared tile, dialog,
button, and responsive layout lives in `styles/dashboard.css`; domain-specific
CSS stays beside its feature.

Use semantic buttons, links, lists, and descriptions before adding ARIA. The
shared `BottomSheet` uses a native dialog and supplies its accessible name,
Escape handling, backdrop closing, and focus behavior. Test controls by role and
visible label rather than by CSS selector.

The existing tile IDs and `van-dashboard.tile-order.v1` localStorage key are a
compatibility contract. Reusing them preserves the user's saved arrangement.

## Preview safety state

`react_dashboard_preview.py` proxies GET and HEAD requests and only the exact
method/path pairs listed in `ALLOWED_MUTATIONS`. Every current React control is
listed individually after review of its disabled states, confirmation,
single-flight behavior, ambiguous-failure refresh, convergence, and tests.
There is no wildcard or method-wide mutation switch.

The legacy UI remains linked in the header as an immediate behavioral and
operational fallback during the parallel-service review window.

## Build and deploy

The Pi never runs Node or Vite. Build and deploy from this clone with:

```bash
./pi/deploy_van_dashboard_preview.sh
```

Rollback swaps the atomic preview release symlinks and restarts only the preview
service:

```bash
./pi/deploy_van_dashboard_preview.sh --rollback
```

Do not use `pi/sync_scripts.sh` from this clone; it is intentionally tied to the
primary checkout and deploys much more than the preview.
