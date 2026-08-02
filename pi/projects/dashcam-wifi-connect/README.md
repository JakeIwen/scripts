# Dashcam Wi-Fi connection research

## Status: unimplemented

This is a research project and manual prototype, not deployed production
tooling. The Raspberry Pi can connect to and control the PRUVEEO D90-4CH once
the camera's access point is running, but the camera does not restore Wi-Fi
after a power cycle. The remaining manual camera interaction prevents an
unattended implementation.

The prototype files are:

- `dashcamctl`: connect, inspect status, control recording, and print the RTSP
  URL after the camera access point has been enabled manually.
- `test_dashcamctl.sh`: mocked tests for connection safety and recording
  control.
- `ignitionmon-wlan-coordination.patch`: an unapplied experiment that suspends
  ignition-monitor Wi-Fi scans while the dashcam owns `wlan0`.

Nothing in this directory should be installed as a service or added to an
automatic boot path until camera-side Wi-Fi startup is solved.

## Primary blocker

The camera runs a 5 GHz access point at `192.168.10.1`, but does not start it
after power-up. The documented manual sequence is:

1. Press **REC/Up** once to stop recording.
2. Hold **OK/Wi-Fi** for about three seconds.

The camera retains this value across a reboot:

```text
/data/menu_config.lua: wifi_switch.current = 1
```

Nevertheless, the access point is absent after a power cycle and the boot log
initializes `isWifiStarting = 0`. Wi-Fi command `2271` starts the access point
at runtime, but it cannot be delivered over Wi-Fi before the access point
exists. `/data/net_config.lua` contains the normal soft-AP parameters—SSID,
password, band/channel, frequency, and security—but no autostart or boot-enable
field. Do not store those credentials in this repository.

The HTTP server advertises WebDAV writes for `/data`, but changing
`wifi_switch.current` cannot solve startup because it is already `1` and is
ignored by the boot path. Blindly editing other camera files is not a safe
substitute for a known firmware setting.

Possible future solutions are:

- a vendor firmware build with Wi-Fi AP autostart or fleet mode;
- isolated button emulation for REC and OK/Wi-Fi using optocouplers or
  dry-contact relays; or
- a carefully researched firmware modification through the SD-card update
  mechanism.

Firmware modification is unsupported and carries a meaningful bricking risk.
Raspberry Pi GPIO must not be connected directly to unknown camera button
contacts.

## Confirmed camera interfaces

Once Wi-Fi is enabled, the following were verified:

- camera address: `192.168.10.1`
- recording status: HTTP command `2005` on port `8082`
- start/stop recording: HTTP command `1100`
- Wi-Fi start action: runtime command `2271`
- low-resolution preview: `rtsp://192.168.10.1:8554/ch01`
- recordings, metadata, configuration, and logs: unauthenticated HTTP on port
  `8082`

The preview is H.264/AAC at 640x360 and 25 fps. Recording remains four-channel;
the start command was verified to create Front, Rear, Left, and Right files.
Disconnecting the Pi does not stop camera recording.

The advertised settings API exposes recording audio (`1009`), date stamp
(`1016`), loop recording (`1002`), gravity sensing (`1006`), and preview
selection (`3028`). A still-photo action using command `1101` appears likely
from another V536 client but has not been verified on this camera.

The camera also stores an `autoscreensaver` setting, but no LCD command is
advertised by its HTTP settings API. No brightness/dimming setting was found.

## Potential research value

Although the preview is too low-resolution and the startup behavior is too
manual for a general live-feed feature, the stream may still be useful during
vehicle research. A timestamped preview capture can be aligned with passive CAN
captures or UDS DID sampling to correlate visible vehicle behavior with
candidate signals—for example:

- cluster indicators and warning messages;
- switch, lamp, door, or actuator state visible in the camera view;
- vehicle motion or environmental changes; and
- the timing of visible state transitions relative to DID or CAN-field changes.

RTSP buffering and the camera's clock mean this should be treated as coarse
correlation evidence, not a precision time source. A future implementation
should timestamp frames on receipt with the same Pi monotonic-clock reference
used by the signal capture and record measured stream latency. CAN transmission
or active diagnostic requests remain separate, explicitly authorized actions.

## Manual prototype

If the camera access point has already been enabled, the prototype can be run
from a checkout:

```bash
pi/projects/dashcam-wifi-connect/dashcamctl connect
pi/projects/dashcam-wifi-connect/dashcamctl status
pi/projects/dashcam-wifi-connect/dashcamctl stream-url
pi/projects/dashcam-wifi-connect/dashcamctl disconnect
```

The expected NetworkManager profile is named `dashcam`, does not autoconnect,
never owns the IPv4 default route, and disables IPv6. Its Wi-Fi credential must
remain in NetworkManager rather than this repository.

The profile and `ignitionmon.service` both use `wlan0`. The unapplied patch
documents one possible coordination mechanism, but it is not sufficient for
production: the ignition monitor's state and recovery behavior require a
fresh review before any integration is deployed.

Run the mocked prototype test with:

```bash
bash pi/projects/dashcam-wifi-connect/test_dashcamctl.sh
```
