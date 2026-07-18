# Van Dashboard and COP ALERT

`van-dashboard.service` serves the dashboard on port `8788` (all interfaces),
so the same URL works through the LAN hostname or a Tailscale hostname/address:

```text
http://vanpi.lan:8788/
```

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

The speed-test button starts `/home/pi/scripts/speedtest.sh` in a separate
thread. Only one test can run at a time; `POST /api/speedtest` returns
immediately, while `GET /api/speedtest` reports progress and the eventual
download, upload, latency, and completion time. The browser renders completion
as `@ HH:MM:SS` in its local time. The test is never run automatically.

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

## COP ALERT behavior

While active, the dashboard:

- persists the active state in `/home/pi/.van_dashboard_state.json`;
- keeps Home Assistant entity `switch.ext_flood` on while the engine is stopped;
- asynchronously configures `light.ext_led` to match the current brightness and
  color temperature of `light.solder_led`, then reads it back for confirmation;
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

At the start of each activation the worker reads `light.solder_led`, the light
powered by `switch.solder_flood`. The settings captured on 2026-07-18 were
brightness `170/255` (67%) and `2702 K` in color-temperature mode. Those values
are the fallback if the reference light cannot be read, and can be overridden
with `VAN_DASHBOARD_COP_LED_FALLBACK_BRIGHTNESS` and
`VAN_DASHBOARD_COP_LED_FALLBACK_KELVIN`. The worker pauses while verified engine
RPM causes COP ALERT to intentionally turn `ext_flood` off, then resumes when
the engine stops.

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
