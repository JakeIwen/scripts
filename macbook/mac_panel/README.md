# Mac Panel

Open <http://127.0.0.1:8769/> or run `macpanel` in Jacob's interactive shell.
The BTT menu-bar item and shortcut open the same page.

## Install and control

From `/Users/jacobr/dev/scripts`:

```sh
zsh macbook/mac_panel/install.zsh
```

The installer creates a standard-library-only Python environment at
`~/.local/share/mac-panel/venv` from `/opt/homebrew/bin/python3`, preserving a
broken previous environment beside it. Re-running installation safely regenerates
the LaunchAgent, including after an interpreter-path change. No sudo is used.

Use the following commands from any directory. `prepare` only generates and lints
configuration; it does not install a job. `status` is read-only and exits nonzero
unless installed configuration, enabled registration, launchd PID, HTTP health
and the private ownership record all agree.

```sh
~/.local/share/mac-panel/venv/bin/python /Users/jacobr/dev/scripts/macbook/mac_panel/panel_service.py prepare
~/.local/share/mac-panel/venv/bin/python /Users/jacobr/dev/scripts/macbook/mac_panel/panel_service.py install
~/.local/share/mac-panel/venv/bin/python /Users/jacobr/dev/scripts/macbook/mac_panel/panel_service.py status
~/.local/share/mac-panel/venv/bin/python /Users/jacobr/dev/scripts/macbook/mac_panel/panel_service.py start
~/.local/share/mac-panel/venv/bin/python /Users/jacobr/dev/scripts/macbook/mac_panel/panel_service.py stop
~/.local/share/mac-panel/venv/bin/python /Users/jacobr/dev/scripts/macbook/mac_panel/panel_service.py restart
~/.local/share/mac-panel/venv/bin/python /Users/jacobr/dev/scripts/macbook/mac_panel/panel_service.py verify
~/.local/share/mac-panel/venv/bin/python /Users/jacobr/dev/scripts/macbook/mac_panel/panel_service.py uninstall
```

`stop` disables and unloads the agent until `start`. `restart` and `verify` request
an authenticated graceful exit and prove KeepAlive starts a different PID and
instance. `uninstall` removes only registration; data and the environment remain.
Uninstall before moving the checkout. The agent starts at GUI login, not before
login at boot.

- Job: `gui/<uid>/com.jacobr.mac-panel`.
- Installed plist: `~/Library/LaunchAgents/com.jacobr.mac-panel.plist`.
- Prepared plist: `tmp/mac-panel/com.jacobr.mac-panel.plist` in the repository.
- Log: `tmp/mac-panel/panel.log` (private).
- Ownership record: `tmp/mac-panel/ownership.json` (mode 600; includes the instance
  token; do not paste it into reports). It is written only after a successful bind
  and removed by its owning instance on exit.

Python edits take effect only after a restart; the page's header says "Panel code
changed since it started" (from `/healthz` `source_changed`) and offers the same
confirmed restart as the services table. The banner watches only `*.py` directly under
`macbook/mac_panel/`, not `shared/python/`. Static files are read per request.

## Sections and owners

Sections are grouped in the sidebar; the hash opens one. Each row names the owner CLI or
module that holds the section's state: the panel only relays validated requests to it.

| Group, section (hash) | Reads and commands |
| --- | --- |
| Overview (`#overview`) | Reads no source of its own: it draws from the state registry the other sections fill. Needs attention, quick controls (Low Data Mode, network verdict, llm-restore), one card per section, a settings finder. |
| Jobs & services, Services (`#services`) | `/bin/launchctl list` plus user `com.jacobr.*` plists; installed system daemons use `launchctl print system/<label>`. A job `launchctl print-disabled` lists as disabled shows grey "disabled". The inventory carries each log's path, stream and modification time; `GET /api/services/logs/<label>` returns bounded, redacted tails when a disclosure opens. Confirmed `POST /api/services {action: restart / start / stop, label, confirmed: true}` for user agents (restart `launchctl kickstart -k`; stop disables then boots out, so KeepAlive cannot undo it; start enables then bootstraps or kicks). Stop and start are refused for `com.jacobr.mac-panel` itself; the header's restart stays. Daemons: restart only, through `sudo -n /bin/launchctl kickstart -k system/<label>`, offered when the sudoers rule below is present. |
| Jobs & services, Deferred jobs (`#deferred`) | `/bin/zsh macbook/scripts/deferred.zsh list --json`; `cancel`, `retry`, `reschedule`, `run-now`, `add` and `add-agent` stay owned by that CLI. Run-now returns an asynchronous task receipt shown in the row; receipts are bounded and reset when the panel restarts. The add form takes a name, a due time, a description and a script (must start with `#!`), plus optional `attempts` and `timeout`; the adapter writes it to a private temporary file for `deferred.zsh add` and deletes it. The Add agent job form (`add-agent` action) takes a name, due time, description, where it runs (this Mac or vanpi), the prompt as pasted text or, for vanpi, an absolute path on the Pi, and optional model, effort, working folder, Ready condition, attempts, timeout and (vanpi) run timeout; the adapter validates every field first, writes pasted text to a private temporary file for `deferred.zsh add-agent --host ...` and deletes it. Agent jobs show host, model and effort tags and, for vanpi, the Pi run's state (`remote_state`) and id. A cancel the CLI refuses because a Pi run may still be active answers 409 `remote-active` and the row then offers **Abandon job** (`cancel --abandon`). Run now and Retry on a job whose Pi launch is `launch-unknown` are armed with the relaunch warning and send the hold's token (`relaunch`, passed as `--relaunch TOKEN`); the CLI checks it under the checker lock, and a missing or changed token answers 409 `relaunch-unconfirmed` (run-now also checks the current listing before starting its task), after which the section reloads so the confirmation is asked again from fresh state. A Pi run-now whose run is still going (exit 4) shows "still running on vanpi; a later check collects it", not a failure. New job names stop at 49 characters (so the dated id stays within the CLI's 60); a vanpi working folder under this Mac's home, a vanpi timeout under 390 s, a due time skipped by daylight saving and text that is not UTF-8 encodable are 400s before anything is staged. A job whose agent or Pi fields contradict each other is shown as an invalid row with its `problem`; the other rows stay. |
| Jobs & services, Crontab (`#crontab`) | `/usr/bin/python3 -B macbook/scripts/crontab_sync.py status --json` reads the tracked and live crontabs. Pause, resume, install and run-now use the same CLI with the displayed revision. Each job carries its next runs for the schedule chart. Log modification times are hints, not proof a job succeeded. |
| Jobs & services, Automations (`#automations`) | `shared/python/tool_automation.py` loads declarations from `shared/automations/`. Each status command prints one validated `status_record`; declared actions run through the tool's fixed argv and timeout, confirmed when declared. An action declared `async` (the Codex/Claude "Check for updates now") runs through a background task and the card shows its receipt. |
| LLM, llm-restore cards (a section of its own, `llm-restore`, mounted inside `#usage` under the budgets card; anchor `snapshots`; a `#llm-restore` hash or stored tab opens LLM there) | `/usr/bin/python3 -I -B shared/sh/llm_restore.py --cron --json`; on/off adds `on` or `off`. That CLI owns its crontab block. The same CLI lists the saved sets (`--saved --json`, shown with the automatic restore list). Selecting a row runs its dry run (`--dry-run`, plus `-- NAME` for a saved set); **Restore** (armed, `confirmed: true`, 120 s timeout) runs the same command without `--dry-run`; **Take snapshot** runs `--save=NAME` (blank name: timestamp; an existing name is a 409, never overwritten). `POST /api/llm-restore/snapshots` takes `{action: preview\|restore\|save\|delete\|overwrite, target: {kind: "auto"} \| {kind: "saved", name}, name?, confirmed?}`; a saved target must be in the CLI's current list. Selecting a saved set also offers **Delete** and **Overwrite** (both armed, `confirmed: true`, saved targets only). The CLI has no delete, so Delete renames the set's file into `~/.local/state/llm-restore/saved/.deleted/<name>-<YYYYmmdd-HHMMSS>.sh` (an exclusively created 0600 placeholder is replaced by the atomic rename; folder 0700, never a symlink; the CLI lists only `saved/*.sh`, so the set leaves the listing). Overwrite renames the old version into `.deleted/` the same way, then runs `--save=NAME` without `--force`; if the CLI says the name exists (a save landed meanwhile) the answer is 409 and both files stay, on any other failure the old version is linked back, except after a timeout, when the save may still finish, so it stays in `.deleted/`. Overwrite refuses a set whose name ends in `.sh` (the CLI strips that suffix, so it cannot address the set); Delete still works for it. The panel refuses a set whose listed path is not a regular `.sh` file directly in that folder. Nothing is erased: recover a set with `mv ~/.local/state/llm-restore/saved/.deleted/NAME-STAMP.sh ~/.local/state/llm-restore/saved/NAME.sh`. Backups are never pruned. The automatic preview regenerates the restore plan, as the CLI itself does. |
| Network, Metered (`#metered`) | `macbook/metered/mac-metered status --json` and its mutations: SSID add (offline) and remove (needs the Pi and its revision), sync, approve, revoke, mode, exempt and Low Data Mode. Pi consumers are approved on the Pi; the panel shows what the Pi reported. A history card plots the verdict, Low Data Mode and each consumer from the history store. |
| Mac, Resources (`#resources`) | `GET /api/resources` and `POST /api/resources` (backend: `resources_adapter.py`). The page draws one snapshot: a System card (health from the backend's `health.level`, mapped to `Level` and never recomputed; gauges of what is used for CPU, memory, swap and disk, coloured by one threshold table, with memory coloured by the backend's pressure value, grey with "pressure unavailable" when it could not be read; load, pressure, thermal, power and uptime; a probe that could not be read shows as unavailable, never zero), an LLM sessions card (sessions sorted by CPU by default, or by Memory or Name from the clickable headers, with kind tags, nested sessions indented under their parent, a totals line per kind, a process tree per session (a table on desktop, one compact row per process on a phone), and the LLM infrastructure rows beneath, read-only), an m4-compute card (worker and job rows, read-only, "idle" with no job), an Apps and processes card (By app is read-only; By process offers Terminate, Force kill, Pause or Resume by `stopped`, and Lower priority below nice 10; sort by CPU or Memory, a Paused chip and a text filter over every process the backend lists, every process up to a cap of 2,000, and stopped processes are kept beyond the cap; the page draws the first 50 matches and says how many more match; By app lists the union of the top 25 apps by CPU and the top 25 by memory, up to 50) and a History card (CPU and memory by kind, machine memory, swap and CPU from the sampler's series). Cmd-K search indexes the section's cards and each LLM session (it opens that session expanded and can arm Terminate session); processes are found with the list's own filter. Every armed button is keyed by the row's pid and start token, so a search staged before a poll never arms a replacement process. A session whose name the backend could only guess (`name_source` `rollout-time` or `fallback`) is marked as an unconfirmed name. A row the backend policy blocks shows no buttons, only its reason behind an info popover; the page's copy of the policy only explains disabled controls, the server re-checks everything. Sort, view, filter, the Paused chip, the session sort and expanded sessions survive the 15 s poll. Memory is resident memory: it differs from Activity Monitor and double-counts shared pages when summed; CPU is percent of one core and an estimate. |
| Mac, Amphetamine (`#amphetamine`) | `macbook/scripts/amphetamine_control.py status/configure`. The automatic-lock switch and the idle-minutes field each save through `configure` with the displayed revision. The battery cutoff is read-only (Amphetamine owns it). |
| Mac, BTT guard (`#guard`) | `/usr/bin/python3 -B macbook/bettertouchtool/btt.py guard`: `checkpoint --list`, `audit` (a fresh read-only audit on every load), `service status`, `acceptance` (shown as Auto-accept; drift pending acceptance is yellow), `inspect`. Inspect and audit are unconfirmed and read-only; the known-good checkpoint (`checkpoint --accept-current`, adding `--replace` only after a fresh active-checkpoint check) is armed and sends `confirmed: true`. Repair is Terminal-only. |
| Mac, ntfy alerts (`#ntfy`) | Reads only routing metadata from `~/.claude/settings.json` hooks and `~/.codex/config.toml` notify; destinations are never read or shown. The one action is a confirmed test through `guard notify-test --send`. |
| Mac, Logs (`#logs`) | `/usr/bin/python3 -B macbook/scripts/log_rotate.py inventory --json` (with `PYTHONPATH=shared/python` passed through `cli_runner`; no `-I`, which would hide the script's package) lists every log the Mac's jobs write (LaunchAgent plists, the tracked crontab, scanned state folders, added paths) with size, archives, growth per day, policy and state; the section adds the `com.jacobr.log-rotate` agent's launchd state. Confirmed `POST /api/logs {action: rotate, path?, force?, confirmed: true}` runs `rotate --json [--only PATH] [--force]`; `policy`, `add`, `ignore`, `unignore` and `adopt` pass the displayed revision to the same-named subcommands (exit 3 is a 409 conflict, exit 75 a 409 with code `busy`; each answers with the new revision and the touched entry, and the page then reloads). A rotation that had per-log errors exits 1 but still prints its result, so it is a 200 showing rotated and failed logs together; any other exit 1 is a 400. A row refused with `unrecorded archives; adopt to continue` offers an armed **Adopt archives** button; the confirmed request records only eligible existing archives and never edits their bytes. The needs-attention list is the rotator's `problems` plus the agent's launchd state; the page renders it as sent. A line chart with a MB axis plots live plus archived bytes from the history store. Each cached inventory is sampled only once; after one cadence the adapter refreshes it in its own single-flight daemon thread, without taking a shared action-task slot. |
| LLM (`#usage`, section id and hash unchanged) | `/usr/bin/python3 -I -B /Users/jacobr/config/mac/usage-budget/usage_budget.py facts --json`, a read-only subcommand that reads the cached snapshot only. Gauges for the Claude, Fable and Codex windows and credits, the routing lean, workers, review and availability, and two line charts (allowance left, credits left) from the history store. No account identity is exposed. Beside the gauges sits the usage-budget tool's settings card (fields, Save, Reset, revision conflicts), loaded from `/api/settings` on each refresh through the section's `load()`; polling pauses while it has unsaved edits. The usage payload and the llm-restore cards load independently: either failing shows an error in its own block while the other keeps working, and a restore action refreshes only the restore cards, never a half-edited settings form. |
| Settings (`#settings`) | `shared/python/tool_settings.py` loads declarations from `shared/settings/` (plus `~/.config/jacobr/declarations/`) and revision-checked values from `~/.config/jacobr/`. Each field renders through one row with its description in an info icon, its unit and a "changed from default" marker; save checks the revision and a conflict offers Reload; reset is armed and sends `action: "reset"` with `confirmed: true`. A declaration that cannot be loaded becomes an error card on that tool alone. The usage-budget tool's card is not shown here: a pointer card links to the LLM section, and its fields are searched there (`USAGE_SETTINGS_TOOL` in `section_copy.mjs`). |

A new automation joins the panel by adding its declaration under
`shared/automations/` and a status command that prints a
`tool_automation.status_record`. The automation tool remains the owner of its
durable state and every mutation the declaration exposes.

### Amphetamine

Open `http://127.0.0.1:8769/#amphetamine`. Automatic lock operates only during
an active Amphetamine session. The panel reads native preferences on refresh,
so edits from Amphetamine's own preferences appear here too. Use the switch
for automatic lock (it saves at once with the saved minutes) and the minutes field
(1–240) with Apply. The displayed
session status distinguishes enabled locking from an active keep-awake session.

Applying restarts Amphetamine under a temporary `caffeinate -i` assertion,
which ends with the controller. It preserves an indefinite open-display
session's display and screen-saver choices. Timed, trigger-based, app/date-based
and closed-display sessions must be ended in Amphetamine first; the controller
refuses to replace them. The low-battery cutoff and lid behavior are unchanged.
Saved preferences are revision checked, backed up privately under
`~/.local/state/amphetamine-control/`, and the three edited keys plus the session
are restored on an apply failure. Native app control requires the Mac's login
session; an unavailable Apple Events connection is reported instead of claiming
the lock is active. This adds no service or background watcher.

macOS privacy permissions apply separately to this login agent. The controller
uses the panel's own Python interpreter so there is one Python identity to
authorize. If macOS denies access to Amphetamine's container, its Python process
needs approval to read app data (Full Disk Access is the manual settings route;
a background agent gets no prompt). The controller then exits 4 and the section
names the binary macOS checks: the venv interpreter's resolved Homebrew target,
which changes with each Homebrew Python upgrade, so the grant must be repeated
then. Restart the panel after granting it.
Apple Events also needs Automation permission to control Amphetamine. Granting
Full Disk Access covers other scripts using that interpreter; it is not limited
to this tab. These permissions require user action in System Settings and are
never granted, reset or bypassed by the controller. An unreadable preference file
is an error, never a report that automatic locking or the battery cutoff is off.

CLI paths above are relative to `/Users/jacobr/dev/scripts` for reference, except the
Usage adapter's `/Users/jacobr/config/mac/usage-budget/usage_budget.py`, which lives outside
the repo. The server always invokes fixed absolute argv without a shell. Errors stay local to
their section. ntfy configuration remains owner-managed rather than panel-editable.

### Refresh behaviour

Services, Deferred jobs, Automations and Metered poll every 15 seconds; every section has
its own Refresh button and "updated N s ago" line, and "Refresh all" refreshes the rest.
A poll (and "Refresh all") skips a section while it has unsaved edits, an open editor, an
armed confirmation or a focused field, and says so in that line; nothing a reader typed is
replaced by a render. Every 15 seconds the page also reads `/healthz`: it reports an
unreachable panel in the header, renews the token and refreshes after a panel restart, and
offers a restart when the Python on disk is newer than the running process. Action results
show in the section's notice with a timestamp; successes clear after 20 seconds, errors stay.

Async actions return a task record `{id, job, state, exit_status, message, started_at,
finished_at}` (epoch-second times, nullable exit and finish while running). Each adapter
returns only its own tasks; `GET /api/tasks/<id>` reads one receipt. Completion updates the
notice without replacing unsaved edits; a deferred refresh runs on the next idle poll,
even for a section that does not normally auto-refresh.

Every armed action is two-click, worded `Confirm: <verb phrase>`: the first click arms it,
Escape, blur or 8 seconds disarm it, and the second click sends the request with
`confirmed: true`. The server refuses these requests with HTTP 400 without it: service
start, stop and restart, crontab pause, resume, install and run-now, deferred run-now,
cancel (and Abandon job after a refused cancel), add, add-agent and Retry on a job whose Pi launch
is unknown (a second Pi run may start), metered SSID removal and Low Data Mode, the BTT guard checkpoint, the ntfy
test, settings reset, log rotation, llm-restore restore, delete and overwrite and confirmed automation actions. Resources adds six more, all sent as `{action, pid, started, scope, confirmed: true}`: Terminate (`terminate`), Force kill (`kill`), Pause (`pause`), Resume (`resume`), Lower priority (`lower-priority`, which the panel cannot undo) and Terminate session (`terminate` with `scope: tree` on the session's root process). Their confirmations name the process or session, with snapshot counts ("12 processes seen"); the result line reports the reply's applied, gone, changed, skipped (members protected by the policy), refused, unchanged and not-reached (targets left alone because the action stopped first) counts, leaving out zeros, and, when the reply's `stopped` (`{code, text}` or null) is set, an error notice saying the action stopped early and why. A search result only navigates to the button and arms it. Reschedule opens an inline editor
whose Apply is enabled only once the time differs.

Navigation: the sidebar groups Overview; Jobs & services (Services, Deferred jobs,
Crontab, Automations); Network (Metered); Mac (Resources, Amphetamine, BTT guard, ntfy
alerts, Logs); LLM (id `usage`, which also shows the llm-restore cards under its budgets); Settings. It is a drawer opened by Menu below 900 px. A nav entry shows its
state dot (green fine, yellow needs attention, red failing, grey idle, hollow unknown) and
its one-line summary as the hover tooltip; a group heading shows the worst of its members.
The arrow keys, Home and End move between entries. The shell opens the section named by the URL hash, else the last-viewed section from
`localStorage`, else `#overview`, so in-page links such as crontab's llm-restore link switch
sections. A hosted section (`llm-restore`) has no nav entry: one table in `shell.mjs` maps its id to the section that shows it, and its problems show on that section's dot. Cmd-K (Ctrl-K) focuses the header search over sections, settings fields, services,
cron and deferred jobs, metered SSIDs and consumers, and actions such as "restart
mac-panel". A result scrolls to and highlights its row; an action result navigates to the row, focuses
its button and idempotently arms it when confirmation is required. Search never clicks
an action control or runs the action; only the reader's confirming click can do that. Every section
loads at startup and the polled ones refresh whether or not their tab is open, so the dots
stay current; a section without auto-refresh reloads when it opens more than 15 seconds
after its last load. The open section and theme (system, light, dark) are kept in the
browser's `localStorage` only.

Charts are inline SVG drawn from the panel's own history: service health, the schedule of
cron and deferred runs, network metering and usage budgets. History charts have a 24 h /
7 d toggle and a tooltip (hover, or tap) with the exact value and time. Value series (the LLM
section's allowance and credits) are labelled line charts: a y axis with round gridlines, a
time axis on local hours (24 h) or days (7 d), a legend with each series' current value, a dot
and value at each line end, a dashed line for a reference series, and a grey "recording since"
band where the panel had not started sampling, so a short history is never stretched across
the window. State strips (service health, network metering) and schedule strips share the same
time axis row and a key to the band colours.

### History store

`sampler.py` runs a daemon thread that reads the existing adapters and appends samples to
`~/.local/state/mac-panel/history.sqlite` (directory 700, file 600, WAL): services,
metered, llm-restore and resources (system, per-kind CPU and memory, and the health level) every 60 s, crontab and deferred every 300 s, usage every 900 s.
Samples are pruned after 30 days. `GET /api/history?section=&since=&until=[&key=]` returns
`{series: {key: [{ts, level, value, text}]}, spacing: {key: seconds}}`, thinned to at most
5000 samples **per series**, behind the
same token guard as `/api/`. History records only while the panel runs, so gaps are
expected. `spacing` reports the median retained spacing for thinned series only; raw
sparse series do not redefine gaps as cadence. Service strips allow the greater of five
minutes or three times that spacing before showing a gap. Cron history stores only its
job id, schedule and executable basename, never command arguments.
`MAC_PANEL_STATE_DIR` relocates the store.

### Crontab actions

The Crontab section displays jobs from the whole tracked `macbook/crontab`,
including jobs paused with the exact `#off ` prefix. Pause and Resume submit the
revision that the page displayed, edit the tracked file atomically and install it
only when no live crontab lines would be lost; their confirmation names how many
repo-only lines that install also brings live. The llm-restore block has no row
actions because `llmrestore --cron off` and `llmrestore --cron on` own that switch;
its rows link to the llm-restore cards in the LLM section. The codex-release-cleanup block remains
editable here.

**Install macbook/crontab** appears only while the live crontab differs from the
tracked file and is disabled, with the reason, while live-only lines make
installation unsafe; the page lists those lines and shows
`macbook/crontab has uncommitted changes` until another session commits the edit.
The CLI exits 0 on success, 2 for usage, 75 for a revision conflict or busy lock,
3 for a refused unsafe/managed/unknown/ambiguous operation, and 1 for another
error. The adapter maps both 75 and 3 to HTTP 409; invalid request bodies are HTTP
400 and unexpected tool failures are HTTP 502. Run-now checks the current revision before
queuing (a stale request returns 409), and the CLI checks it again at execution time.

## Page

`static/` is served as ES modules with no build step; `server.py` serves
`index.html` at `/` and any `[a-z][a-z0-9_]*.mjs|.css` file that exists there.
No `style` attributes or inline `<style>`; dynamic geometry goes through CSS custom
properties set from JS.

- `app.mjs`: the section registry (id, renderer, auto-refresh, optional `searchItems`,
  `attentionItems`, `cardExtra`), polling, panel health and the Overview refresh.
- `shell.mjs`: header, grouped sidebar (a drawer below 900 px), section frames, hash
  routing (`goTo(id, anchor)`, `revealAnchor`), theme. `section_copy.mjs`: groups, titles,
  each section's info paragraph and the copy for the migrated sections. `search.mjs` (pure,
  Node-tested): index and scoring; `search_ui.mjs`: the header popover and Cmd-K.
- `panel_sections.mjs`: `refresh` (GET, keeps disclosure and focus state), `action` (POST,
  notice, then refresh), the dirty check polls obey, and the state registry
  `state[section] = {payload, summary, level, updatedAt, error}` that nav dots, Overview and
  search read (`onState` subscribes).
- `search_actions.mjs`: navigate to an action row and arm its confirmation without clicking it.
- `settings_fields.mjs`: setting labels and anchor ids.
- `settings_card.mjs`: `toolCard(tool, sectionId)`, one tool's settings form (Save, Reset, conflict Reload) for whichever section shows it.
- `task_receipt.mjs`: watch/resume async receipts, show outcomes and request edit-safe refreshes.
- `deferred_form.mjs`: pure deferred-add validation and request-body conversion.
- `section_deferred_add.mjs`: the deferred-job add form and inline errors.
- `deferred_agent_form.mjs`: pure Add agent job validation and request-body conversion
  (the same limits as `deferred_adapter.py`).
- `section_deferred_agent.mjs`: the Add agent job form; Runs on swaps the model control
  and shows the vanpi-only Pi prompt path and Run timeout rows.
- `deferred_agent_job.mjs`: an agent job's tags, Pi run state and the retry wording.
- `section_services_history.mjs`: cached service history, range controls and cadence-aware strips.
- `panel_api.mjs`: the only `fetch`; token header, JSON bodies, token renewal.
- `dom_core.mjs` (element factory, `armed`, idempotent `arm`, `button`), `components.mjs` (`info`, `toggle`,
  `settingRow`, `card`, `kv`, `emptyState`, `loadError`, `fieldError`, `tag`, `timeNode`,
  `anchor`) and `panel_dom.mjs` (tables, disclosures, notices, focus keys; re-exports the rest).
- `charts.mjs`: pure geometry (Node-tested) and the SVG renderers `stateStrip`, `sparkline`,
  `gauge`, `scheduleStrip`, `rangeToggle`. `line_chart.mjs`: `lineChart` and its pure layout;
  `chart_axes.mjs`: `timeAxis`, `stateLegend` and the tick drawing both share; `chart_scale.mjs`:
  pure scales, `niceTicks` and `timeTicks`; `chart_dom.mjs`: SVG/HTML factories and the tooltip. `history_panel.mjs`: a range-toggled block that
  fetches and briefly caches `/api/history` for a section's charts. `tokens.css` holds every
  colour; `style.css` and `charts.css` use only its variables.
- `relative_time.mjs`: pure time parsing and wording, tested under Node.
- `status_cell.mjs`: the `Level` vocabulary, status lines and `worst`.
- `section_restore.mjs` and `restore_snapshots.mjs`: the llm-restore section; its frame is moved into the LLM section's left column each time that section renders.
- `section_resources.mjs`: the Resources page (`render`, `searchItems`, `attentionItems`), built from `resources_system.mjs`
  (System card, health-to-`Level` table), `resources_groups.mjs` (sessions with trees, infrastructure, m4-compute),
  `resources_processes.mjs` (By app / By process, sort, filter; its view state lives in module state),
  `resources_history.mjs` (the `historyPanel` charts), `resources_names.mjs` (which `name_source` values count as unproven), `resources_actions.mjs` (request bodies, armed buttons, result
  line), `resources_table.mjs` (right-aligned number columns) and `resources_format.mjs` (bytes, percent, durations; pure).
- `section_<name>.mjs`: one renderer per section returning `{node, summary, level}`.

Run a second instance from a worktree with `MAC_PANEL_PORT=8770` (and, to keep its
history out of `~/.local/state/mac-panel/`, `MAC_PANEL_STATE_DIR=<scratch dir>`):
`~/.local/share/mac-panel/venv/bin/python macbook/mac_panel/server.py`. It uses that
worktree's own `tmp/mac-panel/`, and the Host allowlist follows the port. The installed
LaunchAgent never sets either variable. The instance runs the real tool CLIs, so use it to
look, not to click actions.

`tests/test_static_page.py` parses every module with Node and checks that each
import names a served file.

## Security

The server binds only `127.0.0.1:<port>` (8769 unless `MAC_PANEL_PORT` is set for a dev
instance). Host headers must be exactly `127.0.0.1:<port>` or `localhost:<port>`; every API
request requires the per-instance page token. Every POST additionally requires a matching
Origin, JSON content type, a body no larger than 64 KiB and, for armed actions,
`confirmed: true`. `/healthz` is token-free but still Host-checked. There are no CORS
headers; a restrictive CSP disallows framing, inline scripts, style attributes and inline
`<style>` blocks. Fixed styles use classes; JS sets only dynamic geometry via CSS custom
properties (CSSOM).

Child tools receive a minimal environment and timeouts. Logs and displayed configuration are
redacted, with all HTTP(S) URLs hidden because arbitrary ntfy topics can be secrets. Log
tails additionally hide MAC addresses and SSID/BSSID/network field values. The metered
service's tails are withheld because free-text network names cannot be reliably masked. The panel reads log sizes and paths through the rotator's CLI, never log contents. It never reads `pi/secrets/`, displays the Claude settings `env` block, invokes BTT
repair, or edits tool-owned files itself. The only root action is the restart of a
`com.jacobr.*` system daemon through a rule Jacob installs himself: `panel_service.py
sudoers --print` shows it and `sudoers --script` prints a zsh installer that checks it with
`visudo -cf` before installing `/etc/sudoers.d/mac-panel-daemons` (mode 440, root:wheel). It
allows only `launchctl kickstart -k system/com.jacobr.<name>`, and the panel never installs
it. The history store is private (700/600) and holds levels, counts and state words, never
log text or network names beyond what the sections already show. The Add agent job form's Ready condition is shell text the job runs unattended with
`/bin/zsh -c` on this Mac, the same trust the script form already extends to a pasted
script. The prompt is pasted text or an absolute path on the Pi; the form never accepts
a Mac file path, so the panel cannot be made to read or send a local file. Local processes running as
Jacob can access the page and token; this is a browser-origin boundary, not isolation from
other processes in the same account.

Resources signals only the panel user's own processes, with `os.kill` and `os.setpriority`
and no sudo. It never signals pid ≤ 1, the panel or any process above or below it, or the
protected programs: the system set (`launchd`, `loginwindow`, `WindowServer`, `Dock`,
`Finder`, `SystemUIServer`, `ControlCenter`, `NotificationCenter`, `coreaudiod`, `cfprefsd`,
`runningboardd`, `tccd`) and the terminal hosts (`iTerm2`, `Terminal`, `tmux`, `screen`,
`zellij`, `Ghostty`, `kitty`, `Alacritty`, `WezTerm`, `Warp`, `Hyper`, `mosh-server`, `sshd`,
`login` and their variants), matched by argv[0] and by the kernel's own (truncated) process
name. If the panel's own row is missing from a table, or the chain above it is broken,
nothing is signalled from that table. A process outside a session that has a terminal, or an
LLM session, gateway or compute root, beneath it is blocked too (`process_hosting.py`),
whatever its name; each such root counts on its own even when it shares the process's
terminal, so a supervisor of a session is blocked. Shells, known by argv[0] or kernel name,
can be signalled only as members of a session tree. Shared LLM infrastructure and
van-compute rows are read-only.

Roles that decide what may be signalled come from `process_roles.py`, which reads the
arguments the opposite way to the display parse: when unsure, it protects. Outside a
session, a process is the compute worker when its command text contains `van_compute.worker`,
and infrastructure when it contains `llm_switchboard`, `model-gateway.js`, `claude-code-proxy`,
`usage-server.mjs` or `@playwright/mcp` (or its kernel name is one of them): plain substring
matching with no option parsing, so no spelling of the options (`-Bmvan_compute.worker`)
hides one, and a command that merely mentions one is read-only as a result. Inside a
session, a process belongs to that session, with one exception: every Codex process roots a
group of its own wherever it runs, even inside another session, because `ps` flattens its
arguments and a prompt or an option value can look like a subcommand. Its role decides only
how that group is shown and whether it may be ended: a Codex session only when it is
certainly an interactive run (with or without a prompt), `exec` or `resume`; otherwise
read-only infrastructure: `Codex app server`, `Codex service` (`exec-server`,
`remote-control`), `Codex command` (any other subcommand), or `Codex (role unread)` when an
option value could have shifted the words, where nothing at all may be signalled. A session with any other
group's root (a session, a Codex process, infra or compute) beneath any of its processes has
no whole-tree action, and the reason names those groups.

Before each signal the panel re-reads the whole process table and decides again from it:
same pid, start time and uid; for a session tree, the root still the same session root,
every member one the plan saw (a process that joined the session since stops the action
before any of its ancestors is signalled), the target still a member with every process up
to the root present, no other group nested in it, and the policy still allowing the process. This
narrows the window in which a pid could be reused to milliseconds; it does not close it. A
tree over `TREE_TARGET_LIMIT` (48) processes has no Terminate session button and the request
is refused with the same reason (one function decides both). A mutation has a total time
budget (`MUTATION_BUDGET`, 8 s), checked again immediately before each signal or priority
change, so nothing is signalled after it. When an action ends before every target was
handled, the reply's `stopped` is `{code, text}` with code `time-limit`, `session-changed`,
`nested-session`, `unreadable` or `root-changed`, the targets it left alone are counted as
`not_reached`, and the page shows the reason after the counts.

Command lines never leave the backend. A display name is the program's base name plus a
script name or `-m module`, only when the arguments parse with confidence against that
interpreter's own flag table (`process_invocation.py`; an interpreter without one, such as
fish, shows the bare program), never an option or an argument value. argv[0] with
whitespace may be a title carrying arguments, so none of it is shown: the program is the
base name of the file the kernel says the process is running (`proc_pidpath`, in
`executable_name.py`; `Google Chrome Helper (Renderer)`), believed only when the kernel's
short name begins it, and otherwise the kernel's short name (16 characters). At most
`EXECUTABLE_LOOKUP_LIMIT` (256) lookups run per table read; the full path never leaves the
backend. Compute job rows are named `job · <program>`. A session's
`name_source` (`registry`, `rollout-time` or `fallback`) says how its name was established.
Only `registry`, Claude's own record for that exact process, proves a name. A Codex name is
never proven: nothing on disk ties a running Codex process to its conversation (a rollout it
has open may be an old thread it is reading), so it is at most the title of the one rollout
created near its start (`rollout-time`), else `codex · <folder>`; lsof is used only for
working folders. The page marks every name that is not `registry` as unconfirmed, and the
Terminate session confirmation then says "name unconfirmed" beside the pid and folder.

## Tests

The repository suite discovers tests in `macbook/mac_panel/tests/`:

```sh
.local/test-venv/bin/python run_tests.py
```

Its import registry supplies the flat panel modules and `shared/python`.
Tests use temporary configuration and fake tool outputs, never live launchd jobs,
crontabs, BTT state or Jacob's settings.
