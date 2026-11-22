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

The `vantv` CLI, the Van TV Firefox extension and the Pi receiver live in
`~/dev/castit/youtube/` (`~/.zshrc`'s `vantv` alias points there).

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

For an offsite Time Machine refresh, run `zsh macbook/scripts/offsite_tm_refresh.zsh`
from the repo root (optional `--label LABEL`). It pauses and restores the hourly
agent while detaching the image and share and starting the Pi copy.

To return to Apple's scheduler, unload
`com.jacobr.vanpi-time-machine`, remove its plist from
`~/Library/LaunchAgents`, and run `sudo tmutil enable`.

## M4 compute worker for vanpi

The outbound-only worker for offline CAN analysis submitted by agents on vanpi,
and its coupled installer, moved to the standalone repository
`/Users/jacobr/dev/van-compute`. See its `pi/docs/compute/VAN_COMPUTE.md`.

## Agent ntfy alerts

`scripts/claude_ntfy_notify.py` is the Claude Code Notification hook in
`~/.claude/settings.json`: it alerts when a session is blocked on a permission
prompt or question. Agents also call its `--send <title> <message> <priority>`
mode when Jacob asks to be notified. It sends through vanpi's `ntfy_send.sh`
over SSH, so notification credentials remain on vanpi.

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
  `llmrestore --save [NAME]` instead saves the sessions open now as a named,
  never-pruned script in `~/.local/state/llm-restore/saved/` (close most of
  them to focus on one); `llmrestore NAME` reopens that set and `llmrestore
  --saved` lists them.

## Deferred one-off jobs

`macbook/scripts/deferred.zsh` runs a script once on or after a due time, then
deletes it. Queue one with
`deferred.zsh add 2026-11-16 tor-unexclude ./undo.zsh "description"` (also
`'YYYY-MM-DD HH:MM'`, `+30d`, `+12h`; `--file` copies extra material in, and
`--attempts`/`--timeout` default to 3 and 900 s). Mac jobs launched by the
hourly checker inherit its LaunchAgent's priority, `ProcessType` `Standard`: at
`Background` an `add-agent` job's test suite ran about six times slower, and its
timing tests failed. A Mac Panel `run-now` runs at the panel's priority, also
`Standard`, and vanpi jobs run on the Pi.
`list` shows the human-readable
queue, while `list --json` supplies Mac Panel's machine-readable checker and job
state. Commands that take `JOB` accept either its exact name or directory id.

Use `cancel JOB` to remove a job, `retry JOB` to reset a failed job for the next
hourly check, and `reschedule JOB DUE` to change its due time (including to a
past time for the next check). `run-now JOB` makes one immediate attempt through
the same environment, timeout, accounting, log and success-purge path as the
checker. It sends no failure alert because its caller receives the status and
can inspect `last.log`; it exits 0 after success, 3 after a completed failed
attempt, 4 when the job reported not ready (below), 75 when the checker lock is
busy, 1 for another error and 2 for usage.

Not ready yet: a job whose `run` exits 75 (EX_TEMPFAIL) is saying its condition
is not met. The checker logs `not ready <id>`, spends no attempt, sends no alert
and leaves the job pending with its due time, so the next hourly check retries.
That cannot go on forever: `add --give-up DUE` (same DUE syntax; default the due
time plus 7 days) sets the time after which a 75 counts as an ordinary failure
(attempt spent, alert as usual). `reschedule` resets the window to the default.
Records written before `give_up` existed lack the key and get the default.
Each not-ready check is recorded in the job as `last_not_ready` (time) and
`not_ready_checks` (count); a failed attempt, `retry` or `reschedule` clears them.
`list --json` reports them with `give_up` (resolved), `agent`, and `ready`, the
`--ready` command as typed (null when none), which Mac Panel shows under each
job's "Ready condition" disclosure.

Agent follow-ups: `deferred.zsh add-agent [--host mac|vanpi] [--ready CMD]
[--model M] [--effort E] [--topic-var VAR] [--cwd DIR] [--run-timeout MINUTES]
[--attempts N] [--timeout S] [--give-up DUE] DUE NAME PROMPT_FILE|vanpi:/PATH
[DESCRIPTION]` queues a job that, once due, runs `--ready CMD` with `/bin/zsh -c`
on this Mac (nonzero means not ready, exit 75), then runs the prompt. On success it
sends one ntfy titled NAME to the topic in `--topic-var` (default
`NTFY_MACBOOK_URL`, from the same secrets file as `NTFY_WARNING_URL`) through
`scripts/deferred_agent_notify.zsh`, with the result (about 600 characters).
Example: `deferred.zsh add-agent --ready 'test -e /tmp/build.done' +1d check-build prompt.md`.
Job names (`add` and `add-agent`) are lower-case letters, digits and hyphens, at
most 60 characters; the job id is `YYYY-MM-DD-NAME`, and every verb takes the name
or the full id.

On the Mac (`--host mac`, the default) the job copies in
`scripts/deferred_agent_run.zsh` and the prompt and runs
`claude -p --output-format json --permission-mode bypassPermissions` in `--cwd`
(default: where you ran `add-agent`; model `haiku`, effort `medium`) behind a fixed
preamble (headless, at most 4 short lines starting `OK` or `NEEDS LOOK`, no ntfy
of its own). The ntfy adds `Open it: cd <cwd> && claude --resume <session_id>`.
A nonzero claude exit or `is_error` is an ordinary failure (the usual warning, no
result ntfy); a finished session that could not be reported is not rerun on retry.

On the Raspberry Pi (`--host vanpi`, implied by a `vanpi:/ABSOLUTE/PATH` prompt,
which names a file on the Pi, read at launch and never copied from this Mac) the
job copies in `scripts/deferred_pi_run.py` as `run`, with
`scripts/deferred_pi_state.py` and `scripts/deferred_pi_remote.py` beside it, and
starts the run through `~/.claude/skills/pi-agent/pi_agent.py`, whose own headless
preamble replaces the Mac one. Defaults: model `sol` (`--model` is a pi-agent tier:
`astra`, `sol` or `luna`), effort `medium`, `--cwd` `~` (the Pi's home, stored as
typed), `--run-timeout` 120 minutes (the longest a run may last before the job stops it)
and one attempt: a vanpi job starts at most one Pi run by itself, so `--attempts`
other than 1 is refused. Another run starts only when a person runs `retry` or
`run-now` (below). `--cwd` must not be under this Mac's home folder
(pi_agent.py would silently rewrite such a path to the Pi's home): write `~/...`
or a real Pi path. `--timeout` (one pass) must be at least 390 seconds, enough to
launch and read the run once, and 420 (the floor plus `READY_ALLOWANCE`, 30 s) with
a `--ready` command, which runs in the same pass before the launch. `--run-timeout` with `--host mac` is refused, as is
a `vanpi:` prompt with `--host mac`; like the other option checks of `add` and
`add-agent` these exit 1, and only a malformed command line exits 2. The job
record says `kind=agent` and `host=vanpi`; nothing a `--file` attachment supplies
makes a job a Pi job.

`--ready` still runs on the Mac, and only a job with no launch on record evaluates
it, inside the time its pass still needs to launch (a `--ready` that does not
finish in time is not ready). Every call to the Pi is capped by the time left in
the pass; a pass that runs out with a launch on record exits 75, never a failure.
Each pass dispatches on state files in the job directory (versioned JSON), shown
by `list --json` as `remote_state` (with `remote_run`, the Pi run id, and
`remote_token`, the launch token, once they exist, and `relaunch_id`, set only
while the attempt may still be active and nothing is left to collect; the list also
reports `host`, `model` and `effort`):

- `unlaunched`: nothing started yet. A pass reads the prompt, requires vanpi to
  answer over ssh (else exit 75, retried at the next check), records a launch
  token and starts the run.
- `launching`: a launch was attempted but its answer was lost (the pass prints
  the commands to look for it). The next pass lists the Pi's newest runs for that
  token (500, widened to 2,000 and then 10,000 while each list comes back full)
  and adopts the one it finds only from a list that holds every run on the Pi;
  it never starts a second run while the outcome is unknown. A Pi that
  cannot be listed is exit 75.
- `launch-unknown`: a complete list holds no run with the token, several runs
  carry it, or the list could not reach back far enough ("search incomplete",
  with a command that searches farther). The job is held and fails; nothing
  contacts the Pi again, and the `pi_agent.py` commands for looking the run up
  are printed. The held token is the job's `relaunch_id` (below).
- `running`: a run id is saved. Each pass polls its status for up to about four
  minutes (less under a short `--timeout`), then exits 75, so the result is
  collected at the first check after the Pi run ends. A run past its
  `--run-timeout` is stopped; only a status that no longer says `running` confirms
  the stop, and that proves the launcher's recorded state, not that every detached
  descendant of the run is gone.
- `finished`: the agent's final message is saved and sent as the ntfy (about 600
  characters plus the run id and the command for the full report). Delivery is
  at-least-once: a send whose answer is lost can arrive twice, and a failed send
  keeps the report and resends it at each check.
- `failed`: the run died, exited nonzero, reported an error, was stopped at its
  `--run-timeout`, finished without a report, or was not collected by its
  collection deadline. The failure names the run and its kind; the job waits for
  a person, and the old id stays in the job's `runs.log` and in `deferred.log`.
  A run that finished cleanly but whose final message is blank is read again at
  the next two checks (exit 75), then fails as "finished without a report".
  `timed-out`, `remote` and `no-report` failures prove the run is over and always
  name the run id whose status established them; a `collection-deadline` failure
  does not prove it, since the run may still be active, and is the only failure
  that may carry just a launch token (for a launch whose run id was never learned).
  Records that disagree about which attempt they describe (a failure naming
  another run than the saved one, or a token-only failure of another kind) make
  the job's state unreadable (below).

Another run, by a person only: a job never starts a second Pi run by itself, in
any state. `retry JOB` (resets a failed job for the next check) and `run-now JOB`
start another run by clearing the previous attempt, and what they need depends
on whether it is known to be over. Under the checker lock they first check every
precondition (the state is readable and consistent, and `--relaunch` names the
unresolved attempt), then give the new attempt a fresh launch window (`give_up`
becomes the later of now and the due time, plus 7 days, as after a reschedule;
the due time and so the job id stay), and only then clear the previous attempt's
records. When a precondition fails nothing is cleared and the message prints a
command that works:

- An attempt confirmed over (`failed` with kind `remote`, `timed-out` or
  `no-report`) is cleared by plain `retry` or `run-now`.
- An attempt that may still be active (`launch-unknown`, or `failed` with kind
  `collection-deadline`) needs `--relaunch ID`: its run id or launch token, which
  `list --json` shows as the job's `relaunch_id` (Mac Panel shows it in the
  confirmation) and which the refusal message prints. The new run can then run
  beside the old one. Without the flag, or with another id, they exit 7 and print
  the command; an id given when nothing is unresolved, or one a later attempt
  already replaced, is refused the same way (exit 7).
- A run the job still owns (`launching`, `running`, or `finished` with an
  undelivered report) is not an attempt to replace: `run-now` without
  `--relaunch` collects it, and `retry` refuses a job that is not failed.
  `run-now JOB --relaunch ID` naming the id or token of a launching or running
  run abandons it and launches another. A finished run whose report is not yet
  delivered is not unresolved, so `--relaunch` is refused for it (exit 7):
  `run-now` without the flag sends the saved report again.
- A job whose state is unreadable or contradictory has no attempt the records
  agree on, so `retry` and `run-now` refuse any `--relaunch` id (exit 7), clear
  nothing and print every id they can recover with the `cancel --abandon JOB`
  command, the only way to remove such a job.

A replacement run the person authorized that cannot start yet stays authorized.
`retry` leaves the job `pending`, and `run-now` saves it `pending` once the
previous attempt is cleared and before its own pass runs, so the checker accounts
the new attempt however it ends, even when the command that started it is
interrupted. A `run-now` whose pass is not ready (its `--ready` condition is false,
or vanpi does not answer) exits 4 and prints `not ready JOB; the next check
retries`, or, for a job not yet due, `the checker retries from its due time ...`.
The checker then launches it once it is due and ready, inside the fresh window. That one launch is all the person authorized:
once it has started, the job again starts nothing more by itself, and a run-now
pass that fails outright (a launch the Pi refuses) leaves the job `failed`.

`reschedule JOB DUE` of a vanpi job that has ever launched (any `remote_state`
but `unlaunched`) is refused with exit 8, so moving a due time can never start a
second run; use `retry` or `run-now`. An unlaunched job reschedules as usual.

Launching and collecting are separate. A job launches by itself only from
`unlaunched`, while it is pending, due and inside its give-up window; an
unlaunched job past `--give-up` (default due + 7 days) fails without launching
and alerts once, and collection of a run it already owns continues past that
time. The give-up time is checked when each job's turn starts (not once per
check, so a job that held the checker cannot let a later one launch late) and
again by the run itself immediately before it records the launch, after any
`--ready` probe; a pass the checker started that reaches the launch past give-up
records nothing, starts nothing and fails the job as past give-up. A pass started
by `run-now` is exempt: the person asked for it now. A job that owns a run (a launch in flight, a run with no terminal state,
or a report not yet delivered) gets a collect-only pass at every check whatever
its state (`pending` or `failed`), due time or give-up time: a `run-now` before
the due time, a `run-now` on a failed job and a reschedule into the future all
leave the run collected. A collect-only pass never launches and spends no attempt.
`run-now` on such a job collects it; when the run is still going it exits 4 and
prints `running JOB: ...`. Collection lasts until the run is terminal or its
`--run-timeout` stop is confirmed, up to its collection deadline (launch time +
`--run-timeout` + 7 days). The scheduler owns that deadline: at each check, before
any pass and whether or not the job's copied `run` exists or is executable, it
records a `collection-deadline` failure from the state file alone once the
deadline has passed, and alerts with the commands to find and stop the run by
hand. A `run` that is missing or not executable only means no collection pass
is made; that is alerted once per run as a collector failure.

The outcome of a pass is read from the state files, never from its exit status:
the job is deleted only when its run is `finished` and the `delivered` marker
exists. A pass that exits 0 with anything else (a run still running or
launching, nothing on record after a launch pass, or unreadable state) is a
collector failure: the job and its records are kept, and the alert goes out once
per run (a job with nothing on record also fails and waits for a person). A failure the run recorded before the scheduler could save
it (a crash between the two) is accounted at the next check instead of
launching again.

The scheduler also owns the alerts, one per kind and attempt: a failed or held
run, a pass that failed before anything started on vanpi (a `vanpi:` prompt that
cannot be read, a launch `pi_agent.py` refused), a job that gave up before
launching, a collector pass that did not settle a
run it owns, whatever its exit status, or a run file that cannot start (it keeps
the run and every check tries again until the collection deadline), and a
finished run whose report could not be delivered. A warning is owed from the
moment the checker raises it (`alerts.owed` in the job directory), whatever
becomes of the job's state, and every later check sends what is still owed;
only a successful send moves it to `alerts.sent`. A crash after a send but before
that move sends the warning again at the next check: a duplicate is possible,
a loss is not. Each failed send adds a
`warning undelivered` line to `deferred.log`; after 48 failed sends (about two
days of hourly checks) the warning is dropped with a `dropped warning` line
there. A damaged `alerts.owed` line is dropped with a `dropped a malformed owed
warning` line and the check carries on. When a job is delivered and deleted while
it still owes warnings for an earlier attempt, they move to `outbox/JOB` under the
queue root and later checks send them the same way (the folder goes once nothing
is left to try); only the warning that this very report was not delivered is
dropped, since the delivery answers it. A warning sent from the outbox ends by saying
the job was deleted, so only its Pi commands still apply. Cancelling a job (including
`--abandon`) drops the warnings it still owes along with it. A failure `run-now` reports at the terminal raises no warning. That last job stays `pending`
with its report saved, resends it at every check and never touches the Pi again:
the report itself has the saved-report guarantee (at least once), while a
failure warning is only retried until one send succeeds.

Exit statuses: every verb exits 75 when the checker lock is busy, 1 for another
error and 2 for usage. `cancel` exits 6 when a Pi run may still be active,
`retry` and `run-now` exit 7 when `--relaunch` is needed or wrong, `reschedule`
exits 8 for a launched vanpi job, and `run-now` exits 0 after success, 3 after a
failed attempt and 4 when the job was not ready or its Pi run is still going.

`list --json`, `cancel`, `retry` and `run-now` read this state with the
repository's `scripts/deferred_pi_control.py`, never by running the job's own
files. A job whose state cannot be read is an invalid row (with a `problem`),
is flagged once by the checker and never run; plain `cancel` refuses it, and
`cancel --abandon` always removes it, printing any run id or token it can
recover. The checker's warning about an invalid job (of any kind) counts as sent
only once ntfy takes it: its marker in `flagged/` holds the failed sends until
then, and each check tries again, up to 48.

`cancel JOB` for a vanpi job stops its Pi run first and deletes the job only once
the run is shown not running. After sending the stop it waits up to about 30 s
for the run to end (polling its status every few seconds), within 180 s overall.
When the Pi cannot be reached or listed, the run still shows running (or cannot be
read) after that wait, or a launch cannot be matched to one run, `cancel` keeps
the job and exits 6, printing the recovery commands; `cancel --abandon JOB`
deletes the job anyway and prints the same commands for a run that may still be
active (and records the id or token in `deferred.log`). `cancel --abandon
--attempt ID JOB` (what Mac Panel's Abandon sends, ID being the launch token or
run id of the attempt whose stop was refused) deletes it only while ID is still
the job's attempt and its state is readable; otherwise it keeps the job and exits 6,
so an attempt that replaced the refused one gets its own stop first. A job record that cannot
be read is checked the same way before it is deleted.

A Pi run can outlive its job: the Pi stays unreachable, or a reachable run does
not stop after SIGTERM, until the collection deadline, or the job is abandoned.
Find and stop it by hand with the run id from `last.log`, `runs.log`,
`deferred.log` or `list --json`:
`/usr/bin/python3 ~/.claude/skills/pi-agent/pi_agent.py status <id>` and
`/usr/bin/python3 ~/.claude/skills/pi-agent/pi_agent.py stop <id>`. For a launch
that never produced an id, search by token
(`pi_agent.py status -n 100000 | grep -e -<token>`).

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
resizing the window. Cmd+= (or Cmd++) and Cmd+- resize every tab and pane of
the focused window rather than only the focused one, and Cmd+0 returns the
window to its display's size; tabs opened later in that window get the chosen
size too, until the window moves to the other display. Those keys are iTerm2
global key bindings (Settings > Keys > Key Bindings) that invoke a function the
follower registers, so while the follower is down they do nothing.
In iTerm 3.7.7 a font change on a Claude Code workgroup tab drops the title
Claude sent and shows the session's explicit name instead ("Chat"), so the
follower puts a session's name back when resizing changed it. That also makes
the restored title the session's explicit name: a size changed outside the
follower (the View menu, a profile edit) can bring back that older title, or
"Chat" on a tab the follower never restored, until Claude sends a new one.
It runs as the LaunchAgent `com.jacobr.iterm-screen-fonts` from its own venv
(`~/.local/share/iterm-screen-fonts/venv`), talks to iTerm2's Python API, and
idles while iTerm2 is closed.

- Install or update: `zsh macbook/scripts/install_iterm_screen_fonts.zsh`. The
  first run asks macOS to let `python3.13` control iTerm2; allow it. It also
  writes the key bindings (`iterm_screen_fonts.py --install-keys`).
- Sizes and timing are saved in
  `~/.config/jacobr/iterm-screen-fonts.json`; their defaults and validation are
  declared in `shared/settings/iterm-screen-fonts.json`. Edit them in the Mac
  Panel's Settings section or, from the repository root, run
  `/usr/bin/python3 shared/python/tool_settings.py set iterm-screen-fonts external=16`.
  Valid changes apply within a second, to every window.
- See what it sees: `~/.local/share/iterm-screen-fonts/venv/bin/python
  macbook/scripts/iterm_screen_fonts.py --list`.
- Log: `tmp/iterm-screen-fonts/agent.log`.
- Remove: give the keys back to iTerm's View menu with
  `~/.local/share/iterm-screen-fonts/venv/bin/python
  macbook/scripts/iterm_screen_fonts.py --remove-keys`, then
  `launchctl bootout gui/$(id -u)/com.jacobr.iterm-screen-fonts` and
  delete `~/Library/LaunchAgents/com.jacobr.iterm-screen-fonts.plist`.

## F5 dictation key as F16

`launchagents/com.jacobr.dictation-key-f16.plist` turns the microphone key on
F5 into F16 with macOS's built-in `hidutil`, reapplied at each login because
hidutil mappings don't survive logout or restart. Dictation still starts from
the Globe key, and fn+F5 still sends F5. The Van TV Firefox extension (castit)
binds F16 to showing and hiding its sidebar. Not F13–F15: Firefox on macOS
reads those as PrintScreen, ScrollLock and Pause, so its shortcuts never fire.

- Install or update, from the repository root:
  `cp macbook/launchagents/com.jacobr.dictation-key-f16.plist ~/Library/LaunchAgents/`,
  `launchctl bootout gui/$(id -u)/com.jacobr.dictation-key-f16`, then
  `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jacobr.dictation-key-f16.plist`.
- Check: `hidutil property --get UserKeyMapping` lists Src `51539607759`
  (0xC000000CF) and Dst `30064771179` (0x70000006B) on the keyboard services.
- Remove: `launchctl bootout gui/$(id -u)/com.jacobr.dictation-key-f16`, delete
  the plist from `~/Library/LaunchAgents/`, and run
  `hidutil property --set '{"UserKeyMapping":[]}'`.

## Dim cab lights when the monitor is plugged in

`scripts/display_cab_dimmer.py` watches for the external Lenovo Q24h-10 monitor
and dims the van's cab Wiz lights to the configured brightness once, only for
cab lights that are already on, through the Pi dashboard API at
`http://vanpi:8788` (`GET /api/lights` and `POST
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
atomic state in `state_store.py`, lazy CoreGraphics access in `displays.py`,
dashboard HTTP in `dashboard.py`, and callback/poll ordering in `runtime.py`.
Read-only Mac Panel/CLI status rendering lives in `status.py`.

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
- Log: `tmp/display-cab-dimmer/agent.log`. Away from the van LAN
  the dashboard is reached over Tailscale; an unreachable dashboard or unavailable light
  is logged and the agent waits. A failed run is shown on its status field but
  does not raise the automation's overall status.
- Stop it: `launchctl bootout gui/$(id -u)/com.jacobr.display-cab-dimmer`.

## Log inventory and rotation

`scripts/log_rotate.py` keeps the Mac's job logs from growing without bound,
now that backups copy them. It lists every log the Mac's jobs write and rotates
any over its limit in place by copy-truncate: the bytes go to `<log>.1.gz`
(older copies shift to `.2.gz` and so on, past `keep` they are deleted), then
the same inode is truncated, so launchd and cron, which hold the file open for
appending, keep writing at offset 0. The archive is written and fsynced before
the log is touched, so a failure leaves the original intact. Growth is checked
before shifting archives; bytes appended during publication are copied through
the still-open archive descriptor (another gzip member) and fsynced before
truncation. Writes landing between that final catch-up and the truncate are lost,
and a writer that does not open the file for appending resumes at its old offset and leaves a zero-filled gap, as
with any copy-truncate; launchd's redirects and shell `>>` append.
Pending recovery can also lose byte-identical regrowth if the writer truncates
and rewrites the same inode with exactly the archived bytes before recovery.
Archives stay in Time Machine; growth is bounded by `keep`, not by exclusions.
It runs as the hourly LaunchAgent `com.jacobr.log-rotate` on `/usr/bin/python3`.

Logs come from, in order (first source wins per path): LaunchAgent plists in
`~/Library/LaunchAgents` (`StandardOutPath`/`StandardErrorPath`), the redirect
targets in `macbook/crontab`, a scan of `~/.local/state`, `~/dev/scripts/tmp`
and `~/Library/Logs` (`.log`, `.out`, `.err`, four directories deep), and extra
paths added by hand. It only touches regular files owned by you inside `$HOME`,
never through a symlink or hard link and never under `~/.claude`, `~/.codex` or
`~/Library/Mobile Documents`; a listed path that breaks those rules shows as
`refused` and is never opened. Log contents are never printed, only paths and
sizes.

Other apps write under `~/Library/Logs` too (for example `Claude/main.log`). A
log the scan finds there starts out `disabled` with the note "third-party app
log; enable per file": it is listed and its bytes count in the totals, but the
rotator leaves it alone until you opt in with a per-file `--enabled on` policy
or add the path as an extra. A LaunchAgent or crontab log in that folder is
yours and rotates normally. `inventory --json` also carries `problems`, the
list of what needs attention (a log past twice its limit, a
total over the warning level, errors in the last run, a stale run), which
`status` and the Mac Panel's Logs section both show as sent. Refused paths
stay visible in the table with their reason, but do not need attention except
for `unrecorded archives; adopt to continue` or `archive record unreadable`:
these require explicit recovery. Any enabled log over twice its limit also
raises a rotation-failing error, even while refused.

- Defaults (rotate above 10 MB, keep 5, gzip, warn above 500 MB total) are
  declared in `shared/settings/log-rotate.json` and edited in the Mac Panel's
  Settings section (default `keep` is at least 1; only a per-file policy can
  choose 0 to discard contents). Per-file policies, extras and ignores are saved in
  `~/.config/jacobr/log-rotate-files.json` (mode 600, revision-checked); state
  (`last_sizes.json` for growth per day, `last_rotated.json`, `last_run.json`,
  `archives.json` for archive provenance, `rotate.log`) is in `~/.local/state/log-rotate/`. The rotator rotates its own
  `rotate.log` last. Archive records match path, inode, size and birth time;
  device numbers are informational because macOS can change them across boots.
  Publication records the destination before linking, and removes the source's
  record after unlinking, so interrupted publication can leave extra records
  rather than unrecorded archives. Missing paths are pruned at each run's start.
  Missing or mismatched records never authorize automatic deletion: an enabled
  log is `refused` with `unrecorded archives; adopt to continue`. A corrupt or
  unreadable `archives.json` refuses every log with archives as `archive record
  unreadable`, and blocks all rotation and adoption without rewriting the record.
  Repair the file by hand, or preserve it with
  `mv ~/.local/state/log-rotate/archives.json ~/.local/state/log-rotate/archives.json.saved`
  (choose an unused backup name), then explicitly adopt each log's archives as
  described below. Removing the corrupt record does not authorize archive deletion.
- Recovery: `adopt --revision REV PATH` explicitly records all existing
  `<PATH>.N[.gz]` archives after checking each is regular, owned by you,
  non-symlinked, not hard-linked and inside the same allowed locations. It
  returns the inventory without changing archive bytes. Use the displayed
  revision from `inventory --json`; a stale revision refuses the operation.
  The Logs row offers the same two-click **Adopt archives** action. Inspect
  the archive paths before adopting; adoption trusts the files currently there.
- Install or update: `zsh /Users/jacobr/dev/scripts/macbook/scripts/install_log_rotate.zsh`.
  It lints the plist, checks the script runs, creates the state directory
  (launchd opens `launchd.log` there before the job starts) and loads the agent.
  Verify with
  `launchctl print gui/$(id -u)/com.jacobr.log-rotate | grep -E 'state =|runs =|last exit'`
  (`last exit code = 0` once it has run) and
  `tail ~/.local/state/log-rotate/rotate.log`.
- From the repo root, `PYTHONPATH=shared/python /usr/bin/python3 -B
  macbook/scripts/log_rotate.py` takes `inventory`, `status`, `rotate [--only
  PATH] [--force]`, and the edits `policy`, `add`, `ignore`, `unignore` and `adopt`
  (each needs `--revision` from `inventory --json`); every command takes
  `--json`. Exit 0 is ok, 1 an error (also a rotate where some log failed, with
  the full result still printed), 2 usage, 3 a stale revision and 75 when
  another run or edit holds the lock.
- Remove: `launchctl bootout gui/$(id -u)/com.jacobr.log-rotate` and delete
  `~/Library/LaunchAgents/com.jacobr.log-rotate.plist`. Archives stay.

## Firefox ads and telemetry off

`scripts/firefox_quiet_prefs.py` writes a managed block into `user.js` in every
Firefox profile that has been run (release and Developer Edition). It turns off
Firefox Suggest (sponsored, Mozilla and online lookups), sponsored new-tab
shortcuts and stories, recommended stories, extension/feature recommendations,
studies, and telemetry uploads. Firefox applies `user.js` at each startup, so
restart it after running; a setting changed in the UI reverts at the next start.

```sh
macbook/scripts/firefox_quiet_prefs.py [--dry-run | --remove]
```

Run it again after creating a new profile.

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
