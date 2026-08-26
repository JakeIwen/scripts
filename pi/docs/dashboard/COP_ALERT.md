# Van Dashboard and COP ALERT

[Pi documentation index](../../README.md)

`van-dashboard.service` serves the dashboard on port `8788` (all interfaces),
so the same URL works through the LAN hostname or a Tailscale hostname/address:

```text
http://vanpi.lan:8788/
```

The Flask composition root and compatibility facade is
`pi/apps/van_dashboard/van_dashboard.py`; cohesive controller modules live
beside it as described in [the backend architecture](ARCHITECTURE.md). Its page,
stylesheet, and browser code live under `templates/` and `static/`.
`pi/sync_scripts.sh` deploys the modules and assets beside the Python entry
point.

The Audiobooks and Movies & TV tiles preserve the current host and change only
the port: Audiobooks uses `8787`, while the Movies & TV service uses `8789`.
Both links therefore work through the LAN hostname or the Tailscale
hostname/address used to open the dashboard.

The service has no user-login layer; it is intended only for the trusted van
LAN and Tailscale ACL. The Movies & TV service is likewise intended only for
those trusted networks. Do not forward ports `8788` or `8789` from a public
interface. Both control surfaces reject cross-origin browser mutations to
reduce CSRF risk.

## OpenWrt and speed tests

OpenWrt/MWAN3 and the speed test use separate half-width cards. The three MWAN3
interface states are stacked beside the active mode. The OpenWrt timestamp and
error label describe the MWAN3 collector data still shown on that card.

The separate UBNT availability and wireless columns were removed. The UBNT
Wi-Fi tile's top badge and green tile state describe physical Ethernet
reachability, while the dot beside `UBNT Wi-Fi` describes radio association.
A reachable antenna configured for an absent SSID therefore shows `CONNECTED`
with a red Wi-Fi dot and `Not associated` detail. Signal and CCQ/quality data
appear when the radio is associated.

MWAN3 interface health comes from mwan3's existing reachability tracking. The
route shown beneath `MWAN3 route` comes from the active members of the router's
configured IPv4 default policy. For example, when both `clientwan` and `wan`
are healthy but the `balanced` policy has selected the preferred client path,
the route reads `clientwan` while both interface chips remain green. A genuinely
weighted policy shows each eligible member's percentage. Existing connections
may remain pinned to their original member until conntrack expires.

`/home/pi/scripts/connectivity_status.py` is a reusable, standard-library JSON
collector. It performs one read-only SSH query containing `mwan3 interfaces`,
`mwan3 policies`, and the configured IPv4 default-policy name per run, one UBNT
ping, and (only when the UBNT responds) one read-only radio-status SSH query. It
never scans for networks. With no visible dashboard, the server runs it in a
background thread every 30 seconds and serves cached results through
`GET /api/connectivity`. A visible page renews a short activity lease with
`GET /api/connectivity?active=1`. While that lease is current, the single
background worker starts its next collection as soon as the previous collection
finishes. Multiple open dashboards share the same lease and worker, so slow
router or UBNT calls cannot overlap or build up a queue. When the last page is
hidden or closed and the lease expires, the worker returns to its 30-second
cadence.

The page reads the cache once per second while visible, with browser caching
disabled; these reads never run the collector in an HTTP request thread. A
collector failure uses a one-second retry delay to avoid a tight failure loop.
A Starlink power or UBNT configuration change can also wake the background
collector. Hosts, key path, and command paths can be overridden with the
collector's `CONNECTIVITY_*` environment variables.

The speed-test button starts `/home/pi/scripts/speedtest.sh` in a separate
thread. Only one test can run at a time; `POST /api/speedtest` returns
immediately, while `GET /api/speedtest` reports progress and the eventual
download, upload, latency, and completion time. The browser renders completion
as `@ HH:MM:SS` in its local time. The test is never run automatically.

## UBNT Wi-Fi selection

The UBNT Wi-Fi tile uses `/home/pi/scripts/ubnt_wifi.py`, a reusable
standard-library JSON interface to the antenna's tracked `wifi_manager.sh`.
Opening its sheet reads the current association and cached observations; Scan
starts the antenna's existing locked, three-pass site scan in a background
thread. Known visible profiles are green and can be selected directly.

Unknown WPA/WPA2 Personal networks request a password; open networks require no
password. WEP and enterprise networks are displayed but not configurable. New
credentials are POSTed same-origin, supplied to the Pi tool over standard input,
and then supplied to the antenna over SSH standard input. They are never placed
in URLs, process arguments, output, or logs. Successful association explicitly
runs the manager's `save-current`/`cfgmtd` path so the full airOS profile and its
credential survive reboot.

Manual selections pause the antenna's automatic selector until Resume automatic
selection is pressed. This permits captive-portal login even though Internet
reachability initially fails. Scans, switches, and provisioning are single-flight
background operations; their POST endpoints return immediately and the sheet
polls authoritative status rather than changing the selected network
optimistically.

## Starlink power

The Starlink tile reads `switch.starlink` through the existing Tuya/Home
Assistant helpers every 15 seconds. Confirmed on is green, confirmed off is
red, and unavailable/unread status is neutral grey. The tile is disabled while
status is unknown so it never guesses a toggle direction. After a power change,
the dashboard reads the authoritative switch state again; failed verification
returns the tile to grey.

The same neutral/failed/live color convention applies to dashboard status
icons: grey means no data yet, red means confirmed down or failed, and green
means confirmed active or reachable.

## Sonos controls

The Sonos tile follows the selected group's coordinator and shows the current
track, artist, live track-position bar, and play/pause/previous/next controls.
When available, coordinator album art is fetched through a bounded same-origin
vanpi proxy so it also works over Tailscale. Its speaker sheet keeps the existing
group-selection checkboxes and adds native Sonos group volume, whole-group mute,
individual volume, and individual mute controls.

## Disk and torrent policy

The Disks & Torrents tile reads and updates state exclusively through
`/home/pi/scripts/policyctl`; the dashboard never reads the policy JSON, mount
table, or process list directly. The sheet keeps requested disk, global torrent,
and Starlink torrent permissions separate from policyctl's authoritative
runtime report of managed mounts and the exact `qbittorrent-nox` process.
Updates use fixed commands, request reconciliation, and then refresh the same
status contract.

Ignition state always overrides requested disk permission, and disabling disks
also stops torrents. Starlink torrenting requires both the global Torrents
enabled setting and Allow torrents on Starlink; the Starlink permission never
overrides the global switch. A successful Starlink power change requests policy
reconciliation immediately.

## COP ALERT behavior

While active, the dashboard:

- persists the active state in `/home/pi/.van_dashboard_state.json`;
- publishes requested intent through `/run/van-dashboard/cop-alert.active`;
- keeps Home Assistant entity `switch.ext_flood` on while the ignition marker is
  absent;
- asynchronously configures `light.ext_led` to fixed brightness `255/255` and
  `2702 K`, then reads it back for confirmation;
- starts a single-flight background send of `🥓 COP ALERT is active` through
  `ntfy_send.sh` immediately on activation and every five minutes. A bounded
  curl timeout, exception containment, and retry scheduling keep a disconnected
  network from blocking the request or the COP-alert maintenance loop.

Turning COP ALERT off removes the requested-intent marker, stops ntfy messages,
and turns `ext_flood` off. If the service restarts while the persisted state is
active, it republishes the marker and resumes the exterior alert behavior.

The dashboard is CAN-free: it does not import SocketCAN or ISO-TP, inspect a
CAN interface, select a channel, or call the wake library. The separately
managed `van-cop-can-wake.service` in `/home/pi/dev/obd-things` is the only
maintained bridge from the requested-intent marker to guarded vehicle-network
wake operations.

The dashboard reads that supervisor's transition-driven status from
`/run/van-cop-can-wake/status.json`. It checks the service is active, uses
`lstat`, refuses symlinks and non-regular or oversized files, verifies the
schema/service/C-CAN role, and exposes only explicitly allowed fields through
`/api/status`. The dashboard never writes this file. An old `generated_at` is
not considered stale because the supervisor updates the file on transitions,
not as a heartbeat. The tile distinguishes requested COP intent from CAN-wake
execution with IDLE, ARMING, WAKING, ACTIVE, BLOCKED, PAUSED, and OFFLINE states;
the last blocked reason, detail, and timestamp remain visible after disarming.
After a successful intent change, the browser briefly polls the ordinary status
API at 500 ms intervals, then returns to its normal five-second refresh.

## Exterior LED matching

The COP ALERT request is not blocked by the exterior light. A separate worker
waits for `light.ext_led` to join Wi-Fi after `ext_flood` supplies power, then
uses `tuya_light.sh` to apply and read back the desired light settings. It
retries every five seconds while the target is unavailable and re-verifies a
confirmed light every 30 seconds. The tile shows `Waiting for ext_led Wi-Fi`
during a 90-second connection grace period, then `ext_led unavailable · still
retrying` if RF remains blocked. Neither state disables the alert, CAN wake,
`ext_flood`, or ntfy behavior.

The fixed look—brightness `255/255` (100%) and `2702 K` in color-temperature
mode—was captured from `light.solder_led` on 2026-07-18. The worker never reads
or depends on `solder_led` during COP ALERT. It pauses while the ignition marker
is present and resumes when the marker clears.

## Dependencies

The system Python used by the service needs `flask` and `soco`.
The existing speed-test script needs `speedtest-cli`; the connectivity
collector adds no Python packages or router-side software.
The existing `tuya_toggle.sh`, `tuya_status.sh`, and `ntfy_send.sh` scripts plus
the new `tuya_light.sh` helper use the existing secret files for Home
Assistant/ntfy integrations; the dashboard contains no credentials.
