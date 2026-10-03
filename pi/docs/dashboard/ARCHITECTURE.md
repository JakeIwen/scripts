# Van dashboard backend architecture

[Pi documentation index](../../README.md) · [Testing](DASHBOARD_TESTING.md)

`pi/apps/van_dashboard/van_dashboard.py` owns `create_app()` and production
startup. It registers feature blueprints rather than re-exporting controller
implementations. The package entry point runs that lifecycle with
`python3 -P -m pi.apps.van_dashboard`.

Controller singletons have one explicit process-level home in `runtime.py`.
Blueprints resolve them at request time through named proxies, preserving shared
identity and replacement semantics. Tests import controller definitions from
their feature module and patch mutable instances at their runtime home. Factory
calls create distinct Flask apps but intentionally share this process's hardware
owner; they do not start its background loops. HTTP helpers live in `http.py`;
blueprints live in `routes/`, one per existing domain. Keeping Flask out of the
controller modules preserves the relay-off CLI's minimal dependency path.

Controller implementations are grouped by capability:

- `van_dashboard_common.py`: environment configuration, state storage, and
  low-level command/file helpers.
- `van_dashboard_cop.py`: COP requested intent, exterior alert maintenance,
  and the read-only CAN-wake supervisor status boundary.
- `van_dashboard_home.py`: Tuya switches and Home Assistant lighting.
- `van_dashboard_sonos.py`: Sonos grouping, transport, volume, and album art.
- `van_dashboard_network.py`: cached connectivity, OpenWrt clients, UBNT Wi-Fi,
  and speed tests.
- `van_dashboard_projects.py`: validated user-added project links in StateStore;
  the `projects` blueprint in `routes/projects.py` owns GET/POST
  `/api/hosted-projects`, sharing additions across dashboard devices.
  `runtime.hosted_projects` is initialized immediately after `state_store`.
- `van_dashboard_usb.py`: USB inventory and guarded hub/port control.
- `van_dashboard_storage.py`: requested disk/torrent policy.
- `van_dashboard_backups.py`: backup evidence and guarded manual jobs.
- `van_dashboard_disks.py`: managed USB-disk status and lifecycle actions.
- `van_dashboard_block_devices.py`: pure shared `lsblk` tree helpers.
- `van_dashboard_integrations.py`: price-watch and system-monitor clients.
- `van_dashboard_system.py`: ignition-monitor, restart, uptime, and power
  controllers.
- `van_dashboard_telemetry.py`: read-only battery summaries and guarded voltage
  checks. Fresh broker telemetry wins; otherwise it validates the broker's
  bounded regular-file engine-off sample and the scheduled `voltage_mon` CSV,
  then returns whichever observation timestamp is newer.
- `van_dashboard_vonstar.py`: fixed-action, intent-only client for the private
  Vonstar Unix service; no CAN implementation or caller-selected vehicle data.

Controller dependency direction remains common/pure helpers, then domains, then
runtime composition. Existing controller bodies and their command, clock and
path injection seams are unchanged. Route functions are grouped in a matching
`routes/<feature>.py` module; `common` serves the static shell, `cop` owns the aggregate status
route, and `integrations` owns compute/price/system-monitor routes. The pure
block-device module has no HTTP routes. Blueprint endpoint names are namespaced;
URL paths and methods are unchanged.

## Package deployment

[Deployment and rollback](../deployment.md) defines the allowlisted immutable
release, explicit first activation, separate compute/frontend ownership and
preserved pre-cutover flat fallback. Dashboard imports are package-only; there
is no star-import facade or sys.path mutation. Tests exercise imports from a
staged package outside the checkout. The separately installed compute metrics
module is a test fixture dependency, not part of the package payload.

## Deliberate limits

Do not instantiate another production lifecycle beside the live hardware owner.
The runbook's factory-only localhost smoke restricts requests to non-controller
routes; arbitrary API requests can still have effects even without background
loops. The existing per-domain workers retain their retry, single-flight,
secret-handling, restoration and allowed-return-code behavior rather than being
hidden behind a universal controller abstraction.

## React frontend

React is the sole dashboard frontend. The production Flask process serves its
entry point at `/` and hashed assets at `/assets/`; it returns HTTP 503 if the
atomic React build is unavailable. The same process remains the sole API and
controller owner, so the frontend does not create a second dashboard backend.

The parallel React service on port `8790` remains available as a canary and
rollback aid. Both ports serve the same React release and consume the same
backend state and API contracts.
