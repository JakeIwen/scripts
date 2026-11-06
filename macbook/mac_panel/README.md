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
| Jobs & services, Deferred jobs (`#deferred`) | `/bin/zsh macbook/scripts/deferred.zsh list --json`; `cancel`, `retry`, `reschedule`, `run-now` and `add` stay owned by that CLI. Run-now returns an asynchronous task receipt shown in the row; receipts are bounded and reset when the panel restarts. The add form takes a name, a due time, a description and a script (must start with `#!`), plus optional `attempts` and `timeout`; the adapter writes it to a private temporary file for `deferred.zsh add` and deletes it. |
| Jobs & services, Crontab (`#crontab`) | `/usr/bin/python3 -B macbook/scripts/crontab_sync.py status --json` reads the tracked and live crontabs. Pause, resume, install and run-now use the same CLI with the displayed revision. Each job carries its next runs for the schedule chart. Log modification times are hints, not proof a job succeeded. |
| Jobs & services, Automations (`#automations`) | `shared/python/tool_automation.py` loads declarations from `shared/automations/`. Each status command prints one validated `status_record`; declared actions run through the tool's fixed argv and timeout, confirmed when declared. An action declared `async` (the Codex/Claude "Check for updates now") runs through a background task and the card shows its receipt. |
| Jobs & services, llm-restore (`#llm-restore`) | `/usr/bin/python3 -I -B shared/sh/llm_restore.py --cron --json`; on/off adds `on` or `off`. That CLI owns its crontab block. |
| Network, Metered (`#metered`) | `macbook/metered/mac-metered status --json` and its mutations: SSID add (offline) and remove (needs the Pi and its revision), sync, approve, revoke, mode, exempt and Low Data Mode. Pi consumers are approved on the Pi; the panel shows what the Pi reported. A history card plots the verdict, Low Data Mode and each consumer from the history store. |
| Mac, Amphetamine (`#amphetamine`) | `macbook/scripts/amphetamine_control.py status/configure`. The automatic-lock switch and the idle-minutes field each save through `configure` with the displayed revision. The battery cutoff is read-only (Amphetamine owns it). |
| Mac, BTT guard (`#guard`) | `/usr/bin/python3 -B macbook/bettertouchtool/btt.py guard`: `checkpoint --list`, `audit` (a fresh read-only audit on every load), `service status`, `acceptance` (shown as Auto-accept; drift pending acceptance is yellow), `inspect`. Inspect and audit are unconfirmed and read-only; the known-good checkpoint (`checkpoint --accept-current`, adding `--replace` only after a fresh active-checkpoint check) is armed and sends `confirmed: true`. Repair is Terminal-only. |
| Mac, ntfy alerts (`#ntfy`) | Reads only routing metadata from `~/.claude/settings.json` hooks and `~/.codex/config.toml` notify; destinations are never read or shown. The one action is a confirmed test through `guard notify-test --send`. |
| Mac, Logs (`#logs`) | `/usr/bin/python3 -B macbook/scripts/log_rotate.py inventory --json` (with `PYTHONPATH=shared/python` passed through `cli_runner`; no `-I`, which would hide the script's package) lists every log the Mac's jobs write (LaunchAgent plists, the tracked crontab, scanned state folders, added paths) with size, archives, growth per day, policy and state; the section adds the `com.jacobr.log-rotate` agent's launchd state. Confirmed `POST /api/logs {action: rotate, path?, force?, confirmed: true}` runs `rotate --json [--only PATH] [--force]`; `policy`, `add`, `ignore`, `unignore` and `adopt` pass the displayed revision to the same-named subcommands (exit 3 is a 409 conflict, exit 75 a 409 with code `busy`; each answers with the new revision and the touched entry, and the page then reloads). A rotation that had per-log errors exits 1 but still prints its result, so it is a 200 showing rotated and failed logs together; any other exit 1 is a 400. A row refused with `unrecorded archives; adopt to continue` offers an armed **Adopt archives** button; the confirmed request records only eligible existing archives and never edits their bytes. The needs-attention list is the rotator's `problems` plus the agent's launchd state; the page renders it as sent. A sparkline plots total live bytes from the history store. |
| Usage (`#usage`) | `/usr/bin/python3 -I -B /Users/jacobr/config/mac/usage-budget/usage_budget.py facts --json`, a read-only subcommand that reads the cached snapshot only. Gauges for the Claude, Fable and Codex windows and credits, the routing lean, workers, review and availability, and sparklines from the history store. No account identity is exposed. |
| Settings (`#settings`) | `shared/python/tool_settings.py` loads declarations from `shared/settings/` (plus `~/.config/jacobr/declarations/`) and revision-checked values from `~/.config/jacobr/`. Each field renders through one row with its description in an info icon, its unit and a "changed from default" marker; save checks the revision and a conflict offers Reload; reset is armed and sends `action: "reset"` with `confirmed: true`. A declaration that cannot be loaded becomes an error card on that tool alone. |

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
cancel and add, metered SSID removal and Low Data Mode, the BTT guard checkpoint, the ntfy
test, settings reset, log rotation and confirmed automation actions. Reschedule opens an inline editor
whose Apply is enabled only once the time differs.

Navigation: the sidebar groups Overview; Jobs & services (Services, Deferred jobs,
Crontab, Automations, llm-restore); Network (Metered); Mac (Amphetamine, BTT guard, ntfy
alerts, Logs); Usage; Settings. It is a drawer opened by Menu below 900 px. A nav entry shows its
state dot (green fine, yellow needs attention, red failing, grey idle, hollow unknown) and
its one-line summary as the hover tooltip; a group heading shows the worst of its members.
The arrow keys, Home and End move between entries. The shell opens the section named by the URL hash, else the last-viewed section from
`localStorage`, else `#overview`, so in-page links such as crontab's llm-restore link switch
sections. Cmd-K (Ctrl-K) focuses the header search over sections, settings fields, services,
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
7 d toggle and a tooltip (hover, or tap) with the exact value and time.

### History store

`sampler.py` runs a daemon thread that reads the existing adapters and appends samples to
`~/.local/state/mac-panel/history.sqlite` (directory 700, file 600, WAL): services,
metered and llm-restore every 60 s, crontab and deferred every 300 s, usage every 900 s.
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
its rows link to the llm-restore section. The codex-release-cleanup block remains
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
- `settings_fields.mjs`: declaration-driven setting inputs, values and validation.
- `task_receipt.mjs`: watch/resume async receipts, show outcomes and request edit-safe refreshes.
- `deferred_form.mjs`: pure deferred-add validation and request-body conversion.
- `section_deferred_add.mjs`: the deferred-job add form and inline errors.
- `section_services_history.mjs`: cached service history, range controls and cadence-aware strips.
- `panel_api.mjs`: the only `fetch`; token header, JSON bodies, token renewal.
- `dom_core.mjs` (element factory, `armed`, idempotent `arm`, `button`), `components.mjs` (`info`, `toggle`,
  `settingRow`, `card`, `kv`, `emptyState`, `loadError`, `fieldError`, `tag`, `timeNode`,
  `anchor`) and `panel_dom.mjs` (tables, disclosures, notices, focus keys; re-exports the rest).
- `charts.mjs`: pure geometry (Node-tested) and the SVG renderers `stateStrip`, `sparkline`,
  `gauge`, `scheduleStrip`, `rangeToggle`. `history_panel.mjs`: a range-toggled block that
  fetches and briefly caches `/api/history` for a section's charts. `tokens.css` holds every
  colour; `style.css` and `charts.css` use only its variables.
- `relative_time.mjs`: pure time parsing and wording, tested under Node.
- `status_cell.mjs`: the `Level` vocabulary, status lines and `worst`.
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
log text or network names beyond what the sections already show. Local processes running as
Jacob can access the page and token; this is a browser-origin boundary, not isolation from
other processes in the same account.

## Tests

The repository suite discovers tests in `macbook/mac_panel/tests/`:

```sh
.local/test-venv/bin/python run_tests.py
```

Its import registry supplies the flat panel modules and `shared/python`.
Tests use temporary configuration and fake tool outputs, never live launchd jobs,
crontabs, BTT state or Jacob's settings.
