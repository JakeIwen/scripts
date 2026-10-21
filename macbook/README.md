# MacBook tooling

**Mac Panel:** <http://127.0.0.1:8769/> (`macpanel` in a shell, or the BTT
menu-bar item) shows and configures the background tools below: tool settings,
deferred jobs, LaunchAgents, the user crontab, the llm-restore snapshot, BTT
Guard and ntfy alerts. Code, install and service commands:
[`mac_panel/README.md`](mac_panel/README.md).

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

## Cast YouTube to Van TV

`vantv <youtube-url>` casts to the Pi's ad-free receiver over the LAN; alone it
casts the front Chrome/Safari YouTube tab, then a clipboard link.
`vantv pause|resume|stop|status` controls playback. In Firefox, the extension
in [`firefox/van-tv/`](firefox/van-tv/README.md) adds a Van TV button to the
player, a context-menu entry on video links, a toolbar popup and Alt+Shift+V;
build it with `zsh macbook/firefox/van-tv/build.zsh` and install the XPI as its
README describes. Receiver details:
[`../pi/projects/youtube-cast/README.md`](../pi/projects/youtube-cast/README.md).

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
script from `rootfs_tidy.sh` with `--package all` to include its separate
`app-server-daemon` tree. The Mac's existing job remains standalone-only;
`--package app-server-daemon` explicitly selects just the daemon. An absent
package is skipped in an `all` sweep; an incomplete or unsafe installed tree
fails. A busy or invalid tree does not prevent checking the other tree.
Each tree keeps the three highest stable version numbers
plus current/running releases, checks package manifests and paths, coordinates
with the installer lock, and refuses mounts or uncertain process inspection.
Unknown names, prereleases, other platforms' targets and symlinks are left alone;
an unfamiliar manifest or current layout stops cleanup. On macOS, `lsof -w`
suppresses the per-run warning an attached SMB share produces.

The installed user cron job runs Sundays at 9:00 PM local time (`0 21 * * 0`)
and calls the cleanup script directly with `--apply`. Inspect it with
`crontab -l`; change or remove its managed block in `macbook/crontab` (see
User crontab below). The job references this checkout, so update its path if
the checkout moves.
`~/.codex/codex-release-cleanup.log` holds only the most recent scheduled run;
inspect it for `STOP`/`SKIP` if space is not being reclaimed. The pre-install
crontab backup is at `~/.codex/codex-release-crontab.previous`.
Cron skips runs while the Mac is asleep/off and does not catch up on wake.
Use the account normally, without sudo; HOME/CODEX_HOME overrides are ignored.

## User crontab

`macbook/crontab` is the whole MacBook user crontab. Manage it from the repository
root with the stdlib-only sync CLI:

```sh
/usr/bin/python3 -B macbook/scripts/crontab_sync.py status
/usr/bin/python3 -B macbook/scripts/crontab_sync.py pause JOB --revision REVISION
/usr/bin/python3 -B macbook/scripts/crontab_sync.py resume JOB --revision REVISION
/usr/bin/python3 -B macbook/scripts/crontab_sync.py install --revision REVISION
```

`status` prints each stable job ID and the current SHA-256 revision; `status
--json` supplies the Mac Panel. Pause prefixes the exact tracked job line with
`#off `, Resume removes that prefix, and both then install the whole tracked file.
The marker stays in Git so a later install preserves the pause. Each edit refuses
a stale revision. Install, Pause and Resume also refuse when the live crontab has
any nonblank line missing from the tracked file; the missing lines are printed
instead of being discarded. The previous live crontab is backed up privately in
`~/.local/state/crontab-sync/`, with the newest 20 retained.

The llm-restore block has its own durable switch and cannot be paused through this
CLI; use `llmrestore --cron off` or `llmrestore --cron on`. The
codex-release-cleanup block is owned by `macbook/crontab` and can be paused by job
ID. Successful edits remain uncommitted, and both the CLI status and Mac Panel
report that state until another session commits the file.

Exit status is 0 for success, 2 for usage, 75 for a revision conflict or busy
lock, 3 for an unsafe install or a managed, unknown or ambiguous job refusal, and
1 for another error. `macbook/tests/test_crontab.py` checks active and `#off `
jobs so a paused job cannot silently retain a moved repo script. Jobs append
stdout and stderr to the ignored `log/` directory.

- `time_machine_exclude_node_modules.sh` (hourly) sticky-excludes new
  `node_modules` under `~/ais` and `~/dev` from Time Machine. Rerun it by hand
  after a fresh `npm install` if the next backup must skip it.
- `btt_backup.zsh` (hourly, daily 15:00, weekly Sunday 15:00) rotates whole
  BetterTouchTool folder copies through `~/backups/BetterTouchTool/<tier>/btt1`–`btt9`.
  Copies are APFS clones, and the folder is excluded from Time Machine. They
  are taken while BTT runs; `btt.py guard` checkpoints are the consistent backup.
- `deferred.zsh watchdog` (daily 12:17) warns when deferred jobs are waiting
  and the checker below has not run for three hours.
- `shared/sh/llm_restore.py --snapshot --scheduled` (every minute) records the
  open Claude Code and Codex sessions for `llmrestore`, logging errors to
  `~/.local/state/llm-restore/cron.log`. `llmrestore --cron off` pauses it with
  a flag file, so reinstalling this crontab never turns it back on or off.

## Deferred one-off jobs

`macbook/scripts/deferred.zsh` runs a script once on or after a due time, then
deletes it. Queue one with
`deferred.zsh add 2026-11-16 tor-unexclude ./undo.zsh "description"` (also
`'YYYY-MM-DD HH:MM'`, `+30d`, `+12h`; `--file` copies extra material in, and
`--attempts`/`--timeout` default to 3 and 900 s). `list` shows the human-readable
queue, while `list --json` supplies Mac Panel's machine-readable checker and job
state. Commands that take `JOB` accept either its exact name or directory id.

Use `cancel JOB` to remove a job, `retry JOB` to reset a failed job for the next
hourly check, and `reschedule JOB DUE` to change its due time (including to a
past time for the next check). `run-now JOB` makes one immediate attempt through
the same environment, timeout, accounting, log and success-purge path as the
checker. It sends no failure alert because its caller receives the status and
can inspect `last.log`; it exits 0 after success, 3 after a completed failed
attempt, 75 when the checker lock is busy, 1 for another error and 2 for usage.

Each job is a private directory under `~/.agent/deferred/jobs/`. The LaunchAgent
`com.jacobr.deferred-jobs` (installed by `deferred.zsh install`) runs due jobs
hourly and at login, with the job directory as the working directory and a
minimal environment. The checker, `cancel`, `retry`, `run-now` and `reschedule`
share one lock, so mutations wait up to ten seconds rather than racing a job;
a still-busy lock exits 75. Success deletes the directory; failure keeps it,
records `last.log`, and sends a high-priority warning to `NTFY_WARNING_URL`
(through `scripts/ntfy_warning.py`, shared with BTT Guard) without any job
output. After the last scheduled attempt the job stays as `failed` until retried,
rescheduled or cancelled. Jobs must be safe to rerun, clean up anything outside
their own directory, and detach any long-lived process they start, since launchd
ends the checker's process group when it exits.

## iTerm font size per display

`scripts/iterm_screen_fonts.py` gives every session in an iTerm2 window the
font size for the display the window sits on, so dragging a window between the
laptop and the external monitor resizes the text in all its tabs without
resizing the window. Manual Cmd+/Cmd- changes stick until the window moves.
It runs as the LaunchAgent `com.jacobr.iterm-screen-fonts` from its own venv
(`~/.local/share/iterm-screen-fonts/venv`), talks to iTerm2's Python API, and
idles while iTerm2 is closed.

- Install or update: `zsh macbook/scripts/install_iterm_screen_fonts.zsh`. The
  first run asks macOS to let `python3.13` control iTerm2; allow it.
- Sizes and timing are saved in
  `~/.config/jacobr/iterm-screen-fonts.json`; their defaults and validation are
  declared in `shared/settings/iterm-screen-fonts.json`. Edit them in the Mac
  Panel's Settings section or, from the repository root, run
  `/usr/bin/python3 shared/python/tool_settings.py set iterm-screen-fonts external=16`.
  Valid changes apply within a second, to every window.
- See what it sees: `~/.local/share/iterm-screen-fonts/venv/bin/python
  macbook/scripts/iterm_screen_fonts.py --list`.
- Log: `tmp/iterm-screen-fonts/agent.log`.
- Remove: `launchctl bootout gui/$(id -u)/com.jacobr.iterm-screen-fonts` and
  delete `~/Library/LaunchAgents/com.jacobr.iterm-screen-fonts.plist`.

## Dim cab lights when the monitor is plugged in

`scripts/display_cab_dimmer.py` watches for the external Lenovo Q24h-10 monitor
and dims the van's cab Wiz lights to the configured brightness once, only for
cab lights that are already on, through the Pi dashboard API at
`http://vanpi.lan:8788` (`GET /api/lights` and `POST
/api/lights/brightness`). It runs as the LaunchAgent
`com.jacobr.display-cab-dimmer`, uses CoreGraphics display change notifications
with a 20-second fallback poll, and waits for the display list to settle before
comparing it with the last settled set. Sleep and wake do not count as a
plug-in: the baseline is frozen while asleep, but a monitor plugged in during
sleep does fire. Turning the monitor off and on counts as a plug-in when macOS
drops the display. Monitors are recognised by their EDID vendor and model, not
the screen name, so a Q24h-10 that is mirroring another display still counts as
connected and switching mirroring off is not a plug-in. A cable or dock that
drops both monitors for longer than the settle time does count.

The stable script remains the LaunchAgent and automation entry. Its pure display
records and plug-in decisions live in `scripts/cab_dimmer/model.py`, private
atomic state in `state_store.py`, and lazy CoreGraphics access in `displays.py`.

- Install or update: `zsh /Users/jacobr/dev/scripts/macbook/scripts/install_display_cab_dimmer.zsh`.
- Try it manually from the repo root: `PYTHONPATH=shared/python
  ~/.local/share/display-cab-dimmer/venv/bin/python
  macbook/scripts/display_cab_dimmer.py --list`, `--status [--json]`,
  `--dim-now`, or, without a flag, watch for display changes. `--dim-now` runs
  even when automatic dimming is disabled and exits 1 if the dashboard action
  fails; successful and no-op runs exit 0.
- Settings are saved in `~/.config/jacobr/display-cab-dimmer.json`; their
  defaults and validation are declared in
  `shared/settings/display-cab-dimmer.json`. Edit them in the Mac Panel's
  Settings section. The **Enabled** setting controls automatic plug-in actions;
  when off, the agent still records monitor changes and plug-ins but leaves the
  lights alone.
- State is stored privately in
  `~/.local/state/display-cab-dimmer/state.json`. It records the agent,
  matching monitors, the latest plug-in, and the latest automatic or manual
  outcome. The Mac Panel's Automations section shows this status and provides a
  **Dim now** button.
- Log: `tmp/display-cab-dimmer/agent.log`. Away from the van LAN,
  `vanpi.lan` does not resolve; an unreachable dashboard or unavailable light
  is logged and the agent waits. A failed run is shown on its status field but
  does not raise the automation's overall status.
- Stop it: `launchctl bootout gui/$(id -u)/com.jacobr.display-cab-dimmer`.

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
