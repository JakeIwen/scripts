# MacBook tooling

This directory contains code executed locally on Jacob's MacBook:

- `scripts/`: shell and Python utilities.
- `applescript/`: reusable compiled AppleScripts.
- `bettertouchtool/`: BetterTouchTool helpers, backups, and configuration
  migration tools.

Code that is also deployed to another host belongs in `../shared/` instead.

## BetterTouchTool path migration

`bettertouchtool/update_repo_paths.js` audits enabled named triggers, other
non-Touch Bar triggers, and Floating Menus. It is a dry run by default:

```sh
./macbook/bettertouchtool/update_repo_paths.js
./macbook/bettertouchtool/update_repo_paths.js --apply
```

Touch Bar trigger and group IDs are deliberately excluded. Run
`bettertouchtool/btt_backup.zsh` before applying a future migration.
