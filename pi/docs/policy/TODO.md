# Vanpi lifecycle policy TODO

[Pi documentation index](../../README.md)

## Event-driven policy migration

- [ ] Put lifecycle reconciliation behind a `vanpi-policy.service` oneshot.
  Keep policy decisions in an idempotent script; use systemd for execution,
  serialization, status, and logging. Ignition hooks still invoke the policy
  script directly.

- [ ] Change the existing ignition hooks to update ignition state and dispatch
  `vanpi-policy.service` asynchronously. Ignition detection is already
  event-driven through `ignitionmon.service`; this change should prevent the
  monitor from blocking while disk and network actions complete.

- [x] Trigger policy reconciliation when udev-managed disk-label links change.
  `vanpi-storage.path` delegates all work to `vanpi-policy.service`; no mounting,
  unmounting, or other long-running work occurs in the udev process itself.

- [x] Retire the OpenWrt mwan3 reconciliation trigger after disk and torrent
  policy stopped depending on mwan3 state. The dedicated router credential and
  hook were removed on 2026-07-23.

- [x] Trigger reconciliation when the requested configuration changes. Prefer
  having the configuration-writing command request the service; use a
  systemd `.path` unit only as a fallback for changes made elsewhere.
  - `policyctl` is the sole writer and requests `vanpi-policy.service` after an
    atomic update.

- [x] Separate the user's requested configuration from the ignition override.
  Requested state lives in `~/.config/vanpi/policy.json`; ignition is an
  unconditional observed-state override. The hooks never rewrite requested
  state or create `mconf_last`.

- [x] Keep periodic reconciliation after event triggers are deployed.
  `vanpi-policy.timer` now provides a 15-minute fallback instead of minutely
  cron.

- [x] Keep stall detection independent from the policy service. Starting an
  already-active systemd oneshot does not create a second waiter, so a separate
  minutely watchdog timer detects and reports an abnormally long policy run.
