# Dendelion OpenWrt configuration

This directory contains reviewed, credential-free artifacts for the Linksys
E8450. Live UCI configuration remains authoritative and must be inspected before
deployment.

The source patch and staged recovery procedure for the shared-radio 0 dBm
failure are under [kernel-5ghz-power](kernel-5ghz-power/README.md). Creating or
testing those artifacts does not change the live router.

## Clientwan path monitoring

`clientwan-path-monitor` probes the active `clientwan` gateway and two public
addresses independently every five seconds. It logs state changes immediately
and aggregate success counts once per minute under the `clientwan-path` syslog
tag. The existing remote logger retains those records on vanpi, allowing a
future incident to distinguish a Wi-Fi/hotspot gateway failure from loss beyond
the phone.

The live `clientwan` mwan3 tracker is intentionally less aggressive than its
old two-down/one-up configuration: five failed rounds mark it offline and three
successful recovery rounds bring it back. This avoids withdrawing the only
usable default route and flushing connection tracking for very short cellular
or hotspot interruptions.

Inspect the current router-side log and durable vanpi history with:

```sh
ssh root@192.168.6.1 'logread -e clientwan-path'
ssh pi@vanpi.lan "sudo grep 'clientwan-path' /var/log/openwrt/dendelion.log | tail -n 30"
```

## HTTPS uplink health

`uplink-https-monitor` runs one procd worker per configured van uplink. Each
worker checks Google and Cloudflare HTTPS 204 endpoints, with certificate
verification, no redirects or proxies, four-second connect and eight-second
request limits, then waits 30 seconds. Down interfaces send no probes. Both
successes mean `online`, one means `degraded`, and neither means `offline`.
Missing dependencies or unreadable interface state mean `unknown`.

Each request uses `mwan3 use <interface>` to set both device and socket mark;
`curl --interface` alone is insufficient under mwan3 OUTPUT rules. DNS still
uses the router's shared resolver, so these are per-uplink HTTPS checks, not
isolated per-uplink DNS checks. Two reachable providers cannot guarantee that
every website, IPv6 path, VPN, or client application works.

Atomic `/tmp/uplink-https/<interface>` snapshots feed the dashboard's existing
SSH query; results older than 90 seconds are ignored. The collector preserves
raw `mwan_state`, effective display `state`, and structured `https` results.
Route membership still comes directly from mwan3. No probe changes routes,
firewall rules, link state, or connection tracking. State changes and periodic
summaries use the `uplink-https` syslog tag, retained by the existing vanpi
receiver. Snapshot columns are interface, Unix time, state, Google HTTP status,
Google curl exit code, Cloudflare HTTP status, and Cloudflare curl exit code.

```sh
ssh root@192.168.6.1 'cat /tmp/uplink-https/*; logread -e uplink-https'
ssh pi@vanpi.lan "sudo grep 'uplink-https' /var/log/openwrt/dendelion.log | tail -n 30"
python3 vanrouter/tests/test_uplink_https_monitor.py
python3 -m unittest pi.tests.network.test_connectivity_status
```

During the September 17 website outage investigation, mwan3 still used ping
tracking with one reachable target sufficient for online. Subsequent properly
pinned WAN tests passed Reddit, Google, Cloudflare, and public DNS queries, so
the earlier cause could not be established. The new history addresses that
observability gap without changing failover policy based on an unproven cause.

## Simultaneous 5 GHz AP and client

`deploy-5ghz-ap.sh` stages a temporary helper on the router. The helper adds a
named `dendelion_5g` AP to `radio1` while the existing `wifinet4` client remains
attached to `clientwan`.

The AP copies the SSID, encryption mode, and key from the existing 2.4 GHz
`wifinet3` AP entirely on the router. Credentials are not stored in this
checkout, passed in process arguments, or printed. Only the new UCI section is
changed; the helper itself is removed from `/tmp` after every operation.

This is same-radio AP+STA operation:

- The 5 GHz AP and upstream client must share the hotspot's channel.
- Airtime and the configured channel width are shared.
- The hotspot must be associated when deploying and testing.
- The 5 GHz AP may become unavailable when the hotspot disconnects.
- `radio0` is not reloaded, so the 2.4 GHz management path remains available.

Before applying, connect through the 2.4 GHz LAN or Ethernet, ensure the iPhone
hotspot is available, and verify the read-only preflight:

```sh
./vanrouter/deploy-5ghz-ap.sh --check
```

Apply and run the AP/STA canary:

```sh
./vanrouter/deploy-5ghz-ap.sh --apply
```

The apply operation waits for both the 5 GHz client and new AP to become
operational. If that does not happen, it deletes only `dendelion_5g`, commits
the rollback, and reloads `radio1`.

After the AP is operational, enable Wi-Fi 6 with an 80 MHz channel and run the
same AP/STA recovery canary:

```sh
./vanrouter/deploy-5ghz-ap.sh --optimize
```

Optimization changes only `radio1.htmode` to `HE80`. If the AP and client do
not both recover, or the deployment shell disconnects, the helper restores the
previous mode and reloads only `radio1`.

Inspect or deliberately remove the added section:

```sh
./vanrouter/deploy-5ghz-ap.sh --status
./vanrouter/deploy-5ghz-ap.sh --remove
```

An alternate SSH target can be supplied as the second argument.

## Tests

```sh
./vanrouter/tests/test_openwrt_5ghz_ap.sh
./vanrouter/tests/test_clientwan_path_monitor.sh
./vanrouter/tests/test_kernel_5ghz_power.sh
```
