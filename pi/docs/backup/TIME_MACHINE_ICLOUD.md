# Time Machine replication to iCloud

This is separate from [Pi/Borg iCloud recovery](ICLOUD.md). It preserves the
native **encrypted** `m4mac0.sparsebundle`, including its existing Time Machine
snapshots, as a reconstructable offsite store at
`icloud:VanRecovery/m4mac/time-machine`. The initial image was approximately
388 GB (362 GiB). Upload and first download verification together transfer
roughly twice that amount; a completed transfer is not yet a Mac restore test.

## Capture without copying a live disk image

1. The Pi requires a fresh heartbeat from the functioning Mac helper before
   requesting any capture gate; a missing/asleep helper leaves ordinary backups
   enabled. It acquires the existing backup/ignition lock and verifies the exact
   `mbp2tbkup` filesystem. The existing Samba preexec drain gate blocks NEW
   Time Machine connections; other shares are unaffected.
2. A small Mac root LaunchDaemon checks every two minutes. Only on an explicit,
   current Pi capture request, it checks Time Machine is idle, identifies the
   matching encrypted image through `hdiutil info`, and detaches it WITHOUT
   force. Busy/active/unknown images are left alone. It acknowledges the exact
   request nonce only after confirming the image is no longer attached.
3. After clean-detach acknowledgement, the Pi allows up to 90 seconds for
   deferred SMB file CLOSEs (`smb_handle_drain_seconds`, configurable). It still
   requires **zero image handles** and fails closed on probe/gate errors; it
   never forces live image handles closed. The dashboard shows this drain phase
   separately from waiting for the Mac. It then closes only the now-idle
   `mbp2tbkup` tree connections. It continuously checks the drain gate, mount,
   Samba handles, ignition and local-backup priority during capture.
4. Files are copied and hashed into root-private immutable objects under
   `/mnt/mbp2tbkup/archive/icloud-time-machine-v1`. They are NEVER hard-linked
   from the live image: Time Machine overwrites bands in place. Previously
   captured files are reused only when source and object inode/size/nanosecond
   mtime/ctime evidence still matches. An end-to-end source inventory check
   rejects a source that changed during capture. The transient `lock` file is
   excluded; `mapped`, token, metadata and every band are included.
5. The gate is released immediately after capture, or on any error/shutdown.
   Normal Mac backups can resume while upload/verification works exclusively
   from the frozen objects. The initial local copy can take several hours;
   later captures copy only changed bands. Capture waiting is bounded to 30
   minutes; capture itself to six hours. No total-duration limit applies to
   healthy network transfers. ExecStopPost releases this job's orphaned gate,
   and never removes another disk-shutdown job's marker.

The sparsebundle remains intact, its encryption password is not read or stored,
and the previously archived original Time Machine history is untouched. No
filesystem conversion, block snapshot, forced disconnect of an attached image,
or global Samba restart is used. Samba documents its share-specific
[preexec gate](https://www.samba.org/samba/docs/current/man-html/smb.conf.5.html#PREEXECCLOSE)
and [close-share operation](https://www.samba.org/samba/docs/current/man-html/smbcontrol.1.html).

## Incremental cloud layout and retention

`objects/<sha256>` contains immutable encrypted bands and image metadata.
`generations/<tm-timestamp-id>/manifest.json` maps image filenames to objects.
Unchanged objects are shared by generations, so weekly replication does not
re-upload the entire image. The store is not directly mountable; use the
included offline assembler to recreate a native `.sparsebundle`.

Every new object is streamed back and SHA-256 checked. Completed checks are
checkpointed and reused only while remote ID/size/mtime still match; a final
inventory detects objects changed during verification. Manifest and completion
marker are uploaded and read back last. Only `_COMPLETE.json` marks a verified
recovery point. Interrupted uploads/checks resume without changing prior points.

Retain **two verified recovery points** by default. Remove old manifests and
unreferenced objects only AFTER a new point passes verification. A private
retirement journal makes metadata deletion resumable. Unknown paths/proofs
stop retention. Partial generations are preserved/referenced, not purged.

The existing rclone iCloud backend reports `About=false`: account-wide free
space cannot be queried through it. A configurable **900 GiB dedicated-store
budget** limits new uploads; this is NOT an iCloud quota reservation. Leave
room for Pi copies and other account data. Provider quota errors stop the job
and preserve existing copies; the job never deletes good copies to make room
for an unverified replacement. Local staging retains at least **100 GiB** free.

## Schedule, status and Starlink

`vanpi-time-machine-icloud.timer` checks hourly at :37 plus up to five minutes
of jitter. A new capture is due seven days after the last verified copy. A frozen
pending generation is resumed even if its initial upload takes over a week.
The newest local completed Time Machine snapshot must be at most 72 hours old
when captured. The job requires its source disk already mounted; it does not
mount an arbitrary path or stage on the Pi's boot card.

All cloud commands reuse the existing Pi uploader's fail-closed route/SSID,
ignition, HDD policy and no-progress guards. Starlink is excluded, including
mixed policies; network-path changes interrupt the connection. This is the same
polling guard, not a promise of zero in-flight bytes during a sudden route switch.
The 02:55–09:00 local-backup priority window and common lock also apply. A waiting
capture gives up the lock when local backups need the window.

The Backups pane tracks the Mac copy separately from Pi/Borg: local capture,
upload estimate, downloaded verification, last success and attempt history.
Settings are root-private `/etc/vanpi-time-machine-icloud.json`. State/history
are under `/var/lib/vanpi-time-machine-icloud`; Apple credentials and sign-in
renewal reuse the existing Pi job, with no additional Apple login.

```sh
ssh pi@vanpi.lan 'sudo /usr/bin/python3 /home/pi/scripts/backup/time_machine_icloud_status.py'
ssh pi@vanpi.lan 'systemctl list-timers vanpi-time-machine-icloud.timer --all'
ssh pi@vanpi.lan 'sudo systemctl start --no-block vanpi-time-machine-icloud.service'
ssh pi@vanpi.lan 'sudo systemctl stop vanpi-time-machine-icloud.service'
```

## Installation and recovery

Deploy the Pi with `bash pi/deploy_time_machine_icloud.sh`. It checks live shared
code and gate hashes, runs tests, preserves settings, refuses active workers,
and keeps rollback copies. Dashboard module/UI deployment is separate.

One-time Mac installation (run from Jacob's normal Terminal, with sudo):

```sh
sudo /usr/bin/python3 /Users/jacobr/dev/scripts/macbook/scripts/time_machine_offsite_coordinator.py --install
```

The root-owned coordinator is installed under
`/Library/Application Support/vanpi-time-machine-offsite`, with LaunchDaemon
`com.jacobr.time-machine-offsite`. SSH runs as the installing Mac user, using
the existing Pi login; no new keys or passwordless-sudo rules are installed.
The installer verifies this SSH path first. Its log is `coordinator.log` in
that installation directory. Existing Time Machine scheduling/preferences are
unchanged. If the Mac is asleep/offline, the Pi safely defers capture and retries.

The cloud root includes [TIME_MACHINE_ICLOUD_RESTORE.txt](../../scripts/backup/TIME_MACHINE_ICLOUD_RESTORE.txt)
and `time_machine_store.py`. Download the store, select a completed generation,
and assemble into a NEW destination. The assembler validates the completion
proof and all objects and never hard-links bands into the writable restored
image. Then attach read-only on a Mac, enter the image password privately,
verify APFS and restore a test file before relying on Migration Assistant.
Keep that encryption password somewhere recoverable after losing all van devices.
