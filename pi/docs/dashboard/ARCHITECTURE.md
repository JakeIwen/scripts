# Van dashboard backend architecture

[Pi documentation index](../../README.md) · [Testing](DASHBOARD_TESTING.md)

The backend keeps `pi/apps/van_dashboard/van_dashboard.py` as a thin compatibility
facade and composition root. It owns the Flask application, process-level
singletons, HTTP routes, telemetry-service toggle wrappers, static assets, and
the startup lifecycle. Existing tests and callers can continue importing
`pi.apps.van_dashboard.van_dashboard` and replacing route-level singleton
objects.

Controller implementations are grouped by capability:

- `van_dashboard_common.py`: environment configuration, state storage, and
  low-level command/file helpers.
- `van_dashboard_cop.py`: COP requested intent, exterior alert maintenance,
  and the read-only CAN-wake supervisor status boundary.
- `van_dashboard_home.py`: Tuya switches and Home Assistant lighting.
- `van_dashboard_sonos.py`: Sonos grouping, transport, volume, and album art.
- `van_dashboard_network.py`: cached connectivity, OpenWrt clients, UBNT Wi-Fi,
  and speed tests.
- `van_dashboard_usb.py`: USB inventory and guarded hub/port control.
- `van_dashboard_storage.py`: requested disk/torrent policy.
- `van_dashboard_backups.py`: backup evidence and guarded manual jobs.
- `van_dashboard_disks.py`: managed USB-disk status and lifecycle actions.
- `van_dashboard_block_devices.py`: pure shared `lsblk` tree helpers.
- `van_dashboard_integrations.py`: price-watch and system-monitor clients.
- `van_dashboard_system.py`: ignition-monitor, restart, uptime, and power
  controllers.
- `van_dashboard_telemetry.py`: read-only battery summaries and guarded voltage
  checks.

The dependency direction is intentionally one-way: common and pure helpers,
then domain controllers, then the facade. Domain modules do not import the
facade or Flask routes. Constructors retain their command, clock, and path
injection seams, while safety-sensitive validation and error policies remain
inside their domain rather than being hidden behind a universal command
abstraction.

## Flat deployment compatibility

`pi/sync_scripts.sh` currently flattens Python application files into
`/home/pi/scripts/python-automation/`, and systemd executes the facade directly.
For that reason every backend filename has a globally unique
`van_dashboard_` prefix and every domain import explicitly supports both the
repository package layout and deployed sibling layout. Each required module is
also an `ExecStartPre` dependency in `van-dashboard.service`, ensuring that the
service updater notices module-only deployments and restarts the application.

The test suite exercises both import layouts. Do not introduce an unconditional
relative import or a generic unprefixed module without updating the deployment
contract and its flat-layout smoke test.

## Deliberate limits

Routes remain in the facade for now. Moving them into Blueprints would require
an explicit dependency container because tests and maintenance tools replace
facade singletons at runtime. Controller extraction provides most of the
readability benefit without combining that work with an application-factory or
route-wiring redesign.

Likewise, background-operation loops remain domain-specific. Their retry,
single-flight, secret-handling, restoration, and allowed-return-code semantics
differ enough that a shared worker superclass would obscure important behavior.

## React preview

The replacement frontend is maintained under
`pi/apps/van_dashboard/frontend/` and is introduced through the separate
[React preview service](REACT_PREVIEW.md). It consumes the same Flask API through
a narrow loopback proxy; it does not create another backend or import these
controller modules.
