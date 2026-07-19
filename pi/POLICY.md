# Vanpi storage and torrent policy

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
