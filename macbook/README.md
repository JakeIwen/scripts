# MacBook tooling

This directory contains code executed locally on Jacob's MacBook:

- `scripts/`: shell and Python utilities.
- `applescript/`: reusable compiled AppleScripts.
- `bettertouchtool/`: BetterTouchTool helpers, backups, and configuration
  migration tools.

Code that is also deployed to another host belongs in `../shared/` instead.

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

`scripts/van_compute_worker.py` is an outbound-only, allowlisted worker for expensive offline CAN
analysis submitted by agents on vanpi. `scripts/install_van_compute_worker.zsh` deploys the matching
Pi queue CLI and installs the user LaunchAgent. See [`../pi/VAN_COMPUTE.md`](../pi/VAN_COMPUTE.md)
for the architecture, safety boundary, installation, and commands.

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

## BetterTouchTool path migration

`bettertouchtool/update_repo_paths.js` audits enabled named triggers, other
non-Touch Bar triggers, and Floating Menus. It is a dry run by default:

```sh
./macbook/bettertouchtool/update_repo_paths.js
./macbook/bettertouchtool/update_repo_paths.js --apply
```

Touch Bar trigger and group IDs are deliberately excluded. Run
`bettertouchtool/btt_backup.zsh` before applying a future migration.
