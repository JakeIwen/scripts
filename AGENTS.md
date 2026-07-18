# Repository guidance

## Private local context

If `AGENTS.private.md` exists, read it before work involving live devices,
networking, storage, backups, or vehicle tooling. It contains private,
potentially stale operational context and is intentionally excluded from Git.
Use it only to guide the task at hand: do not quote it, summarize it into tracked
files, print it in logs, or include its details in commits, issues, or reports.

Never store passwords, tokens, private keys, Wi-Fi credentials, or secret service
URLs in an agent-context file. Use the existing secret-management mechanism.

## Scope and source quality

This repository contains personal scripts and configuration for Raspberry Pi
services, OpenWrt and wireless equipment, media services, backups, desktop
utilities, home automation, and vehicle-related tooling.

Treat repository configuration and historical notes as hints, not proof of live
state. Before changing a live device, inspect its current configuration and
compare it with this checkout. Deployed files may differ from the repository.

## Repository map

- `pi/`: Raspberry Pi backup, disk, service, and setup tooling.
- `vanrouter/`: OpenWrt configuration and scripts.
- `ubnt/`: directional wireless-device profiles and uplink scripts.
- `automation/`: home, media, and desktop automation.
- `sh/`: macOS and general-purpose shell utilities.
- `NativCast/`: Raspberry Pi casting server and browser extension.

Some ignored secret directories, wireless profiles, and device configurations
may contain credentials or private network data. Never print or commit secrets,
and preserve unrelated local or deployed changes.

## Vanpi CAN/UDS workspace

The primary CAN-bus research and diagnostic toolkit is a separate Git checkout
on vanpi at `/home/pi/dev/obd-things/`; it is not mirrored by this repository.
Before CAN/UDS work, inspect that live checkout and its Git status, then read its
root `README.md`, `CLAUDE.md`, and `docs/bus-map.md`. It may contain active,
uncommitted investigations, so preserve its changes independently from this
repository.

Its main contents are:

- `bringup.sh`: PCAN/SocketCAN setup for C-CAN (500 kbit/s) and B-CAN
  (125 kbit/s), passive/listen-only by default; transmit mode is explicit.
- `lib/`: reusable CAN interface/bus detection and wake helpers, ISO-TP/UDS
  request handling, and the ECU/module-address registry.
- `tools/`: generic UDS requests and DID/routine discovery, CAN field finding,
  signal correlation, decoding, and capture helpers.
- `live_data/`: reusable terminal live-data viewer infrastructure.
- `projects/`: battery-voltage, ECU-mapping, radar/ACC, and TPMS investigations,
  with project-specific READMEs, scripts, findings, and some systemd units.
- `tmp/`: ignored, transient captures, sweeps, logs, locks, and other runtime
  output. Durable findings are deliberately promoted into the relevant
  `projects/<name>/findings/` directory.

Some helpers can reconfigure `can0`, wake a bus, issue diagnostic requests, or
perform safety-critical radar actuation. Their presence is not authorization to
run them: follow the live repo's guidance, coordinate with any service owning
the interface, and retain the CAN-transmission safeguards below.

## Live infrastructure safety

- Prefer read-only inspection before changing routing, wireless, firewall,
  storage, mounts, backups, cron, or services.
- Establish and verify a recovery path before a remote Wi-Fi or routing change
  that could disconnect the current client.
- Resolve device names, filesystem labels, mount state, and command paths
  explicitly. Never assume `/dev/sdX` still identifies the same USB device.
- Some identical USB readers do not have unique serials. Backup and clone targets
  must use verified filesystem labels or carefully checked physical paths, not an
  ambiguous `/dev/disk/by-id` entry.
- Check the exit status of discovery commands. An error or empty result is not
  evidence that a disk, filesystem, or mount is absent.
- Non-interactive shells can have a reduced `PATH`; use explicit trusted command
  paths where a safety check depends on a system utility.
- Before deletion or recursion, resolve and validate the exact target and mount
  state immediately beforehand. Fail closed when validation is incomplete.
- Prefer `rmdir` for an expected-empty mount directory. Never recursively delete
  a mount point as cleanup.
- Backup, unmount, and ignition-aware processes interact. Inspect the deployed
  scripts, active jobs, locks, flags, and mounts before forcing an operation.
- A CAN adapter may be connected to a live vehicle network. Do not transmit CAN
  frames unless explicitly requested and the target channel is verified.

## Backup and disk tooling

The active design is represented by:

- `pi/scripts/backup/pi_backup.sh`: backup orchestration.
- `pi/scripts/backup/backup_watchdog.sh`: freshness and health monitoring.
- `pi/scripts/backup/abort_backup.sh`: coordinated termination before unmounting.
- `pi/scripts/clone_to_sd.sh`: bootable spare-card cloning.
- `pi/scripts/mount_disks.sh` and `pi/scripts/umount_disks.sh`: disk lifecycle.

An older mount-directory cleanup implementation once treated a failed filesystem
probe as proof that a mount was absent, then recursively deleted the live mount.
The safeguards added afterward—mount verification, explicit utility paths,
fail-closed behavior, and non-recursive directory removal—are critical. Do not
weaken or bypass them.

Keep bootable spare generations staggered so a bad current configuration does
not immediately propagate to every recovery card. Revalidate the live schedule,
capacity limits, labels, and deployed versions before changing clone behavior.

## macOS video thumbnails

On affected macOS versions, Finder may select frame zero for videos that fade in
from black, producing black thumbnails even though the media and SMB share are
healthy. `sh/stamp-video-thumbs.zsh` uses a `qlmanage` thumbnail as a Finder
custom icon. It requires a GUI session and may write AppleDouble resource-fork
files on SMB storage.

After macOS updates, test whether native frame selection is fixed before stamping
more icons. Remove custom icons and clear the thumbnail cache deliberately if the
workaround is no longer needed.
