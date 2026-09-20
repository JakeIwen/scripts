# Weekly Pi recovery copies in iCloud Drive

This job uploads **encrypted copies of the existing Borg repository** directly
from vanpi. Each generation contains its own root/boot backup history, the
Home Assistant SQLite snapshot, and the OpenWrt/UBNT exports already captured
by the local daily job. It excludes external media, EXFAT snapshots, and Mac
Time Machine history. It is not a bootable raw card image.

The repository was approximately 13.7 GB when configured. Eight weekly copies
would occupy roughly 110 GB at that size; both figures grow with the repository.
Full upload plus download verification transfers about twice one generation's
size. This design trades bandwidth for independent, verifiable recovery copies.

## Schedule and data safety

- A systemd timer checks hourly at :17 plus up to five minutes of jitter,
  09:00–23:59 in the Pi's local timezone. Work is due seven days after the last
  verified upload. Deferrals retry in subsequent windows; manual login also
  requests the first run immediately.
- Ignition, HDD policy, and the existing shared backup job lock apply. Each
  attempt has a four-hour deadline. An interrupted copy/upload can retry.
- The latest local Borg success must be no more than 48 hours old.
- The exact labeled backup disk is verified before use. Staging lives in the
  root-only `icloud-weekly/` directory on that disk, not on the boot SD card.
- `borg with-lock` protects the copy. Transient locks are omitted; the copy
  gets a full Borg data-integrity check and a SHA-256 manifest before publication.
  An unfinished local copy may be refreshed; a manifested generation is frozen.
- Destination: `icloud:VanRecovery/vanpi/weekly/<generation>/`. rclone copies
  immutable data, then downloads and compares every file. `_COMPLETE.json` is
  published last and read back before recording success.
- Retention affects only this job's validated, completed generation directories.
  Eight complete generations are retained. Older completed local staging copies
  are removed only after the corresponding remote retention operation succeeds.
  Unverified remote partials and stale abandoned staging generations are retained
  for inspection, not silently deleted. Repeated stale partials may need cleanup.
- Copied repositories retain the original encryption key/identity. Treat them
  as restore sources; do not independently create/prune/recreate Borg archives
  in a copy while the original repository is in use.

## Starlink exclusion

The job inspects the router's selected IPv4 policy and every selected member.
For the antenna path it reads the actual UBNT association. For wireless client
uplinks it reads the router's actual station SSID. `denlink` and any SSID
containing `starlink` are blocked. A mixed policy containing Starlink is also
blocked; a non-Starlink selected hotspot is allowed even if a standby antenna
is associated with Starlink.

Unknown/stale evidence, unknown policy members, additional source routing rules,
unexpected Pi gateways/interfaces, and VPN exit routes defer the job. Rename the
Starlink SSID only after updating `blocked_ssids` in the configuration.

Each rclone operation binds to the verified Pi IPv4 address and ignores proxy
environment overrides. The route is checked before every cloud operation and
every five seconds during it, including retries, verification, and retention.
The client is paused during the inspection, then terminated if evidence fails,
indicates Starlink, or shows any route/association change. Restarting even for
an allowed route change prevents an existing connection from remaining pinned
to the old uplink through connection tracking. This is a polling guard: it cannot guarantee zero bytes
in flight during an abrupt uplink switch. Detection includes the sampling gap
and probe latency. It does not modify router routing/firewalls, and does not
control another device's independent iCloud downloads.

## Authentication

rclone 1.75.1 was installed from the official Linux arm64 release after SHA-256
verification. iCloud Drive support requires the regular Apple account password
and 2FA, not an app-specific password. Tokens typically need renewal around
30 days. Credentials are confined to a root-owned mode-0600 file,
`/root/.config/rclone/vanpi-icloud.conf`; rclone password obscuring is not
encryption. Never put credentials in Git, logs, chat, or command arguments.

Run from a private Terminal session:

```sh
ssh -t pi@vanpi.lan 'sudo /bin/bash /home/pi/scripts/backup/icloud_backup.sh --login'
```

In rclone's interactive menu, create a remote named **icloud**, type
**iclouddrive**, service **drive**. Enter your Apple credentials and approve 2FA
privately. Save, then quit the menu. The helper performs a tiny upload/download
canary, removes that canary, and requests the initial backup. For an existing
authenticated remote, the same helper invokes rclone's reconnect flow directly
and repeats the canary. No weekly transfer runs before the first canary succeeds.

If setup saves credentials but the cloud test fails, do not treat the saved
cookies/token as proof of a usable session. The helper reports recognized Apple
authentication errors without printing cookies or server response bodies.
"Trust token expired" can mean Apple requests fresh 2FA, even after recent setup;
it is not proof that 30 days elapsed. A recorded authentication failure defers
weekly retries until the login helper completes its cloud test successfully.
Retry just the test, without requesting another login, with:

```sh
ssh pi@vanpi.lan 'sudo /bin/bash /home/pi/scripts/backup/icloud_backup.sh --verify-login'
```

Before relying on recovery, keep the Borg passphrase and Apple account recovery
material somewhere accessible after loss of every van device. The Borg
passphrase stored inside an encrypted backup cannot unlock that backup.

## Settings, status, and recovery

Defaults live in `pi/configs/icloud-backup.json`. The active root-only file is
`/etc/vanpi-icloud-backup.json`; deployment preserves existing settings.

```sh
ssh pi@vanpi.lan 'sudo /bin/bash /home/pi/scripts/backup/icloud_backup.sh --preflight'
ssh pi@vanpi.lan 'sudo /bin/bash /home/pi/scripts/backup/icloud_backup.sh --status'
ssh pi@vanpi.lan 'systemctl list-timers vanpi-icloud-backup.timer --all'
ssh pi@vanpi.lan 'sudo journalctl -u vanpi-icloud-backup.service -n 40 --no-pager'
```

Status is `/var/lib/vanpi-icloud-backup/state.json`. Login/transfer problems
notify through the existing backup ntfy mechanism, rate-limited to once daily.
`last_success_at` requires verified remote data, not just a queued upload.

Restore only a generation with a valid `_COMPLETE.json`. Download its entire
directory and follow its `RESTORE.txt`, including manifest checks and Borg data
verification. Use the [recovery playbook](RESTORE.md) for replacement Pi storage
and hardware. A full restore/boot test is still required before declaring the
offsite recovery plan proven.

Deploy scripts, configuration defaults and units with:

```sh
/bin/bash /Users/jacobr/dev/scripts/pi/deploy_icloud_backup.sh
```

The installer requires a verified iCloud-capable rclone at `/usr/local/bin/rclone`
and refuses to update code during an active offsite run. It preserves settings
and credentials and enables the timer, not an immediate unauthenticated upload.

References: [rclone iCloud authentication](https://rclone.org/iclouddrive/),
[Borg locked repository copies](https://borgbackup.readthedocs.io/en/1.2-maint/usage/lock.html),
[rclone download verification](https://rclone.org/commands/rclone_check/).
