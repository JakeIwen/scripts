# UBNT backup and restore

[Pi documentation index](../../README.md)

The repository's normal UBNT workflow remains the editable, two-way operational
copy:

```bash
./ubnt/backup_profiles.sh ubnt@192.168.8.20 --sync-working
./ubnt/scp_to_device.sh --stage-only
./ubnt/scp_to_device.sh --install-paused
./ubnt/scp_to_device.sh --activate
```

That workflow is intentionally separate from the disaster-recovery snapshot.
Before each Borg archive, `ubnt_backup.sh` reads the primary NanoStation's
complete `/etc/persistent` tree over SSH and stores the last verified export at:

```text
/home/pi/backups/snapshots/ubnt/ubnt-persistent-latest.tar.gz
```

The export includes saved wireless profiles, scripts, configuration, rollback
generations, and other persistent airOS state. It is replaced atomically only
after gzip and tar validation, path-safety checks, duplicate-member detection,
required-file checks, a nonempty profile check, and a sanity check of
`profiles/system.cfg`. A failed or unreachable-device pull leaves the prior
snapshot intact and lets the normal Borg archive continue with a warning.

The older airOS SSH server offers only its legacy RSA host key. The backup
command enables `ssh-rsa` explicitly for this one pinned-host connection while
still requiring the known host key, batch mode, the configured identity, and
disabled forwarding.

The archive contains Wi-Fi credentials. Its directory is root-owned and mode
`0700`, the file is mode `0600`, and the Borg repository on `bigboi` is
encrypted. Do not copy or extract it into the tracked repository. Keep the Borg
passphrase and exported Borg key somewhere independent of both vanpi and
`bigboi`.

## Schedule and monitoring

No additional cron job is needed. Root's existing `backup_window.sh` runs
`pi_backup.sh`, which
hourly from 03:00 through 08:00, with the first successful run each day winning.
The UBNT export happens immediately before `borg create`, so the existing Borg
daily, weekly, and monthly retention versions it together with the Pi and
OpenWrt snapshots.

`backup_watchdog.sh` reports a missing export or one older than
`UBNT_BACKUP_STALE_HOURS`. An antenna that remains powered off eventually
alerts, while its last valid configuration remains recoverable in Borg.

Manual verification:

```bash
sudo /home/pi/scripts/backup/ubnt_backup.sh
sudo tar -tzf \
  /home/pi/backups/snapshots/ubnt/ubnt-persistent-latest.tar.gz
sudo /home/pi/scripts/backup/backup_watchdog.sh
```

The member list contains profile names but does not expose their credentials.
Do not print or inspect profile contents on a shared terminal.

## Recover from Borg

Mount a selected Borg archive read-only and copy the UBNT export somewhere
private:

```bash
sudo -i
source /home/pi/scripts/backup/backup_conf.sh
mkdir -p /mnt/tmp
borg mount "$BORG_REPO"::vanpi-YYYY-MM-DD_HHMM /mnt/tmp
cp \
  /mnt/tmp/home/pi/backups/snapshots/ubnt/ubnt-persistent-latest.tar.gz \
  /root/
borg umount /mnt/tmp
chmod 600 /root/ubnt-persistent-latest.tar.gz
```

Validate and extract it without overwriting a live device:

```bash
gzip -t /root/ubnt-persistent-latest.tar.gz
mkdir -m 0700 /root/ubnt-restore
tar -xzf /root/ubnt-persistent-latest.tar.gz -C /root/ubnt-restore
```

Treat the extracted `persistent/` directory as sensitive. Compare it with the
replacement device and selectively stage or restore files. Do not overwrite a
different airOS model or firmware version wholesale, and always take a fresh
backup of an intact target before restoring.
