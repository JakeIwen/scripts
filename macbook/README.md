# MacBook tooling

This directory contains code executed locally on Jacob's MacBook:

- `scripts/`: shell and Python utilities.
- `applescript/`: reusable compiled AppleScripts.
- `bettertouchtool/`: BetterTouchTool helpers, backups, and configuration
  migration tools.

Code that is also deployed to another host belongs in `../shared/` instead.

## Codex ntfy notifications

`scripts/codex_ntfy_notify.py` is a macOS Codex external-notification hook. It
looks up the conversation title in the Mac's local Codex state database, then
sends the completed notification through vanpi's existing `ntfy_send.sh` over
SSH. Notification credentials remain on vanpi.

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
