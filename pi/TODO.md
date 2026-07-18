# Vanpi lifecycle policy TODO

## Event-driven policy migration

- [ ] Put lifecycle reconciliation behind a `vanpi-policy.service` oneshot.
  Keep policy decisions in an idempotent script; use systemd for execution,
  serialization, status, and logging.
  - The service now exists and is used by the OpenWrt mwan3 trigger. Existing
    ignition and cron callers still invoke the policy script directly.

- [ ] Change the existing ignition hooks to update ignition state and dispatch
  `vanpi-policy.service` asynchronously. Ignition detection is already
  event-driven through `ignitionmon.service`; this change should prevent the
  monitor from blocking while disk and network actions complete.

- [ ] Trigger policy reconciliation on disk attachment through udev and
  systemd. Do not perform mounting, unmounting, or other long-running work in
  the udev process itself.

- [x] Trigger policy reconciliation from OpenWrt when mwan3 uplink state
  changes. Pi-side NetworkManager cannot observe those transitions because the
  Pi-to-router LAN connection remains up. The router notification should mean
  only "reconcile now"; the Pi should then query mwan3 once for authoritative
  current state.
  - Use a dedicated, forced-command credential that can only request the
    policy service; do not give the router general SSH access to the Pi or
    other devices.
  - Make the router hook non-blocking, bounded by a short timeout, and tolerant
    of duplicate events.

- [ ] Trigger reconciliation when the requested configuration changes. Prefer
  having the configuration-writing command request the service; use a
  systemd `.path` unit only as a fallback for changes made elsewhere.

- [x] Separate the user's requested configuration from the ignition override.
  Derive effective policy from both inputs instead of replacing `mconf` with
  `nodisk` and later restoring `mconf_last`.
  - `mconf` remains the requested state. The observed `ignition_is_on` flag is
    an unconditional disk-safety override, and both ignition transitions run
    reconciliation without modifying the requested state.

- [ ] Keep periodic reconciliation after event triggers are deployed. Replace
  the minutely cron invocation only after event coverage is verified, then use
  a slower systemd timer to recover from missed notifications and reboot
  races.

- [ ] Keep stall detection independent from the policy service. Starting an
  already-active systemd oneshot does not create a second waiter, so a separate
  watchdog must detect and report an abnormally long policy run.
