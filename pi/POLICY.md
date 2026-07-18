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

In particular, Starlink torrenting requires both `torrents_enabled=true` and
`allow_starlink_torrents=true`. An unavailable or unrecognized Starlink power
state fails closed unless Starlink torrent permission was explicitly granted.

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

The Bash aliases retain the previous short names:

- `nodisk` / `nodiskx`: disable / enable parked storage
- `notor` / `notorx`: disable / enable all torrenting
- `startor` / `startorx`: allow / block torrenting while Starlink is on
- `mconf`: display the requested policy

`policyctl migrate` creates the policy from legacy `mconf`, `mconf_last`, and
`starconf` markers without overwriting an existing policy. After migration and
deployment, those legacy directories are no longer read.

If the policy file is missing or invalid while parked, reconciliation returns
an error without inferring a mount or unmount request. Ignition-on handling
still stops qBittorrent and spins down disks even when requested policy cannot
be read.
