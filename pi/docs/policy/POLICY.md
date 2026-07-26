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

Ignition shutdown uses a separate bounded emergency mode. It creates
per-filesystem Samba drain markers before disconnecting the corresponding
shares, so Finder or another SMB client cannot reconnect during the unmount
window. Backup and qBittorrent processes receive a short graceful deadline,
then only their exact lock holders or process names are killed. If scoped Samba
closure fails, ignition shutdown stops the global `smbd` service and kills any
remaining service processes.

Each filesystem is synchronized and normally unmounted. If a normal unmount is
still busy, the script records its userspace holders, sends TERM and then KILL
through `fuser -mM` for that exact verified mountpoint, synchronizes again, and
retries a normal unmount. Physical spindown remains conditional on verifying
that the filesystem and every partition on its parent disk are unmounted.
Emergency mode never uses force (`umount -f`) or lazy (`umount -l`) detach.
Parked policy changes, dashboard ejects, and safe system power operations retain
their non-emergency behavior.

The Samba drain markers live under `/run` and therefore clear at reboot.
`mount_disks.sh` explicitly clears the marker for an exact share after accepting
or mounting its labeled filesystem.

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
