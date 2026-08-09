# Dendelion 5 GHz scan gate

Status: the standalone service is implemented, default-disabled, and tested
offline. It has not been installed, enabled, or deployed to `dendelion`.

This is the no-flash implementation of the bounded scan-gating experiment. It
runs beside stock OpenWrt 25.12.5 as a small root `procd` service; it does not
replace `wpad`, mac80211, the MediaTek driver, or the separate 5 GHz power-limit
patch.

UCI configuration alone cannot express this behavior. UCI can pin channels and
enable interfaces, but it cannot sequence a watchdog, an acknowledged
supplicant `DISCONNECT`, fallback AP verification, and a later bounded retry.
The service supplies that state machine using existing public ubus methods.

## Behavior

The installed configuration remains off until `enabled` is deliberately set to
`1`. When enabled, the service does the following:

1. While `lion_fone` is connected, stock OpenWrt owns normal AP/STA channel
   following and `dendelion_5g` uses the station's live channel.
2. A scan or disconnect opens one 15-second connecting window. The 5 GHz AP is
   stopped before that window and duplicate events cannot extend its watchdog.
3. If the station completes, every gate timer is cancelled and stock OpenWrt
   restores the AP on the station's actual channel. The service audits that
   state and repairs a missing AP at the live station frequency before
   verifying it.
4. If no usable network appears, the watchdog expires, or association fails,
   the service issues `DISCONNECT`, waits, and verifies an authoritative parked
   supplicant state.
5. Only after that verification does it start `dendelion_5g` at the configured
   non-DFS fallback channel and verify hostapd reports `ENABLED` at the expected
   frequency.
6. After 60 seconds it stops the AP, flushes the station BSS cache, issues
   `RECONNECT`, and opens another bounded connecting window.

A failed or unverified disconnect never advertises a phantom fallback AP.
After three failed parking attempts the policy enters `fault`, issues
`RECONNECT`, and returns control to stock OpenWrt. A late `COMPLETED` state wins
over parking in every phase.

## Safety boundaries

The daemon refuses to operate unless all of these conditions are true:

- the board is `linksys,e8450-ubi` running OpenWrt 25.12.5;
- `radio1` is stably up with a fixed non-DFS 5 GHz channel;
- `wireless.radio1.scan_list` is absent, so retries are full-band scans;
- `wifinet4` is the only active station on that radio, is non-MLO, and belongs
  to `clientwan`;
- `dendelion_5g` is an active AP on the same radio; and
- the root/per-interface hostapd and wpa_supplicant ubus objects are present.

Runtime ifnames and the PHY are resolved from `network.wireless status` and
hostapd, not hardcoded. Ubus notification callbacks only copy safe state/event
fields into a one-millisecond queue; all synchronous control calls happen after
the stock supplicant controller has returned. Every timer has a generation
guard, so callbacks retained across reload, interface replacement, or shutdown
cannot mutate a new policy instance.

The published `apsta_scan_gate` status object contains phases, runtime ifnames,
frequencies, and internal error descriptions. It never publishes the wireless
configuration payload, SSID key, BSSID, or scan results.

## Inherent limits

- One PHY still cannot beacon on channel 149 while scanning other channels.
  `dendelion_5g` therefore disappears during each retry window; the configured
  watchdog is the hard upper bound.
- Hostapd `get_status` proves daemon acceptance and expected channel state, not
  over-the-air beacon reception. The deployment canary must test discovery and
  association from another client.
- Stock OpenWrt's AP/STA controller remains active. The queue and verified park
  sequence are designed to coexist with it, but unlike the optional source
  patch the daemon cannot suppress an internal late `CH_SWITCH_STARTED` path.
- If `wifinet4` is intentionally disabled and reports `INACTIVE`, retries stop.
- The service runs as root because arbitrary per-interface supplicant control
  is not available to an unprivileged network account.

## Files

The deployable payload is under `files/`:

- `etc/config/apsta-scan-gate` — site configuration, with `enabled '0'`;
- `etc/init.d/apsta-scan-gate` — late-start/early-stop `procd` service;
- `usr/libexec/apsta-scan-gate.uc` — ubus, topology, lifecycle, and status
  adapter; and
- `usr/share/ucode/apsta_scan_gate/policy.uc` — pure fake-clock-tested policy.

`deploy-service.sh` orchestrates the local/SSH workflow.
`service-action.sh` is the audited router-side helper streamed over stdin or
used inside a unique `/tmp` stage. Neither is installed on the router.

## Offline verification

With a host `ucode` binary matching the target version:

```sh
cd /Users/jacobr/dev/scripts
UCODE=/path/to/ucode \
  ./vanrouter/5ghz-scan-gate/tests/run-service-policy.sh
UCODE=/path/to/ucode \
  ./vanrouter/5ghz-scan-gate/tests/run-service-daemon-integration.sh
UCODE=/path/to/ucode \
  ./vanrouter/tests/test_5ghz_scan_gate.sh
```

The policy runner executes 31 deterministic cases, including timer boundaries,
duplicate events, association grace, late completion, three failed disconnects,
connected-AP repair, AP up/down failures, failed `BSS_FLUSH`/`RECONNECT`, stale
callbacks, manual retry, restart/release behavior, and real payload-bearing
control-event forms. The daemon integration runner uses mocked UCI, ubus, and
uloop modules to prove that the default-disabled path has zero network/event-loop
activity, the API is published before reconciliation, secrets remain redacted,
and stock release makes exactly three bounded reconnect attempts. The top-level
test also checks shell syntax, default-off configuration, procd lifecycle
parameters, path ownership, and daemon compilation.

## Non-deploying and disabled install steps

Run from the Mac or from a copied checkout on vanpi. An alternate SSH target is
the second argument. `--check` is read-only. `--stage-only` writes only a unique
temporary directory, validates hashes and syntax with the router's own ucode,
runs all 31 policy cases there, and attempts to remove the stage. If SSH drops
during cleanup, a credential-free `/tmp/apsta-scan-gate.*` directory may remain
until reboot and can be removed after checking its exact name.

```sh
cd /Users/jacobr/dev/scripts
./vanrouter/5ghz-scan-gate/deploy-service.sh --check root@192.168.6.1
./vanrouter/5ghz-scan-gate/deploy-service.sh --stage-only root@192.168.6.1
```

`--install-disabled` replaces each payload file atomically, backs up any
previous owned files under `/root/apsta-scan-gate-backup-<UTC timestamp>`, and
restores the complete prior owned-file set if any replacement or final
verification fails. It proves that there is no running process or boot link,
does not enable the service, and does not reload either radio:

```sh
./vanrouter/5ghz-scan-gate/deploy-service.sh \
  --install-disabled root@192.168.6.1
./vanrouter/5ghz-scan-gate/deploy-service.sh --status root@192.168.6.1
```

## Deliberate activation and canary

Activation is separate from installation. Connect through `dendelion` on
`radio0` or Ethernet first; the command requires that recovery path to be named
explicitly. It attaches to current radio state and does not issue `wifi reload`:

```sh
cd /Users/jacobr/dev/scripts
APSTA_SCAN_GATE_RECOVERY=radio0 \
  ./vanrouter/5ghz-scan-gate/deploy-service.sh \
  --activate root@192.168.6.1
```

For an Ethernet recovery path, use `APSTA_SCAN_GATE_RECOVERY=ethernet` instead.
Activation waits up to 25 seconds—longer than the scan watchdog—for a stable
`connected` or verified `parked` phase with hostapd actually `ENABLED`. Any
setup, start, or stabilization failure boot-disables the service and attempts
an acknowledged stock handoff before reporting rollback.
Hostapd's status is control-plane evidence only: its cached `ENABLED` state does
not prove that beacon frames are physically on air. Activation therefore means
“ready for the external canary,” not deployment success.
Inspect only the service's safe status/log output:

```sh
ssh -o BatchMode=yes root@192.168.6.1 '
ubus call apsta_scan_gate status
logread | grep -F apsta_scan_gate | tail -50
'
```

The canary must cover both directions. First confirm `lion_fone` association
and real `clientwan` traffic. Turn the hotspot off for longer than one watchdog
and verify that a second client can discover and join `dendelion_5g` while the
station is parked. Leave it off through one scheduled retry, then turn it back
on and verify real internet traffic through `clientwan`; do not accept MWAN's
logical `up` flag as proof.

## Disable, recovery, and removal

Normal disable first removes the boot link, then requires an acknowledged stock
`RECONNECT` before stopping a running daemon. If that acknowledgment fails, it
leaves the daemon running—and therefore preserves a possible fallback AP—and
reports that boot/config state must be inspected and `radio1` reset from the
recovery connection. If only a stale procd registration remains during respawn
backoff, the helper removes that registration but still requires the radio reset
because no live daemon can acknowledge its prior state. It never claims stock
ownership merely because the process disappeared, and it does not reload Wi-Fi:

```sh
cd /Users/jacobr/dev/scripts
./vanrouter/5ghz-scan-gate/deploy-service.sh --disable root@192.168.6.1
./vanrouter/5ghz-scan-gate/deploy-service.sh --status root@192.168.6.1
```

Status reports the UCI flag, any boot link, exact canonical boot links, procd
registration, procd running state, daemon ubus API, and matching process as
separate fields. This keeps a partial rollback or respawn-backoff state visible.

Use this helper—not a direct `/etc/init.d/apsta-scan-gate stop`—for a deliberate
manual disable. The helper observes `stock_resumed=true` before allowing procd
to remove the instance. A direct init stop is suitable during system shutdown,
but if its log reports three failed reconnect attempts, reset `radio1` from the
recovery connection before assuming hotspot autoconnect has resumed.

If a crashed process left `radio1` in a state that cannot be reconciled, remain
connected through `radio0` or Ethernet and reset only the 5 GHz radio:

```sh
ssh -o BatchMode=yes root@192.168.6.1 '
wifi down radio1
wifi up radio1
'
```

Removal backs up the four owned files before changing their enabled state, then
performs the acknowledged disable and deletes only those payload paths. The
standard init disable also removes service rc links; the helper defensively
removes the canonical S99/K10 links. The timestamped backup remains recoverable:

```sh
./vanrouter/5ghz-scan-gate/deploy-service.sh --remove root@192.168.6.1
```

Firmware rollback and wired 3.3 V UART recovery remain documented in
[`../kernel-5ghz-power/README.md`](../kernel-5ghz-power/README.md#rollback-levels).
The Bluetooth TTL module is not treated as a dependable boot console.
