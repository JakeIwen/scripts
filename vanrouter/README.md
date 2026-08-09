# Dendelion OpenWrt configuration

This directory contains reviewed, credential-free artifacts for the Linksys
E8450. Live UCI configuration remains authoritative and must be inspected before
deployment.

The source patch and staged recovery procedure for the shared-radio 0 dBm
failure are under [kernel-5ghz-power](kernel-5ghz-power/README.md). Creating or
testing those artifacts does not change the live router.

The default-disabled `procd` service for keeping `dendelion_5g` available
between bounded absent-hotspot scans is under
[5ghz-scan-gate](5ghz-scan-gate/README.md). It runs beside stock OpenWrt and
does not require a new firmware build. It is implemented and tested offline but
has not been installed, enabled, or deployed.

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
./vanrouter/tests/test_kernel_5ghz_power.sh
./vanrouter/tests/test_5ghz_scan_gate.sh
```
