# Time Machine backup

The Mac backs up through the `mbp2tbkup` SMB share. The current native image is
`m4mac0.sparsebundle`; `pi/services/van-dashboard.service` sets
`VAN_DASHBOARD_TIME_MACHINE_BUNDLE` to this path for progress and freshness.

## Capacity and encryption

- Sparsebundle band size: **64 MiB** (`67108864` bytes).
- Backing-store version: **2**, created by Time Machine itself.
- Virtual image size: **16 TB**; this does not reserve 16 TB on the HDD.
- Encryption: enabled during Time Machine setup; the mounted image reports
  encrypted. Preserve its password somewhere recoverable after device loss.
- SMB reported limit: **1,100 GiB** on the 2 TB filesystem, which also holds
  the frozen iCloud staging objects.

`fruit:time machine max size` approximates usage as band count times band size
for bundles in the share root. It does not account for the hidden archive or
other files, and is not a filesystem-enforced quota. Leave physical headroom
for those files, including new or changed bands during the next cloud capture
and its configured free-space reserve. Do not raise the limit or add unrelated
data without reevaluating the combined live-image and staging allowance.

The September image formerly at
`archive/pre-64m-20260920/m4mac.previous-time-machine-image` was deleted with
Jacob's explicit authorization on October 28, 2026, reclaiming approximately
469 GiB after cloud capture failed its free-space check. Its older history is
no longer available. The current `m4mac0.sparsebundle`, frozen staging objects
and verified cloud generations were preserved. Samba's `veto files = /archive/`
continues to hide the staging area from clients of the Time Machine share.

## Establishing 64 MiB bands

On macOS 26.4.1, this sequence produced a native, encrypted v2 image with
64 MiB bands:

1. Preserve existing history and make sure Time Machine is idle before changing
   a destination or its files. Verify the exact labeled filesystem is mounted.
2. Temporarily advertise `fruit:time machine max size = 256G` on this share,
   validate with `testparm`, and reload Samba. Existing clients must reconnect
   to observe the creation limit; do not interrupt an active backup.
3. Let Time Machine create a fresh encrypted network backup through its normal
   settings flow. Only approve erasure when the exact target is a verified
   disposable image, never an existing history.
4. Observe creation, including temporary `.incomplete` directories. Confirm
   the completed image's `Info.plist` reports `band-size=67108864` and
   `bundle-backingstore-version=2`, and that MachineID metadata exists.
   Time Machine may choose a suffixed name when a prior image exists.
5. Restore the normal Samba limit promptly and reload without disconnecting
   the active session. Confirm the Mac sees the restored capacity; an old
   session may retain its creation-time limit until a clean reconnect.
6. Confirm encryption, backup progress, a completed snapshot, and a restore
   check. Point the dashboard at the actual new bundle path.

The observed creation behavior is consistent with advertised capacity / 4096:
256 GiB / 4096 = 64 MiB. This is observed macOS behavior, not a documented
Apple configuration contract; recheck the resulting metadata after upgrades.
Raising the SMB limit does not require rewriting the established band size.

The attempted `hdiutil create` images had the requested size and band geometry
but did not work with this Time Machine setup: repeated attempts reported
"already in use" without creating backup history. They used backing-store v1.
The format difference alone does not prove the precise locking failure, and
v1 should not be described as universally unsupported. Use the tested native
creation procedure rather than the unsuccessful pre-created-image procedure.

## Offsite replication

The separate [Time Machine iCloud replication job](TIME_MACHINE_ICLOUD.md)
captures a cleanly detached image into immutable local objects, then uploads
only changed objects and verifies them. It keeps two completed recovery points
through interrupted updates and uses the same Starlink guard as Pi offsite
backups. Initial installation/running is not evidence of a completed offsite
recovery point: check its completion marker and dashboard verification status.

Verify the filesystem and test restoration after downloading a remote copy.
`hdiutil verify` alone does not validate a writable sparsebundle: its documented
checksum verification applies to read-only/compressed images.
