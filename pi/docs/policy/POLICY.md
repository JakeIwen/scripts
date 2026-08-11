# Vanpi storage and torrent policy

[Pi documentation index](../../README.md)

`/home/pi/.config/vanpi/policy.json` is the single source of requested state:

```json
{
  "allow_starlink_torrents": false,
  "disks_enabled": true,
  "torrents_enabled": true,
  "version": 1
}
```

Only `/home/pi/scripts/policyctl` should modify this file. It validates the
complete document, serializes writers with `flock`, atomically replaces the
file, and requests `vanpi-policy.service` after a change. The dashboard and
interactive shell aliases use this same interface.

## Effective policy

Ignition is observed safety state and always overrides requested state:

| Condition | Disks | qBittorrent |
| --- | --- | --- |
| Ignition on | unmounted and spun down | stopped |
| Disks disabled | unmounted and spun down | stopped |
| Torrents disabled | mounted | stopped |
| Starlink on or unknown, permission disabled | mounted | stopped |
| Starlink on, permission enabled, torrents enabled | mounted | running |
| Starlink off, torrents enabled | mounted | running |

`/home/pi/scripts/disk_policy.sh` is the shared source for automatic, manual,
always-available, and rotational disk labels. Mounting, guarded unmounts,
dashboard disk controls, and the ignition-on path all consume those arrays.
`policyctl` keeps a small Python tuple of rotational labels for status output;
its regression test requires that tuple to match `HDD_LABELS` exactly.

In particular, Starlink torrenting requires `disks_enabled=true`,
`torrents_enabled=true`, and `allow_starlink_torrents=true`. An unavailable or
unrecognized Starlink power state fails closed unless Starlink torrent
permission was explicitly granted.

Disk availability no longer depends on mwan3 state, upstream Wi-Fi attachment,
or internet availability. While parked, disks are mounted unless explicitly
disabled. qBittorrent may remain running without an internet uplink and resume
naturally when connectivity returns.

`vanpi-storage.path` watches the udev-managed filesystem-label and
partition-label directories and requests immediate policy reconciliation when
their contents change. `vanpi-policy.timer` performs a lightweight fallback
reconciliation every 15 minutes for missed events and boot races.

`vanpi-policy-watchdog.timer` checks the lifecycle lock once a minute without
running reconciliation. It alerts when a policy run holds the lock for more
than two minutes. This check is deliberately independent because systemd
coalesces attempts to start an already-running oneshot.

While parked and before applying requested policy, reconciliation checks every
`MOUNT_LABELS` target for a kernel mount whose `/dev` source vanished during a
USB reset or disconnect. It stops backup, torrent, and SMB consumers,
revalidates the exact stale source, and attempts a bounded normal unmount. It
never force-unmounts or lazy-detaches a filesystem. After successful cleanup,
the ordinary exact-label mount logic can attach a re-enumerated device at the
same target. Ignition shutdown deliberately takes priority over this ordinary
recovery so its longer graceful waits cannot delay HDD protection.

Ignition shutdown uses a separate bounded emergency mode. All managed-disk
unmounts create per-filesystem Samba drain markers before disconnecting the
corresponding shares, so Finder or another SMB client cannot reconnect during
the unmount window. qBittorrent receives a graceful deadline and is then killed
by exact process name; a failure to verify that stop is logged but does not
prevent the guarded unmount attempt. Ignition mode shortens the graceful
deadline for backup and qBittorrent processes. If scoped Samba closure fails,
ignition shutdown stops the global `smbd` service and kills any remaining
service processes.

Each filesystem is synchronized and normally unmounted. If a normal unmount is
still busy, the script records its userspace holders, sends TERM and then KILL
through `fuser -mM` for that exact verified mountpoint, synchronizes again, and
retries a normal unmount. Physical spindown remains conditional on verifying
that the filesystem and every partition on its parent disk are unmounted.
No mode uses force (`umount -f`) or lazy (`umount -l`) detach. Parked policy
changes, dashboard ejects, and safe system power operations retain longer
graceful deadlines and do not use ignition's global Samba fallback, but they do
evict exact-mount holders rather than leave a verified managed disk mounted.

The Samba drain markers live under `/run` and therefore clear at reboot.
`mount_disks.sh` explicitly clears the marker for an exact share after accepting
or mounting its labeled filesystem.

The dashboard retains kernel storage errors for seven days, but only errors from
the current boot affect a disk health badge. The compact card shows current
mount/access state; hover the current-error badge for the diagnostic or tap it
on mobile. Historical errors remain available to the monitor without producing
dashboard warnings.
The manual Repair action supports exact-label USB exFAT and ext4 filesystems. It
serializes disk lifecycle work, preserves whether the target was mounted, stops
its consumers, runs bounded automatic repair followed by a read-only
verification, and restores the prior state only after success. A failed check
quarantines the label against remounting. Filesystem repair is not presented as
a repair for failing media, USB transport, cabling, or power.

For a device-offline or USB transport fault, the dashboard also exposes the
existing per-port `uhubctl` power cycle as **Reset USB**. It selects only a
power-controlled port containing exactly that one filesystem label and remains
disabled until storage on the port is unmounted. The intended recovery order is
Unmount, Reset USB, then Repair; resetting USB is never an implicit side effect
of filesystem repair.

`vanpi-disk-health-watchdog.timer` checks always-mounted exFAT flash media once
a minute. A readable but read-only filesystem remains available and requires an
explicit dashboard repair. If two consecutive checks show that `pi` can neither
read nor write an exact mounted filesystem, the watchdog runs the same
label-only repair used by the dashboard: drain and unmount it, run automatic
`fsck.exfat`, verify it clean, remount it with `pi` ownership, and probe it
again. A failed filesystem check quarantines the label against remount/retry;
failures before quarantine receive a 15-minute retry cooldown.

## Commands and aliases

```bash
policyctl status
policyctl --json status
policyctl disks on|off
policyctl torrents on|off
policyctl starlink-torrents on|off
policyctl reconcile
```

`policyctl status` reports both requested policy and authoritative runtime
observation. Its JSON form preserves the requested fields at the top level and
adds a `runtime` object:

```json
{
  "allow_starlink_torrents": false,
  "disks_enabled": false,
  "runtime": {
    "disks_mounted": false,
    "mounted_disk_labels": [],
    "qbittorrent_running": false
  },
  "torrents_enabled": true,
  "version": 1
}
```

Managed disk state comes from exact `/mnt/<HDD_LABELS>` mount entries, and the
process state uses the exact kernel process name `qbittorrent-nox`. A runtime
discovery error makes `status` fail instead of reporting a false stopped or
unmounted state. `policyctl read` remains requested-policy-only because it is
the stable compact interface consumed by the reconciler.

The Bash aliases retain the previous short names:

- `nodisk` / `nodiskx`: disable / enable parked storage
- `notor` / `notorx`: disable / enable all torrenting
- `startor` / `startorx`: allow / block torrenting while Starlink is on
- `mconf`: display requested policy and authoritative runtime state

`policyctl migrate` creates the policy from legacy `mconf`, `mconf_last`, and
`starconf` markers without overwriting an existing policy. After migration and
deployment, those legacy directories are no longer read.

If the policy file is missing or invalid while parked, reconciliation returns
an error without inferring a mount or unmount request. Ignition-on handling
still stops qBittorrent and spins down disks even when requested policy cannot
be read.
