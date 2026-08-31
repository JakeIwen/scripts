# vOnStar dashboard controls

[Pi documentation index](../../README.md) · [Dashboard architecture](ARCHITECTURE.md)

The vOnStar tile gives the trusted port-8788 dashboard exactly three high-level
vehicle-lock requests: **Lock All**, **Unlock Front**, and **Unlock Cargo**.
Each requires an ordinary browser confirmation, disables the complete control
group while pending, and refreshes authoritative service status afterward.
Existing LAN/Tailscale trust and the dashboard's same-origin mutation check are
the authentication boundary; there is no separate PIN.

The architecture is deliberately one-way:

```text
van-dashboard -> /run/vonstar/api.sock -> vonstar.service -> guarded CAN action
```

`van_dashboard_vonstar.py` only speaks bounded HTTP over that fixed Unix socket.
It validates the service identity/schema and exact three-action catalog,
generates a unique `dash-...` request ID, and sends a JSON object containing
only that request ID to a fixed action path. It rejects symlinks and non-socket
paths and returns only sanitized action/status fields to the browser. Raw CAN
IDs, payloads, counters, CRCs, timing, physical pairs, buses, and netdev names
are neither accepted nor exposed. The socket is never proxied directly to TCP.

The dashboard source integration does not install or start `vonstar.service`.
Deployment remains gated on separate live replay validation of Lock All and
Unlock Cargo, which are currently capture-mapped; Unlock Front has been
independently live-verified. Until the service is installed in execute mode,
the tile remains offline or plan-only and all three action buttons stay
disabled.

Clicking elsewhere on the tile opens the status sheet. Its **Check Status**
button is the only browser path that calls `POST /api/vonstar/access-state`.
That read is not passive: it may perform one guarded wake and power accessory
rails or the dashcam, so it always requires confirmation and is never run by
page load, refresh, timers, visibility handlers, action completion, or
deployment checks. It shares the same single-flight lock as the three action
buttons and never retries automatically.

The returned state is a timestamped snapshot rather than live tracking. Only a
`verified` lock-domain quality may render All locked, Front unlocked/cargo
locked, or Front and cargo unlocked. Driver ajar remains visibly labeled as a
candidate. The modified sliding-door factory circuit is always described as
sensor-bypassed and physically unobservable; its forced-closed electrical input
must never render as a physically closed door. Incomplete individual-door
mapping is labeled partial coverage rather than an overall lock-state failure.
Raw observation summaries are not used to infer UI state and are not displayed
on the tile or ordinary status sheet.
