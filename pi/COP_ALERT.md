# Van Dashboard and COP ALERT

`van-dashboard.service` serves the dashboard on port `8788` (all interfaces),
so the same URL works through the LAN hostname or a Tailscale hostname/address:

```text
http://vanpi.lan:8788/
```

The Flask backend is `automation/van_dashboard.py`; its page, stylesheet, and
browser code live under `automation/templates/` and `automation/static/`.
`pi/sync_scripts.sh` deploys those directories beside the Python entry point.

The Audiobooks tile preserves the current host and changes only the port to
`8787`, so it also works over Tailscale.

The service has no user-login layer; it is intended only for the trusted van
LAN and Tailscale ACL. Do not forward port `8788` from a public interface. The
server rejects cross-origin browser control requests to reduce CSRF risk.

## Connectivity and speed tests

The Connectivity card keeps three related states separate:

- MWAN3 comes from mwan3's existing reachability tracking;
- UBNT Availability is a single ping that detects lost Ethernet/power; and
- UBNT Wireless requires a real access-point association. A configured SSID
  is still shown when the radio says `Not-Associated`, but it is not reported
  as connected. When associated, signal, link quality/CCQ, and bitrate are
  available to the dashboard.

The mwan3 mode lists every online uplink (for example `clientwan + wan`) so a
balanced multi-uplink state is not mislabeled as a single primary route.

`/home/pi/scripts/connectivity_status.py` is a reusable, standard-library JSON
collector. It performs one read-only `mwan3 interfaces` SSH query per run, one
UBNT ping, and (only when the UBNT responds) one read-only radio-status SSH
query. It never scans for networks. The dashboard runs it in a background
thread every 30 seconds and serves cached results through
`GET /api/connectivity`, so browser polling adds no router load. Hosts, key
path, and command paths can be overridden with the collector's
`CONNECTIVITY_*` environment variables.

The page reads that cache every 10 seconds with browser caching disabled. It
also reads immediately when a suspended tab becomes visible or focused, and a
Starlink power change wakes the background collector without running router or
UBNT commands in the HTTP request thread.

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
- keeps Home Assistant entity `switch.ext_flood` on while the engine is stopped;
- asynchronously configures `light.ext_led` to fixed brightness `255/255` and
  `2702 K`, then reads it back for confirmation;
- sends an RF Hub `22 F190` identification read every 15 seconds on C-CAN. This
  is the already-verified parked C-CAN wake that powers the dash accessory rail;
- sends `🥓 COP ALERT is active` through `ntfy_send.sh` immediately and every
  five minutes, which resolves `NTFY_MESSAGE_URL` from the existing secrets;
- passively reads C-CAN engine speed and publishes short-lived marker files in
  `/run/van-dashboard` for the ignition hook.

Turning COP ALERT off stops CAN wake and ntfy messages and turns `ext_flood`
off. If the service restarts while the persisted state is active, it resumes
the alert behavior.

The dashboard never changes `can0` bitrate, listen-only mode, or link state.
The C-CAN wake therefore requires the shared interface to already be UP,
armed, and at 500 kbit/s. A mismatch is shown as a degraded CAN wake rather
than reconfiguring the interface out from under another CAN service.

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
or depends on `solder_led` during COP ALERT. It pauses while verified engine RPM
causes COP ALERT to intentionally turn `ext_flood` off, then resumes when the
engine stops.

## Engine-running gate

The gate requires two standard C-CAN engine-speed broadcasts, each using bytes
0-1 big-endian: `0x0F4` divided by 8 and `0x0FC` divided by 4. In the labeled
captures inspected on 2026-07-17:

- ignition on, engine stopped: `0x0F4` was `0` in all 2,000 samples and `0x0FC`
  was `0` in all 1,999 samples;
- engine running: `0x0F4` was `0x1780`, or 752 RPM, in all 6,000 samples;
  `0x0FC` varied from 721-773 RPM with a 752 RPM median across 5,999 samples.

`0x0FC`'s natural idle variation supports using it as the actual-speed
evidence; requiring the matching `0x0F4` broadcast as well prevents either
field alone from releasing `ext_flood`.

The dashboard requires five consecutive plausible samples from 300-8,000 RPM
and treats the evidence as stale after 2.5 seconds. `ignition_on.sh` reads the
persisted COP ALERT state and may turn `ext_flood` off during COP ALERT only
while that fresh engine marker exists. Missing, stale, or ambiguous CAN data
preserves the switch, including across a dashboard restart. When the engine
stops, COP ALERT resumes `ext_flood` and parked CAN wakes.

## B-CAN follow-up

COP ALERT intentionally supports only the currently connected C-CAN path.
Before supporting a PCAN moved to B-CAN:

1. Wire in the existing verified B-CAN wake primitive from
   `/home/pi/dev/obd-things/lib/canbus.py`: 75 benign `0x7FF` DLC-0 frames at
   20 ms spacing on the verified 125 kbit/s bus, followed by passive restore.
2. Resolve shared-interface ownership so the dashboard cannot race another
   service by changing bitrate or listen-only state.
3. Derive and validate a true engine-running/RPM field from the labeled B-CAN
   `ignition_on.log` and `engine_on.log` captures. Do not substitute bus
   activity, ignition state, or charging voltage for actual engine-running
   evidence.
4. Add capture-backed tests equivalent to the dual C-CAN RPM gate before B-CAN
   is allowed to release `ext_flood`.

## Dependencies

The system Python used by the service needs `flask`, `soco`, and `can-isotp`.
The existing speed-test script needs `speedtest-cli`; the connectivity
collector adds no Python packages or router-side software.
The existing `tuya_toggle.sh`, `tuya_status.sh`, and `ntfy_send.sh` scripts plus
the new `tuya_light.sh` helper use the existing secret files for Home
Assistant/ntfy integrations; the dashboard contains no credentials.
