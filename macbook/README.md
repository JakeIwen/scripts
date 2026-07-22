# MacBook tooling

This directory contains code executed locally on Jacob's MacBook:

- `scripts/`: shell and Python utilities.
- `applescript/`: reusable compiled AppleScripts.
- `bettertouchtool/`: BetterTouchTool helpers, backups, and configuration
  migration tools.

Code that is also deployed to another host belongs in `../shared/` instead.

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
