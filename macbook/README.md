# MacBook tooling

This directory contains code executed locally on Jacob's MacBook:

- `scripts/`: shell and Python utilities.
- `applescript/`: reusable compiled AppleScripts.
- `bettertouchtool/`: BetterTouchTool helpers, backups, and configuration
  migration tools.

Code that is also deployed to another host belongs in `../shared/` instead.

## Mac media suite

Performance Audio, Convert Video, Custom Conversion, BPM Over Time and Rhythm
Practice are maintained in `~/dev/mac-media-tools`. See the
[media-suite README](../../mac-media-tools/README.md) for setup, workflows and tests.

The shared Finder app, BPM environment, private jobs, presets and reports live in
that checkout's `build/`. BetterTouchTool installers and maintenance stay in this
repository under [`bettertouchtool/`](bettertouchtool/README.md). Their shared
`MEDIA_TOOLS` resolver accepts `MAC_MEDIA_TOOLS` for another checkout location;
generated BTT definitions and configuration backups remain in `macbook/build/`.

## Strip image metadata

`scripts/strip-image-metadata.zsh` opens a native persistent image drop window
and file picker, creating sibling `_stripped` copies without identifying source
metadata. A BTT installer adds **Tools > Strip Metadata** to the main Media menu.
See [setup, supported formats, and re-encoding behavior](docs/STRIP_IMAGE_METADATA.md).

## Guarded vanpi Time Machine scheduling

`scripts/start_vanpi_time_machine_backup.zsh` replaces macOS's unguarded
automatic Time Machine attempts with an hourly LaunchAgent. It starts a backup
only when:

- SMB port 445 is reachable at `VANPI.lan`; and
- vanpi's exact-label Samba mount gate confirms that `mbp2tbkup` is mounted.

`tmutil startbackup --auto` selects the configured Time Machine destination and
is itself a no-op if a backup is already running.

Install it from a Terminal that has Full Disk Access:

```sh
./macbook/scripts/install_vanpi_time_machine.zsh
```

The installer disables the built-in automatic schedule before loading the
LaunchAgent, preventing the original hourly failure notifications and avoiding
two competing schedulers. Normal unavailable states are silent. To inspect the
current decision manually:

```sh
~/Library/Application\ Support/vanpi-time-machine/start_vanpi_time_machine_backup.zsh --check
```

To return to Apple's scheduler, unload
`com.jacobr.vanpi-time-machine`, remove its plist from
`~/Library/LaunchAgents`, and run `sudo tmutil enable`.

## M4 compute worker for vanpi

The outbound-only worker for offline CAN analysis submitted by agents on vanpi,
and its coupled installer, moved to the standalone repository
`/Users/jacobr/dev/van-compute`. See its `pi/docs/compute/VAN_COMPUTE.md`.

## Codex ntfy notifications

`scripts/codex_ntfy_notify.py` is a macOS Codex external-notification hook. It
uses the latest explicit `/rename` value from the Mac's Codex session index as
the ntfy title, falling back to the current folder name when the conversation
has not been renamed. The newest assistant response leads the notification body,
followed by the working directory for context. The hook sends through vanpi's
existing `ntfy_send.sh` over SSH, so notification credentials remain on vanpi.

Enable it in the user-level `~/.codex/config.toml` (Codex ignores `notify` in
project-level configuration):

```toml
notify = ["/Users/jacobr/dev/scripts/macbook/scripts/codex_ntfy_notify.py"]
```

## Codex release cleanup

`../shared/sh/cleanup_codex_releases.py` (also reachable at the old
`scripts/cleanup_codex_releases.py` symlink, which the installed cron entry
uses) previews removal from the invoking account's
`~/.codex/packages/standalone/releases/`; `--apply` deletes. The Pi runs the same
script from `rootfs_tidy.sh`. It keeps the three highest stable version numbers
plus current/running releases, checks package manifests and paths, coordinates
with the installer lock, and refuses mounts or uncertain process inspection.
Unknown names, prereleases, other platforms' targets and symlinks are left alone;
an unfamiliar manifest or current layout stops cleanup. On macOS, `lsof -w`
suppresses the per-run warning an attached SMB share produces.

The installed user cron job runs Sundays at 9:00 PM local time (`0 21 * * 0`)
and calls the cleanup script directly with `--apply`. Inspect it with
`crontab -l`; use `crontab -e` to change or remove its managed block. The job
references this checkout, so update its path if the checkout moves.
`~/.codex/codex-release-cleanup.log` holds only the most recent scheduled run;
inspect it for `STOP`/`SKIP` if space is not being reclaimed. The pre-install
crontab backup is at `~/.codex/codex-release-crontab.previous`.
Cron skips runs while the Mac is asleep/off and does not catch up on wake.
Use the account normally, without sudo; HOME/CODEX_HOME overrides are ignored.

## Deferred one-off jobs

`macbook/scripts/deferred.zsh` runs a script once on or after a due time, then
deletes it. Queue one with
`deferred.zsh add 2026-11-16 tor-unexclude ./undo.zsh "description"` (also
`'YYYY-MM-DD HH:MM'`, `+30d`, `+12h`; `--file` copies extra material in, and
`--attempts`/`--timeout` default to 3 and 900 s). See queued jobs with `list`,
drop one with `cancel NAME`, and check the checker with `status`.

Each job is a private directory under `~/.agent/deferred/jobs/`. The LaunchAgent
`com.jacobr.deferred-jobs` (installed by `deferred.zsh install`) runs due jobs
hourly and at login, with the job directory as the working directory and a
minimal environment. Success deletes the directory; failure keeps it, records
`last.log`, and sends a high-priority warning to `NTFY_WARNING_URL` without any
job output. After the last attempt the job stays as `failed` until cancelled.
Jobs must be safe to rerun, clean up anything outside their own directory, and
detach any long-lived process they start, since launchd ends the checker's
process group when it exits.

## BetterTouchTool maintenance

Use the [BTT tooling index](bettertouchtool/README.md) for maintained commands,
runtime providers and feature installers.

```sh
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/btt.py inspect
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/btt.py --help
```

Style/setup/repair commands inspect by default and require `--apply` for live
changes. Historical incident notes remain under `bettertouchtool/one_off_fixes/`;
the completed migration and superseded repair executables are in Git history.
Keep personal exports and snapshots in ignored output directories, not Git.
