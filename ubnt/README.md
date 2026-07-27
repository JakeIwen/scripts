# NanoStation Wi-Fi selection

The device stores full airOS configurations for saved networks under
`/etc/persistent/profiles`. Those files contain credentials, are ignored by Git,
and are never included in a normal code deployment.

## Manager commands

On the NanoStation:

```sh
/etc/persistent/scripts/wifi_manager.sh status
/etc/persistent/scripts/wifi_manager.sh connect 'profile name'
/etc/persistent/scripts/wifi_manager.sh pause
/etc/persistent/scripts/wifi_manager.sh resume
```

The van dashboard uses four additional fixed manager entry points:

```sh
/etc/persistent/scripts/wifi_manager.sh dashboard-status
/etc/persistent/scripts/wifi_manager.sh dashboard-scan
/etc/persistent/scripts/wifi_manager.sh manual-connect-stdin
/etc/persistent/scripts/wifi_manager.sh provision-stdin
```

The first two emit credential-free, hex-encoded records for
`pi/scripts/ubnt_wifi.py`. The latter two read their selection or provisioning
request from standard input so Wi-Fi passwords never appear in SSH arguments,
process listings, command output, or manager logs. New profiles support
WPA/WPA2 Personal and open networks; WEP is reported to the UI as unsupported.
After association, provisioning deliberately runs the same `save-current` and
`cfgmtd` persistence path as an explicit profile save.

Manual dashboard connections pause automatic selection. The dashboard sheet
shows this state and provides an explicit Resume automatic selection button,
which keeps captive-portal onboarding from being abandoned before login.

`connect` aggregates three site-scan passes because individual airOS scans can
omit visible networks. It applies the strongest matching observation's
frequency to a temporary profile copy, and falls back to an unrestricted scan
if the fast attempt does not associate. The unrestricted fallback waits up to
35 seconds for association before declaring the attempt unsuccessful.
`UBNT_SCAN_PASSES`, `UBNT_SCAN_SETTLE_SECONDS`, and
`UBNT_ASSOCIATE_FALLBACK_SECONDS` can tune this behavior. Automatic operations
do not overwrite saved profiles.

A user-requested switch owns one 120-second protection window beginning before
its scan; fallback attempts do not extend that deadline. If association times
out, the same command immediately recovers to the best visible saved profile
using the scan results it already collected. Raw airOS reload output is captured
in a mode-600 temporary file and deleted, preventing configuration diffs and
credentials from being printed to the terminal or logs.

`save-current PROFILE` is the explicit profile-write operation. If the profile
already exists, its previous version is copied into the profile directory's
`.disabled` folder first. `disable PROFILE` moves a profile there instead of
deleting it.

## Automatic selection

Cron calls `wifi_manager.sh auto` once per minute. An atomic directory lock
prevents overlap. A GUI/manual transition receives a three-minute grace period,
and an established connection must fail three checks before selection changes.

Unlisted profiles have priority 100. `denlink` normally has priority 10 so any
other saved network wins when available. If `config/prefer_denlink` exists,
`denlink` has priority 1000. Overrides use `priority|profile filename` lines in
`persistent/config/wifi-priority`.

Runtime locks, scans, cooldowns, and logs are kept in `/tmp` or `/var/log`, not
under `/etc/persistent`.

airOS regenerates `/etc/dropbear/authorized_keys` during a wireless soft
reload. The boot hook and Wi-Fi manager therefore reinstall the keys listed in
`persistent/config/raspi_rsa_id.pub` after boot and after every reload.

The main runtime log is capped at 256 KiB and retains its most recent 1,000
lines when rotated. Cron error logs use the same limits through an hourly
rotation job. Healthy-link heartbeats are written only when the SSID changes or
once per hour; switches, failures, cooldowns, and transitions are always logged.
These defaults can be tuned with `UBNT_MAX_LOG_BYTES`, `UBNT_LOG_KEEP_LINES`,
and `UBNT_HEALTHY_LOG_INTERVAL`.

## Backup and deployment

Always back up first:

```sh
./backup_profiles.sh ubnt@192.168.8.20 --sync-working
```

Backups are permissions-restricted under the ignored `private-backups/`
directory. Deployment also runs this backup automatically.

Deployment modes are deliberately separate:

```sh
./scp_to_device.sh --stage-only
./scp_to_device.sh --install-paused
./scp_to_device.sh --activate
```

- `--stage-only` uploads to `/tmp`, validates with the device's BusyBox tools,
  then removes the staging directory without changing live files.
- `--install-paused` installs and persists code, stops cron, and leaves automatic
  selection paused for a manual canary test.
- `--activate` installs and starts the new cron configuration.

Every install records a code-only rollback under
`/etc/persistent/rollback/code-TIMESTAMP`. Restore one with:

```sh
./rollback_device.sh code-YYYYMMDDTHHMMSSZ
```

Rollback and deployment never copy, replace, or delete the profiles directory.

The editable push/pull copies above remain the normal operational workflow.
Separately, vanpi pulls the device's complete `/etc/persistent` tree immediately
before its encrypted Borg backup to `bigboi`. That snapshot is for catastrophic
recovery and never syncs changes back into this checkout. See
[`UBNT_BACKUP.md`](../pi/scripts/backup/UBNT_BACKUP.md).

## Tests

```sh
./tests/test_parse_iwlist.sh
./tests/test_wifi_manager.sh
```
